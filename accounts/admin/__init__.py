"""The admin dashboard at /admin: people, money, holds, requests, billers, budget, audit, exports, backups.

English, server-rendered HTML with inline CSS (no JavaScript, no external assets), mobile first. Every page
says that the money is a mock ledger: balances are IQD inside this service's database, not connected to a
payment network, and bills do not reach billers.

Security:
- HTTP Basic: ADMIN_USER (default karar) and ADMIN_PASSWORD (or ADMIN_PASSWORD_HASH, a PBKDF2 hash). Without a
  password every /admin path is 404. 10 failed logins from one address in 10 minutes -> 429 for 15 minutes.
- CSRF: browsers resend Basic credentials by themselves, so every POST needs an Origin (or Referer) of this
  host and a form token (HMAC of the day and the path, valid today and yesterday).
- Strict headers (no-store, no framing, a CSP without scripts); plain-http requests behind the proxy are refused.
- Money actions go through wallet.api.Wallet (idempotent by a form op_id: a resubmitted form moves nothing);
  everything else through accounts.service. Every action writes one audit row (actor admin:<user>@<ip hash>)
  with the reason. Exports never contain a PIN hash; CSV cells that start like a formula are neutralised.
- Identity checks (kyc/): the person page shows the level and the record that is kept (document type, printed
  name, the last 4 characters of the number, birth year, time, reason code). There is no image and no whole number
  to show: neither is stored. Checks that wait for a person are on the requests page, with approve and reject
  (a reason is required, the action is audited, and the person is told inside their window).
"""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import html
import io
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from accounts import backup, phones
from accounts.admin.style import CSS
from accounts.service import Accounts, clean_name
from payments.messages import format_iqd
from wallet.api import Wallet
from wallet.db import BAGHDAD, from_iso, utc_now, verify_pin

BANNER = ("Mock ledger: balances are IQD inside this service's database. Not connected to ZainCash or any payment "
          "network; bills are not sent to billers.")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    # same-origin: a browser then sends the page's real Origin on the admin's own form POSTs (no-referrer made it
    # «null», and every action was refused). Nothing is sent to other sites.
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'",
}
NAV = [("/admin", "Overview"), ("/admin/users", "People"), ("/admin/users/new", "Add person"),
       ("/admin/transactions", "Transactions"), ("/admin/holds", "Holds"), ("/admin/requests", "Requests"),
       ("/admin/billers", "Billers"), ("/admin/budget", "Budget"), ("/admin/audit", "Audit"),
       ("/admin/backups", "Backups")]
EXPORTS = ("users", "transactions", "ledger", "holds", "audit")
MAX_FAILS, FAIL_WINDOW, LOCKOUT = 10, 600, 900
GLOBAL_MAX_FAILS = 30  # failed logins from all addresses together in FAIL_WINDOW, before everyone waits LOCKOUT
MAX_TRACKED = 5000  # addresses kept in the in-memory fail table


@dataclass
class AdminContext:
    accounts: Accounts
    wallet: Wallet
    db_path: Path
    user: str = "karar"
    password: str = field(default="", repr=False)
    password_hash: str = field(default="", repr=False)
    store: Any = None  # the WhatsApp channel's store: counters, spend, sign-ups in progress
    daily_budget_usd: float = 0.0
    meta_price_usd: float = 0.0
    clock: Callable[[], datetime] = utc_now
    secret: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)
    fails: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    locked: dict[str, float] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.password or self.password_hash)


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(n: Any) -> str:
    try:
        return format_iqd(int(n))
    except (TypeError, ValueError):
        return ""


def when(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return from_iso(iso).astimezone(BAGHDAD).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(iso)


def pill(value: str | None) -> str:
    return f'<span class="pill {e(value)}">{e(value)}</span>'


def csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def make_router(ctx: AdminContext) -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------ guard

    def client_ip(request: Request) -> str:
        """The address the nearest proxy saw: the right-most X-Forwarded-For entry (the proxy appends it; the
        left-most ones are written by the client and can be anything), else the socket's peer."""
        forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
        return forwarded[-1] if forwarded else (request.client.host if request.client else "?")

    def prune(now: float) -> None:
        for key in [k for k, v in ctx.fails.items() if not v or now - v[-1] >= FAIL_WINDOW]:
            ctx.fails.pop(key, None)
        for key in [k for k, until in ctx.locked.items() if until <= now and k != "*"]:
            ctx.locked.pop(key, None)
        while len(ctx.fails) > MAX_TRACKED:
            ctx.fails.pop(next(iter(ctx.fails)))

    def actor(request: Request) -> str:
        ip = hmac.new(ctx.secret, client_ip(request).encode(), hashlib.sha256).hexdigest()[:8]
        return f"admin:{ctx.user}@{ip}"

    def check(request: Request) -> None:
        if not ctx.enabled:
            raise HTTPException(404)
        if request.headers.get("x-forwarded-proto", "").lower() == "http":
            raise HTTPException(403, "https only")
        ip, now = client_ip(request), time.monotonic()
        if ctx.locked.get(ip, 0) > now or ctx.locked.get("*", 0) > now:
            raise HTTPException(429, "too many failed logins", headers={"Retry-After": str(LOCKOUT)})
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.lower().startswith("basic "):
            try:
                user, _, password = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                user, password = "", ""
            user_ok = hmac.compare_digest(user.encode(), ctx.user.encode())
            if ctx.password_hash:
                pass_ok = verify_pin(password, ctx.password_hash)
            else:
                pass_ok = hmac.compare_digest(password.encode(), ctx.password.encode())
            ok = user_ok and pass_ok
        if not ok:
            if auth:
                prune(now)
                fails = [t for t in ctx.fails[ip] if now - t < FAIL_WINDOW] + [now]
                ctx.fails[ip] = fails
                if len(fails) >= MAX_FAILS:
                    ctx.locked[ip] = now + LOCKOUT
                every = [t for t in ctx.fails["*"] if now - t < FAIL_WINDOW] + [now]  # whatever the address says
                ctx.fails["*"] = every
                if len(every) >= GLOBAL_MAX_FAILS:
                    ctx.locked["*"] = now + LOCKOUT
            raise HTTPException(401, "login needed", headers={"WWW-Authenticate": 'Basic realm="billpay admin"'})
        ctx.fails.pop(ip, None)

    def token(path: str, day: str | None = None) -> str:
        day = day or ctx.clock().astimezone(BAGHDAD).strftime("%Y-%m-%d")
        return hmac.new(ctx.secret, f"csrf|{day}|{path}".encode(), hashlib.sha256).hexdigest()

    def csrf_ok(request: Request, form: dict[str, str]) -> bool:
        host = request.headers.get("host", "")
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        if origin == "null":  # an opaque origin: only the browser's own same-origin mark (never set by a page) passes
            if request.headers.get("sec-fetch-site", "").lower() != "same-origin":
                return False
        elif not origin or urlsplit(origin).netloc != host:
            return False
        today = ctx.clock().astimezone(BAGHDAD)
        given = form.get("csrf", "")
        return any(hmac.compare_digest(given, token(request.url.path, (today - timedelta(days=d)).strftime("%Y-%m-%d")))
                   for d in (0, 1))

    async def posted(request: Request) -> dict[str, str]:
        check(request)
        raw = (await request.body()).decode("utf-8", errors="replace")
        form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        if not csrf_ok(request, form):
            raise HTTPException(403, "bad form token or origin")
        return form

    def hidden(path: str) -> str:
        return f'<input type="hidden" name="csrf" value="{token(path)}">'

    def page(title: str, body: str, active: str = "", flash: str = "", bad: bool = False) -> HTMLResponse:
        nav = "".join(f'<a href="{href}" class="{"on" if href == active else ""}">{e(name)}</a>' for href, name in NAV)
        note = f'<div class="flash {"bad" if bad else ""}">{e(flash)}</div>' if flash else ""
        doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
               f'<meta name="viewport" content="width=device-width, initial-scale=1">'
               f'<title>{e(title)} · Bill-pay admin</title><style>{CSS}</style></head><body>'
               f'<header><b>Bill-pay admin</b><nav>{nav}</nav></header>'
               f'<div class="banner">{e(BANNER)}</div><main><h1>{e(title)}</h1>{note}{body}</main></body></html>')
        return HTMLResponse(doc, headers=HEADERS)

    def back(path: str, msg: str, bad: bool = False) -> RedirectResponse:
        query = urlencode({"msg": msg, **({"bad": "1"} if bad else {})})
        return RedirectResponse(f"{path}?{query}", status_code=303, headers=HEADERS)

    def flash_of(request: Request) -> tuple[str, bool]:
        return request.query_params.get("msg", "")[:200], request.query_params.get("bad") == "1"

    def table(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset(), *,
              pairs: bool = False) -> str:
        """A table; on a phone each row becomes a card labelled by its headers. pairs=True is a key-value table:
        its first column is the label, so the cards carry no header names."""
        head = "".join(f'<th class="{"n" if i in numeric else ""}">{e(h)}</th>' for i, h in enumerate(headers))
        label = (lambda i: "") if pairs else (lambda i: e(headers[i]))
        body = "".join("<tr>" + "".join(f'<td data-label="{label(i)}" class="{"n" if i in numeric else ""}'
                                         f'{" k" if pairs and i == 0 else ""}">'
                                         f"{cell}</td>" for i, cell in enumerate(row)) + "</tr>" for row in rows)
        if not rows:
            body = f'<tr><td colspan="{len(headers)}" class="muted">Nothing yet.</td></tr>'
        return f'<table class="rt{" kv" if pairs else ""}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    def user_link(uid: str | None, label: str | None = None) -> str:
        return f'<a href="/admin/users/{e(uid)}">{e(label or uid)}</a>' if uid else ""

    def intval(form: dict, name: str, default: int | None = None) -> int | None:
        try:
            return int(str(form.get(name, "")).replace(",", "").strip())
        except ValueError:
            return default

    # ------------------------------------------------------------ overview

    @router.get("/admin")
    def overview(request: Request) -> HTMLResponse:
        check(request)
        s = ctx.accounts.overview()
        audit = ctx.wallet.audit()
        day = ctx.clock().astimezone(BAGHDAD).strftime("%Y-%m-%d")
        spent = ctx.store.spent(day) if ctx.store is not None else 0.0
        counters = {c["name"]: c["n"] for c in (ctx.store.counters(day) if ctx.store is not None else [])
                    if c["day"] == day}
        checks = sum(1 for v in audit["checks"].values() if v)
        tiles = [
            ("Active accounts", s.get("active")), ("Invited", s.get("invited")), ("Frozen", s.get("frozen")),
            ("Sign-ups today / 7 days", f"{s.get('signups_today')} / {s.get('signups_week')}"),
            ("Open holds", f"{s.get('holds_open')} · {money(s.get('holds_iqd'))} IQD"),
            ("Payments today", f"{s.get('txns_today')} · {money(s.get('volume_today'))} IQD"),
            ("Mock fees today", f"{money(s.get('fees_today'))} IQD"), ("Balances held", f"{money(s.get('balances'))} IQD"),
            # demo accounts' opening money is booked on 'demo_funds', never 'cash': it is not cash in
            ("Cash in (admin net)", f"{money(audit['totals'].get('cash'))} IQD"),
            ("Demo accounts", f'<a href="/admin/users?source=demo">{e(s.get("demo_accounts") or 0)}</a> · '
                              f'{e(money(s.get("demo_balances")))} IQD pretend'),
            ("Ledger audit", f'<span class="{"ok" if audit["ok"] else "bad"}">{checks}/{len(audit["checks"])} hold</span>'),
            ("Core spend today", f"${spent:.2f} of ${ctx.daily_budget_usd:.2f}"),
            ("WhatsApp in / out today", f"{counters.get('msgs_in', 0)} / "
                                        f"{sum(v for k, v in counters.items() if k.startswith('msgs_out'))}"),
            ("Open requests", f'<a href="/admin/requests">{s.get("requests")}</a>'), ("Blocked PINs", s.get("pins_blocked")),
            ("Accounts / schema", f"{ctx.accounts.count_users()} / v{ctx.accounts.schema_version()}"),
        ]
        grid = "".join(f'<div class="tile"><div class="k">{e(k)}</div><div class="v">'
                       f'{v if isinstance(v, str) and v.startswith("<") else e(v)}</div></div>' for k, v in tiles)
        bad = "".join(f"<li>{e(v)}</li>" for v in audit["violations"][:20])
        body = f'<div class="grid">{grid}</div>' + (f'<h2>Audit violations</h2><ul class="bad">{bad}</ul>' if bad else "")
        body += _sparkline(ctx)
        return page("Overview", body, "/admin")

    # ------------------------------------------------------------ people

    @router.get("/admin/users")
    def users(request: Request) -> HTMLResponse:
        check(request)
        q, status = request.query_params.get("q", ""), request.query_params.get("status", "")
        source = request.query_params.get("source", "")
        source = source if source in ("demo", "real") else ""
        try:
            offset = max(0, int(request.query_params.get("offset", "0")))
        except ValueError:
            offset = 0
        rows = ctx.accounts.search_users(q, status, limit=50, offset=offset, source=source)
        options = "".join(f'<option value="{v}" {"selected" if v == status else ""}>{v or "any status"}</option>'
                          for v in ("", "active", "invited", "frozen", "closed"))
        kinds = "".join(f'<option value="{v}" {"selected" if v == source else ""}>{label}</option>'
                        for v, label in (("", "all accounts"), ("real", "real only"), ("demo", "demo only")))
        form = (f'<form class="box" method="get" action="/admin/users"><label>Search (number, name, id)'
                f'<input name="q" value="{e(q)}"></label><label>Status<select name="status">{options}</select></label>'
                f'<label>Kind<select name="source">{kinds}</select></label><button>Search</button></form>')

        def number(u: dict) -> str:  # a username account (no number in WhatsApp) has a key, not a number
            return "username" if phones.is_user_key(u.get("phone_e164")) else phones.display(u.get("phone_e164"))

        voiced = ctx.wallet.voice_users([u["id"] for u in rows])  # a voice kept for voice approval
        out = [[user_link(u["id"], u["name"]) + (" " + pill("demo") if u.get("source") == "demo" else "")
                + (" " + pill("voice") if u["id"] in voiced else "")
                + ' <span class="muted" dir="auto"></span>',
                e(number(u)), pill(u["status"]), pill(u["pin_state"]), money(u["balance"]),
                e(when(u.get("last_inbound_at"))), e(when(u.get("created_at")))] for u in rows]
        more = f'<p><a href="/admin/users?{urlencode({"q": q, "status": status, "source": source, "offset": offset + 50})}">' \
            'Next 50</a></p>' if len(rows) == 50 else ""
        return page("People", form + table(["Name", "Number", "Status", "PIN", "Balance", "Last seen", "Created"],
                                           out, {4}) + more, "/admin/users", *flash_of(request))

    @router.get("/admin/users/new")
    def new_user_form(request: Request) -> HTMLResponse:
        check(request)
        path = "/admin/users/new"
        body = (f'<form class="box" method="post" action="{path}">{hidden(path)}'
                f'<input type="hidden" name="op_id" value="{secrets.token_hex(8)}">'
                '<label>Phone<input name="phone" required placeholder="0770 123 4567"></label>'
                '<label>Name<input name="name" required dir="auto"></label>'
                '<label>Language<select name="language"><option value="iq">Iraqi</option><option value="msa">MSA'
                '</option><option value="en">English</option></select></label>'
                '<label>Gender<select name="gender"><option value="">ask them</option><option value="m">male</option>'
                '<option value="f">female</option></select></label>'
                '<label>Opening balance (IQD)<input name="opening" value="0" inputmode="numeric"></label>'
                '<label>Daily limit (IQD)<input name="limit" value="1000000" inputmode="numeric"></label>'
                '<label>Note<input name="note"></label><button>Add person</button></form>'
                '<p class="muted">They become an invited account: at their first WhatsApp message they only set a PIN.</p>')
        return page("Add person", body, path, *flash_of(request))

    @router.post("/admin/users/new")
    async def new_user(request: Request) -> Response:
        form = await posted(request)
        e164 = phones.canonical(form.get("phone"))
        name = clean_name(form.get("name", ""))
        if e164 is None or name is None:
            return back("/admin/users/new", "A valid Iraqi mobile number and a name (letters only) are needed.", True)
        existing = ctx.accounts.by_phone(e164)
        if existing:
            return back(f"/admin/users/{existing['id']}", "That number already has an account.")
        gender = form.get("gender") if form.get("gender") in ("m", "f") else None
        language = form.get("language") if form.get("language") in ("iq", "msa", "en") else "iq"
        opening = max(0, intval(form, "opening", 0) or 0)
        limit = intval(form, "limit", 1_000_000)
        result = ctx.wallet.invite(e164, name, language=language, gender=gender, opening=opening,
                                   daily_limit=limit if limit is not None else 1_000_000, note=form.get("note") or None,
                                   op_id=form.get("op_id") or None, actor=actor(request))
        return back(f"/admin/users/{result['user_id']}", "Person added. Ask them to message the service number.")

    @router.get("/admin/users/{uid}")
    def user_page(uid: str, request: Request) -> HTMLResponse:
        check(request)
        user = ctx.accounts.get(uid)
        if user is None:
            raise HTTPException(404)
        p = ctx.wallet.user_profile(uid)
        voice_samples = ctx.wallet.voiceprint_samples(uid)
        base = f"/admin/users/{uid}"

        def action(name: str, label: str, extra: str = "", ghost: bool = False) -> str:
            path = f"{base}/{name}"
            return (f'<form class="box" method="post" action="{path}">{hidden(path)}{extra}'
                    f'<label>Reason<input name="reason" required minlength="3" maxlength="200"></label>'
                    f'<button class="{"ghost" if ghost else ""}">{e(label)}</button></form>')

        op = f'<input type="hidden" name="op_id" value="{secrets.token_hex(8)}">'
        amount = '<label>Amount (IQD)<input name="amount" required inputmode="numeric"></label>'
        notify = '<label>Notify<select name="notify"><option value="1">yes</option><option value="0">no</option></select></label>'
        info = table(["Field", "Value"], [
            ["Name", f'<span dir="auto">{e(user["name"])}</span>'], ["Number", e(phones.display(user.get("phone_e164")))],
            ["Status", pill(user["status"]) + (f' <span class="muted">{e(user.get("frozen_reason"))}</span>'
                                               if user.get("frozen_reason") else "")],
            ["PIN", pill(user["pin_state"]) + (" (reset " + e(when(user.get("pin_reset_at"))) + ")"
                                               if user.get("pin_reset_at") else "")],
            ["Balance", f"{money(p['balance'])} IQD"],
            ["Today", f"spent {money(p['spent_today'])} of {money(p['daily_limit'])} (left {money(p['daily_remaining'])})"],
            ["Language / gender", f"{e(user.get('language'))} / {e(user.get('gender') or '?')}"],
            ["Signed up", e(when(user.get("created_at")))], ["Last message", e(when(user.get("last_inbound_at")))],
            ["WhatsApp profile name", f'<span dir="auto">{e(user.get("wa_profile_name"))}</span>'],
            ["Source", e(user.get("source"))], ["Note", e(user.get("note"))],
            ["Voice", (pill("voice") + f" {voice_samples} sample(s), payments up to the voice cap need no PIN"
                       if voice_samples else '<span class="muted">none kept</span>')]], pairs=True)
        info += kyc_box(uid, user)
        profile_path = f"{base}/profile"
        edits = (f'<form class="box" method="post" action="{profile_path}">{hidden(profile_path)}'
                 f'<label>Name<input name="name" value="{e(user["name"])}" dir="auto"></label>'
                 f'<label>Note<input name="note" value="{e(user.get("note") or "")}"></label><button class="ghost">Save</button></form>')
        limit_path = f"{base}/limit"
        edits += (f'<form class="box" method="post" action="{limit_path}">{hidden(limit_path)}'
                  f'<label>Daily limit (IQD)<input name="limit" value="{e(user.get("daily_limit"))}" inputmode="numeric"></label>'
                  f'<button class="ghost">Set limit</button></form>')
        money_forms = action("credit", "Credit…", op + amount + notify) + action("debit", "Debit…", op + amount + notify, True)
        status_forms = (action("unfreeze", "Unfreeze") if user["status"] == "frozen" else action("freeze", "Freeze", ghost=True))
        status_forms += action("pin/reset", "Reset PIN", ghost=True)
        if voice_samples:
            status_forms += action("voice/clear", "Clear voice (PIN for every payment)", ghost=True)
        if user["pin_state"] == "blocked":
            status_forms += action("pin/unblock", "Unblock PIN (no reset)", ghost=True)
        if user["status"] != "closed":
            status_forms += action("close", "Close account (balance 0 only)", ghost=True)
        else:
            status_forms += action("release-number", "Release the number", ghost=True)
        contacts = ctx.accounts.contacts(uid)
        crow = [[f'<span dir="auto">{e(c["display_name"])}</span>', e(phones.display(c.get("phone_e164")) or c["phone"]),
                 user_link(c.get("account_id"), "yes") if c.get("account_id") else "no", e(c.get("source")),
                 f'<span dir="auto">{e(c.get("alias"))}</span>'] for c in contacts]
        bills_html = ""
        for a in ctx.accounts.bill_accounts(uid):
            rows = []
            for b in ctx.accounts.bills(a["id"]):
                edit = ""
                if b["status"] == "unpaid":
                    path = f"{base}/bills/{b['id']}"
                    edit = (f'<form class="inline" method="post" action="{path}">{hidden(path)}'
                            f'<input name="amount" value="{b["amount_due"]}" size="8"><button class="ghost">Set</button></form>')
                rows.append([e(b["period"]), money(b["amount_due"]), pill(b["status"]), e(b.get("source")), edit])
            issue_path = f"{base}/bills/issue"
            issue = (f'<form class="box" method="post" action="{issue_path}">{hidden(issue_path)}'
                     f'<input type="hidden" name="account_id" value="{e(a["id"])}">'
                     f'<label>Period (YYYY-MM)<input name="period" required pattern="\\d{{4}}-\\d{{2}}"></label>'
                     f'<label>Amount<input name="amount" required inputmode="numeric"></label>'
                     f'<button class="ghost">Issue bill</button></form>')
            bills_html += (f'<h2 dir="auto">{e(a.get("label_ar"))} · {e(a.get("biller_name_en") or a.get("biller_name_ar"))}'
                           f' · {e(a["account_no"])}</h2>' + table(["Period", "Amount", "Status", "Source", ""], rows, {1})
                           + issue)
        txns = ctx.accounts.history(uid, limit=50)
        trow = [[e(when(t["at"])), e(t["kind"]), e(t["direction"]), f'<span dir="auto">{e(t["name"] or t["biller_ar"] or "")}</span>'
                 + (f" …{e(t['last4'])}" if t.get("last4") else ""), money(t["amount"]), money(t["fee"]), pill(t["status"]),
                 e(t.get("reason") or ""), e(t["id"])] for t in txns]
        hrow = [[e(h["id"]), e(phones.display(h["phone_e164"])), money(h["amount"]), pill(h["state"]),
                 e(when(h["expires_at"]))] for h in ctx.wallet.holds(user_id=uid, limit=50)]
        waiting = [[e(h["id"]), e(phones.display(h["phone_e164"])), money(h["amount"]), pill(h["state"])]
                   for h in ctx.wallet.holds(state="HELD", limit=500) if h["phone_e164"] == user.get("phone_e164")]
        arow = [[e(when(a["at"])), e(a["actor"]), e(a["action"]), e(a.get("ref") or ""), e(a["details_json"])]
                for a in ctx.accounts.audit_rows(user_id=uid, limit=100)]
        body = (f'<div class="cols"><div>{info}{edits}</div><div><h2>Money</h2>{money_forms}<h2>Account</h2>'
                f'{status_forms}</div></div>'
                f'<h2>Contacts ({len(contacts)})</h2>' + table(["Name", "Number", "Has account", "Source", "Alias"], crow)
                + f"<h2>Bill accounts</h2>{bills_html or '<p class=muted>None.</p>'}"
                + "<h2>Transactions (last 50)</h2>" + table(["Time", "Kind", "Dir", "Who", "Amount", "Fee", "Status",
                                                             "Reason", "Ref"], trow, {4, 5})
                + "<h2>Holds sent</h2>" + table(["Hold", "To", "Amount", "State", "Expires"], hrow, {2})
                + "<h2>Held for this number</h2>" + table(["Hold", "Number", "Amount", "State"], waiting, {2})
                + "<h2>Audit (last 100)</h2>" + table(["Time", "Actor", "Action", "Ref", "Details"], arow))
        return page(user["name"], body, "/admin/users", *flash_of(request))

    def kyc_forms(uid: str, inline: bool = False, approvable: bool = True) -> str:
        """Approve / reject for a check that waits for a person. Each form has its own token and asks for a reason.
        Where no document was read, or the code read an age under 18, there is only Reject (the person is asked for
        another photo): a click never verifies what nobody could look at."""
        out = ""
        choices = (("approve", "Approve", False), ("reject", "Reject", True)) if approvable else (("reject", "Reject", True),)
        for name, label, ghost in choices:
            path = f"/admin/users/{uid}/kyc/{name}"
            hint = "reason" if name == "approve" else "reason (the person reads it)"
            if inline:
                out += (f'<form class="inline" method="post" action="{path}">{hidden(path)}'
                        f'<input name="reason" required minlength="3" maxlength="200" placeholder="{hint}" size="14">'
                        f'<button class="{"ghost" if ghost else ""}">{label}</button></form>')
            else:
                out += (f'<form class="box" method="post" action="{path}">{hidden(path)}'
                        f'<label>{hint.capitalize()}<input name="reason" required minlength="3" maxlength="200"></label>'
                        f'<button class="{"ghost" if ghost else ""}">{label} identity check</button></form>')
        return out

    def kyc_box(uid: str, user: dict) -> str:
        """The identity check of one account: its level, its limits and the record that is kept. Never an image
        and never a whole number: neither exists here."""
        kyc = ctx.accounts.kyc(uid)
        if kyc is None:
            return ('<h2>Identity check</h2><p class="muted">No level: an account from before the identity check, '
                    'or one the admin added. Its limits are the ones set above.</p>')
        same = ctx.accounts.kyc_same_document(user.get("kyc_number_fingerprint"), uid=uid)
        match_name = ctx.accounts.kyc_match_name(uid)
        others = ", ".join(user_link(o["user_id"]) if o["user_id"] else e(phones.display(o["phone"]) + " (signing up)")
                           for o in same)
        rows = [
            ["Level", pill(kyc["level"])],
            ["Name matched against", f'<span dir="auto">{e(match_name)}</span>'
                                     ' <span class="muted">(typed at sign-up, or set by an admin)</span>'],
            ["Limits of this level", f"{money(kyc['daily_limit'])} IQD a day · {money(kyc['single_max'])} IQD in one payment"],
            ["Document", e(kyc.get("document_type") or "none read")],
            ["Name on the document", f'<span dir="auto">{e(kyc.get("name"))}</span>'],
            ["Number", e("••••" + kyc["number_last4"]) if kyc.get("number_last4") else ""],
            ["Birth year", e(kyc.get("birth_year"))],
            ["Checked", e(when(kyc.get("checked_at")))],
            ["Reason", e(kyc.get("reason"))],
        ]
        if (user.get("name") or "") != match_name:  # the person renamed the account since: shown, never matched
            rows.insert(2, ["Current name (renamed by the person)", f'<span dir="auto">{e(user.get("name"))}</span>'])
        if others:
            rows.append(["Same document as", others])
        box = "<h2>Identity check</h2>" + table(["Field", "Value"], rows, pairs=True)
        box += '<p class="muted">The photo is not kept, and only the last 4 characters of the number are.</p>'
        if kyc["level"] == "pending":
            approvable = ctx.accounts.kyc_approvable(user)
            if not approvable:
                box += ('<p class="muted">Approve is not offered: no document was read, the document read shows '
                        'an age under 18, or the back of the card could not be tied to its front. Reject with a '
                        'reason; the person may send another photo.</p>')
            box += kyc_forms(uid, approvable=approvable)
        if ctx.accounts.kyc_under_age_hold(user):
            box += ('<p class="muted">The document read under 18, so the account is frozen: payments stop, money '
                    'sent to it still arrives. Rejecting keeps it frozen; only Unfreeze above lets it pay again '
                    'and send another document.</p>')
        return box

    @router.post("/admin/users/{uid}/kyc/approve")
    async def kyc_approve(uid: str, request: Request) -> Response:
        def run(a: str, r: str) -> None:
            if not ctx.accounts.kyc_decide(uid, True, actor=a, reason=r):
                waiting = (ctx.accounts.get(uid) or {}).get("kyc_level") == "pending"
                raise HTTPException(409, {"error": "not_approvable" if waiting else "not_waiting_for_review"})
        return await _status_action(uid, request, run, "Identity check approved: the account is verified, with "
                                                       "the limits of that level. The person is told.")

    @router.post("/admin/users/{uid}/kyc/reject")
    async def kyc_reject(uid: str, request: Request) -> Response:
        def run(a: str, r: str) -> None:
            if not ctx.accounts.kyc_decide(uid, False, actor=a, reason=r):
                raise HTTPException(409, {"error": "not_waiting_for_review"})
        return await _status_action(uid, request, run, "Identity check rejected: the small limits stay (an under-18 "
                                                       "account stays frozen). The person is told, with the reason.")

    @router.post("/admin/users/{uid}/credit")
    async def credit(uid: str, request: Request) -> Response:
        return await _money(uid, request, "credit")

    @router.post("/admin/users/{uid}/debit")
    async def debit(uid: str, request: Request) -> Response:
        return await _money(uid, request, "debit")

    async def _money(uid: str, request: Request, kind: str) -> Response:
        form = await posted(request)
        base = f"/admin/users/{uid}"
        user = ctx.accounts.get(uid)
        if user is None:
            raise HTTPException(404)
        amount, reason = intval(form, "amount"), (form.get("reason") or "").strip()
        if not amount or amount <= 0 or not 3 <= len(reason) <= 200:
            return back(base, "An amount above 0 and a reason (3 to 200 characters) are needed.", True)
        if kind == "debit" and amount > user["balance"]:
            return back(base, "A debit cannot take the balance below 0.", True)
        if form.get("confirm") != "1":  # the second step: show the effect, then a second click
            path = f"{base}/{kind}"
            after = user["balance"] + (amount if kind == "credit" else -amount)
            keep = "".join(f'<input type="hidden" name="{k}" value="{e(form.get(k, ""))}">'
                           for k in ("amount", "reason", "op_id", "notify"))
            body = (f'<p>{e(kind.title())} <b>{money(amount)} IQD</b> for <span dir="auto">{e(user["name"])}</span>: '
                    f'balance {money(user["balance"])} → <b>{money(after)}</b>. Reason: {e(reason)}</p>'
                    f'<form class="box" method="post" action="{path}">{hidden(path)}{keep}'
                    f'<input type="hidden" name="confirm" value="1"><button>Confirm {e(kind)}</button>'
                    f'<a href="{base}">Back</a></form>')
            return page(f"Confirm {kind}", body, "/admin/users")
        op_id = form.get("op_id") or ""
        method = ctx.wallet.admin_credit if kind == "credit" else ctx.wallet.admin_debit
        try:
            result = method(uid, amount, reason, op_id=op_id, actor=actor(request), notify=form.get("notify") != "0")
        except HTTPException as exc:
            code = exc.detail.get("error") if isinstance(exc.detail, dict) else str(exc.detail)
            return back(base, f"Refused: {code}.", True)
        note = "Already done (this form was sent before): nothing moved again." if result["replayed"] else \
            f"Done: {kind} {money(amount)} IQD, balance now {money(result['balance'])} IQD."
        return back(base, note)

    def _reason(form: dict) -> str | None:
        reason = (form.get("reason") or "").strip()
        return reason if 3 <= len(reason) <= 200 else None

    async def _status_action(uid: str, request: Request, run: Callable[[str, str], None], done: str) -> Response:
        form = await posted(request)
        base = f"/admin/users/{uid}"
        if ctx.accounts.get(uid) is None:
            raise HTTPException(404)
        reason = _reason(form)
        if reason is None:
            return back(base, "A reason (3 to 200 characters) is needed.", True)
        try:
            run(actor(request), reason)
        except HTTPException as exc:
            code = exc.detail.get("error") if isinstance(exc.detail, dict) else str(exc.detail)
            return back(base, f"Refused: {code}.", True)
        return back(base, done)

    @router.post("/admin/users/{uid}/freeze")
    async def freeze(uid: str, request: Request) -> Response:
        return await _status_action(uid, request, lambda a, r: ctx.wallet.set_status(uid, "frozen", actor=a, reason=r),
                                    "Frozen: payments stop, incoming money still arrives.")

    @router.post("/admin/users/{uid}/unfreeze")
    async def unfreeze(uid: str, request: Request) -> Response:
        return await _status_action(uid, request, lambda a, r: ctx.wallet.set_status(uid, "active", actor=a, reason=r),
                                    "Unfrozen.")

    @router.post("/admin/users/{uid}/close")
    async def close(uid: str, request: Request) -> Response:
        return await _status_action(uid, request, lambda a, r: ctx.wallet.set_status(uid, "closed", actor=a, reason=r),
                                    "Closed.")

    @router.post("/admin/users/{uid}/release-number")
    async def release(uid: str, request: Request) -> Response:
        def run(a: str, r: str) -> None:
            if not ctx.accounts.release_number(uid, a, r):
                raise HTTPException(409, {"error": "not_closed"})
        return await _status_action(uid, request, run, "Number released: it can sign up again.")

    @router.post("/admin/users/{uid}/pin/reset")
    async def pin_reset(uid: str, request: Request) -> Response:
        return await _status_action(uid, request, lambda a, r: ctx.wallet.reset_pin(uid, actor=a, reason=r),
                                    "PIN reset: they set a new one at their next message; 24 h transfer cap applies.")

    @router.post("/admin/users/{uid}/pin/unblock")
    async def pin_unblock(uid: str, request: Request) -> Response:
        return await _status_action(uid, request, lambda a, r: ctx.wallet.unblock_pin(uid, actor=a, reason=r),
                                    "PIN unblocked (no reset).")

    @router.post("/admin/users/{uid}/voice/clear")
    async def voice_clear(uid: str, request: Request) -> Response:
        return await _status_action(uid, request, lambda a, r: ctx.wallet.delete_voice(uid, actor=a, reason=r),
                                    "Voice cleared: every payment needs the PIN until a new voice is kept.")

    @router.post("/admin/users/{uid}/limit")
    async def set_limit(uid: str, request: Request) -> Response:
        form = await posted(request)
        limit = intval(form, "limit")
        try:
            if limit is None:
                raise HTTPException(400, {"error": "bad_limit"})
            ctx.wallet.set_limit(uid, limit, actor=actor(request))
        except HTTPException:
            return back(f"/admin/users/{uid}", "The limit must be 0 to 10,000,000 IQD.", True)
        return back(f"/admin/users/{uid}", f"Daily limit set to {money(limit)} IQD.")

    @router.post("/admin/users/{uid}/profile")
    async def set_profile(uid: str, request: Request) -> Response:
        form = await posted(request)
        name = clean_name(form.get("name", "")) if form.get("name") else None
        if form.get("name") and name is None:
            return back(f"/admin/users/{uid}", "A name is letters only (2 to 40).", True)
        ctx.accounts.update_profile(uid, name=name, note=(form.get("note") or "")[:300], actor=actor(request))
        return back(f"/admin/users/{uid}", "Saved.")

    @router.post("/admin/users/{uid}/bills/issue")
    async def issue_bill(uid: str, request: Request) -> Response:
        form = await posted(request)
        amount, period = intval(form, "amount"), form.get("period", "")
        owned = {a["id"] for a in ctx.accounts.bill_accounts(uid)}
        if form.get("account_id") not in owned or not amount or amount <= 0 or len(period) != 7:
            return back(f"/admin/users/{uid}", "An account, a period (YYYY-MM) and an amount are needed.", True)
        ok = ctx.accounts.issue_bill(form["account_id"], period, amount, actor(request))
        return back(f"/admin/users/{uid}", "Bill issued." if ok else "That month already has a bill.", not ok)

    @router.post("/admin/users/{uid}/bills/{bill_id}")
    async def edit_bill(uid: str, bill_id: str, request: Request) -> Response:
        form = await posted(request)
        amount = intval(form, "amount")
        ok = bool(amount) and ctx.accounts.set_bill(bill_id, amount, actor(request))
        return back(f"/admin/users/{uid}", "Bill updated." if ok else "Only an unpaid bill can change.", not ok)

    # ------------------------------------------------------------ money views

    @router.get("/admin/transactions")
    def transactions(request: Request) -> HTMLResponse:
        check(request)
        q = request.query_params
        rows = ctx.accounts.transactions_feed(kind=q.get("kind", ""), status=q.get("status", ""), q=q.get("q", ""),
                                              since=q.get("from", ""), until=q.get("to", ""), limit=100)
        form = ('<form class="box" method="get" action="/admin/transactions">'
                + "".join(f'<label>{label}<input name="{name}" value="{e(q.get(name, ""))}"></label>'
                          for name, label in (("kind", "Kind"), ("status", "Status"), ("q", "Id / number"),
                                              ("from", "From (ISO)"), ("to", "To (ISO)")))
                + '<button>Filter</button></form><p><a href="/admin/export/transactions.csv">Export CSV</a></p>')
        out = [[e(when(t["created_at"])), e(t["kind"]), user_link(t["user_id"], t.get("user_name")),
                user_link(t.get("payee_user_id"), t.get("payee_name")) or e(phones.display(t.get("counterparty_phone"))),
                money(t["amount"]), money(t["fee"]), pill(t["status"]), e(t["id"]), e(t.get("quote_id") or "")]
               for t in rows]
        return page("Transactions", form + table(["Time", "Kind", "From", "To", "Amount", "Fee", "Status", "Ref", "Quote"],
                                                 out, {4, 5}), "/admin/transactions")

    @router.get("/admin/holds")
    def holds(request: Request) -> HTMLResponse:
        check(request)
        out = []
        for h in ctx.wallet.holds(limit=300):
            cancel = ""
            if h["state"] == "HELD":
                path = f"/admin/holds/{h['id']}/cancel"
                cancel = (f'<form class="inline" method="post" action="{path}">{hidden(path)}'
                          f'<input name="reason" required minlength="3" placeholder="reason" size="12">'
                          f'<button class="ghost">Refund</button></form>')
            out.append([e(h["id"]), user_link(h["sender_id"]), e(phones.display(h["phone_e164"])), money(h["amount"]),
                        money(h["fee"]), pill(h["state"]), e(when(h["created_at"])), e(when(h["expires_at"])),
                        e(h.get("settle_reason") or ""), cancel])
        return page("Holds", table(["Hold", "Sender", "For number", "Amount", "Fee", "State", "Created", "Expires",
                                    "Settled", ""], out, {3, 4}), "/admin/holds", *flash_of(request))

    @router.post("/admin/holds/{hold_id}/cancel")
    async def cancel_hold(hold_id: str, request: Request) -> Response:
        form = await posted(request)
        reason = _reason(form)
        if reason is None:
            return back("/admin/holds", "A reason is needed.", True)
        try:
            done = ctx.wallet.refund_hold(hold_id, reason=f"admin_cancel: {reason}", actor=actor(request))
        except HTTPException:
            raise HTTPException(404)
        return back("/admin/holds", "Refunded to the sender, fee included." if done else "Already settled.", not done)

    @router.get("/admin/requests")
    def requests_page(request: Request) -> HTMLResponse:
        check(request)
        reqs = []
        for r in ctx.accounts.open_requests():
            what = e(r["kind"])
            if r["kind"] == "kyc_review":  # an identity check that waits for a person: decide it here
                what += f' <span class="muted">{e(r.get("reason"))} · {e(r.get("document_type") or "no document read")}</span>'
            reqs.append([what, user_link(r["user_id"], r.get("name")), e(phones.display(r.get("phone"))),
                         e(when(r.get("at"))), kyc_forms(r["user_id"], inline=True, approvable=r.get("approvable", True))
                         if r["kind"] == "kyc_review" else ""])
        stuck = []
        if ctx.store is not None:
            for row in ctx.store.onboarding_rows():
                started = datetime.fromtimestamp(float(row["started_at"]), BAGHDAD).strftime("%Y-%m-%d %H:%M")
                stuck.append([e(phones.display(row["phone_e164"])), e(row["step"]), e(row["lang"]), e(started)])
        body = (table(["Request", "Person", "Number", "Since", ""], reqs)
                + "<h2>Sign-ups in progress</h2>" + table(["Number", "Step", "Language", "Started"], stuck))
        return page("Requests", body, "/admin/requests", *flash_of(request))

    @router.get("/admin/billers")
    def billers(request: Request) -> HTMLResponse:
        check(request)
        out = []
        for b in ctx.accounts.billers(active_only=False):
            path = f"/admin/billers/{b['id']}"
            edit = (f'<form class="inline" method="post" action="{path}">{hidden(path)}'
                    f'<input name="mock_min" value="{e(b.get("mock_min") or "")}" size="7">'
                    f'<input name="mock_max" value="{e(b.get("mock_max") or "")}" size="7">'
                    f'<select name="active"><option value="1" {"selected" if b.get("active") else ""}>active</option>'
                    f'<option value="0" {"" if b.get("active") else "selected"}>off</option></select>'
                    f'<button class="ghost">Save</button></form>')
            out.append([e(b["id"]), e(b["kind"]), f'<span dir="auto">{e(b["name_ar"])}</span>', e(b.get("name_en")),
                        e(b.get("account_pattern") or ""), edit])
        path = "/admin/billers"
        add = (f'<h2>Add a biller</h2><form class="box" method="post" action="{path}">{hidden(path)}'
               '<label>Id<input name="id" required pattern="[a-z0-9_]{2,30}"></label>'
               '<label>Kind<select name="kind"><option>electricity</option><option>generator</option>'
               '<option>internet</option><option>water</option></select></label>'
               '<label>Arabic name<input name="name_ar" required dir="auto"></label>'
               '<label>English name<input name="name_en" required></label>'
               '<label>Mock min<input name="mock_min" value="25000"></label>'
               '<label>Mock max<input name="mock_max" value="90000"></label><button>Add</button></form>')
        return page("Billers", table(["Id", "Kind", "Arabic", "English", "Account pattern", "Mock range / state"], out)
                    + add, "/admin/billers", *flash_of(request))

    @router.post("/admin/billers")
    async def add_biller(request: Request) -> Response:
        form = await posted(request)
        bid, kind = form.get("id", "").strip(), form.get("kind", "")
        low, high = intval(form, "mock_min"), intval(form, "mock_max")
        if not bid or kind not in ("electricity", "generator", "internet", "water") or not low or not high or low > high:
            return back("/admin/billers", "Id, kind and a mock range (min ≤ max) are needed.", True)
        ok = ctx.accounts.add_biller(bid, kind, form.get("name_ar", "").strip(), form.get("name_en", "").strip(),
                                     low, high, actor(request))
        return back("/admin/billers", "Added." if ok else "That id exists.", not ok)

    @router.post("/admin/billers/{biller_id}")
    async def edit_biller(biller_id: str, request: Request) -> Response:
        form = await posted(request)
        low, high = intval(form, "mock_min"), intval(form, "mock_max")
        ctx.accounts.set_biller(biller_id, active=form.get("active") == "1", mock_min=low, mock_max=high,
                                actor=actor(request))
        return back("/admin/billers", "Saved.")

    @router.get("/admin/budget")
    def budget(request: Request) -> HTMLResponse:
        check(request)
        since = (ctx.clock().astimezone(BAGHDAD) - timedelta(days=30)).strftime("%Y-%m-%d")
        spend = {r["day"]: r["usd"] for r in (ctx.store.spend_days(since) if ctx.store is not None else [])}
        counts: dict[str, dict[str, int]] = defaultdict(dict)
        for c in (ctx.store.counters(since) if ctx.store is not None else []):
            counts[c["day"]][c["name"]] = c["n"]
        out = []
        for day in sorted(set(spend) | set(counts), reverse=True):
            c = counts.get(day, {})
            outbound = sum(v for k, v in c.items() if k.startswith("msgs_out"))
            meta = f"${outbound * ctx.meta_price_usd:.2f} (estimate)" if ctx.meta_price_usd else ""
            out.append([e(day), f"${spend.get(day, 0.0):.3f}", str(c.get("msgs_in", 0)), str(c.get("msgs_out_text", 0)),
                        str(c.get("msgs_out_interactive", 0)), str(c.get("msgs_out_audio", 0)), e(meta),
                        str(c.get("signups", 0)), str(c.get("rate_limited", 0))])
        body = (f'<p>Daily budget of the conversational core: ${ctx.daily_budget_usd:.2f}.</p>'
                + table(["Day", "Core spend", "In", "Out text", "Out cards", "Out voice", "Meta (est.)", "Sign-ups",
                         "Rate-limited"], out, {1}))
        return page("Budget", body, "/admin/budget")

    @router.get("/admin/audit")
    def audit(request: Request) -> HTMLResponse:
        check(request)
        q = request.query_params
        report = ctx.wallet.audit()
        checks = table(["Invariant", "Holds"], [[e(k), f'<span class="{"ok" if v else "bad"}">{"yes" if v else "NO"}</span>']
                                                 for k, v in report["checks"].items()])
        totals = table(["Ledger total", "IQD"], [[e(k), money(v)] for k, v in report["totals"].items()], {1})
        form = ('<form class="box" method="get" action="/admin/audit">'
                + "".join(f'<label>{label}<input name="{name}" value="{e(q.get(name, ""))}"></label>'
                          for name, label in (("action", "Action"), ("actor", "Actor"), ("user", "User id")))
                + '<button>Filter</button></form>')
        rows = [[e(when(a["at"])), e(a["actor"]), e(a["action"]), user_link(a.get("user_id")), e(a.get("ref") or ""),
                 e(a["details_json"])] for a in ctx.accounts.audit_rows(q.get("action", ""), q.get("actor", ""),
                                                                         q.get("user", ""), limit=300)]
        bad = "".join(f"<li>{e(v)}</li>" for v in report["violations"][:50])
        body = (f'<div class="cols"><div>{checks}</div><div>{totals}</div></div>'
                + (f'<ul class="bad">{bad}</ul>' if bad else "")
                + "<h2>Log</h2>" + form + table(["Time", "Actor", "Action", "User", "Ref", "Details"], rows)
                + '<p><a href="/admin/export/audit.csv">Export CSV</a></p>')
        return page("Audit", body, "/admin/audit")

    # ------------------------------------------------------------ exports and backups

    @router.get("/admin/export/{name}.csv")
    def export(name: str, request: Request) -> Response:
        check(request)
        if name not in EXPORTS:
            raise HTTPException(404)
        since, until = request.query_params.get("from", ""), request.query_params.get("to", "")
        if name == "users":
            rows = ctx.accounts.search_users(limit=100_000)
            cols = ["id", "name", "phone_e164", "status", "pin_state", "balance", "daily_limit", "language", "gender",
                    "source", "created_at", "activated_at", "last_inbound_at", "note", "kyc_level", "kyc_reason",
                    "kyc_checked_at"]  # never pin_hash, and nothing of a document
        elif name == "transactions":
            rows = ctx.accounts.transactions_feed(since=since, until=until, limit=1_000_000)
            cols = ["id", "created_at", "kind", "status", "user_id", "user_phone", "payee_user_id", "counterparty_phone",
                    "amount", "fee", "quote_id", "hold_id", "reason"]
        elif name == "ledger":
            rows = ctx.accounts.ledger(since, until)
            cols = ["id", "created_at", "quote_id", "account", "delta", "entry"]
        elif name == "holds":
            rows = ctx.wallet.holds(limit=1_000_000)
            cols = ["id", "quote_id", "sender_id", "phone_e164", "amount", "fee", "state", "created_at", "expires_at",
                    "settled_at", "claimed_by", "settle_reason"]
        else:
            rows = ctx.accounts.audit_rows(limit=1_000_000)
            cols = ["id", "at", "actor", "action", "user_id", "ref", "details_json"]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(cols)
        for row in rows:
            writer.writerow([csv_cell(row.get(c)) for c in cols])
        ctx.accounts.log(actor(request), "export", None, name, {"rows": len(rows)})
        headers = {**HEADERS, "Content-Disposition": f'attachment; filename="{name}.csv"'}
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)

    @router.get("/admin/backups")
    def backups(request: Request) -> HTMLResponse:
        check(request)
        path = "/admin/backups/now"
        rows = [[e(b["name"]), f"{b['size'] / 1024:.0f} KB", e(b["modified"])] for b in backup.listing(ctx.db_path)]
        body = (f'<form class="box" method="post" action="{path}">{hidden(path)}<button>Back up now</button></form>'
                + table(["File", "Size", "Written (Baghdad)"], rows)
                + '<p class="muted">Backups sit on the same volume as the database (they hold PIN hashes, so they are '
                  'never downloadable here). Railway volume backups cover losing the volume.</p>')
        return page("Backups", body, "/admin/backups", *flash_of(request))

    @router.post("/admin/backups/now")
    async def backup_now(request: Request) -> Response:
        await posted(request)
        written = backup.now(ctx.db_path, ctx.clock())
        ctx.accounts.log(actor(request), "backup", None, written.name if written else None, {"ok": bool(written)})
        return back("/admin/backups", f"Written: {written.name}" if written else "The backup failed its check.",
                    written is None)

    return router


def _sparkline(ctx: AdminContext) -> str:
    """Core spend over the last 7 days as a tiny inline SVG."""
    if ctx.store is None:
        return ""
    days = [(ctx.clock().astimezone(BAGHDAD) - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(6, -1, -1)]
    spend = {r["day"]: r["usd"] for r in ctx.store.spend_days(days[0])}
    values = [spend.get(d, 0.0) for d in days]
    if not any(values):
        return '<h2>Core spend, last 7 days</h2><p class="muted">No spend in the last 7 days.</p>'
    top = max(values + [ctx.daily_budget_usd or 0.01])
    bars = "".join(f'<rect x="{i * 22}" y="{40 - 40 * v / top:.1f}" width="16" height="{40 * v / top:.1f}" '
                   f'fill="currentColor"><title>{d}: ${v:.2f}</title></rect>'
                   f'<text x="{i * 22 + 8}" y="52" text-anchor="middle" font-size="7" fill="var(--s500)">{d[-2:]}</text>'
                   for i, (d, v) in enumerate(zip(days, values)))
    base = '<line x1="0" y1="40.5" x2="154" y2="40.5" stroke="var(--s300)" stroke-width="1"/>'
    peak = max(values)
    label = ", ".join(f"{d}: ${v:.2f}" for d, v in zip(days, values))
    return (f'<h2>Core spend, last 7 days</h2><p class="muted">Highest day ${peak:.2f} · total ${sum(values):.2f}'
            f' · budget ${ctx.daily_budget_usd:.2f}/day</p><svg viewBox="0 0 154 56" width="308" height="112" '
            f'style="color:var(--clay);max-width:100%" role="img" aria-label="core spend per day: {e(label)}">'
            f'{base}{bars}</svg>')


__all__ = ["AdminContext", "make_router", "BANNER", "csv_cell"]


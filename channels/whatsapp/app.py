"""HTTP side of the WhatsApp channel: the webhook (Meta's Cloud API, or YCloud with WHATSAPP_PROVIDER=ycloud) and a
health check.

    uvicorn channels.whatsapp.app:create_app --factory --port 8090

GET  /webhook   Meta's verification handshake (hub.mode, hub.verify_token, hub.challenge)
POST /webhook   messages, checked with X-Hub-Signature-256 (with YCloud: YCloud-Signature, and only that); answered
                with 200 at once, handled in the background, one sender at a time
GET  /healthz   200 when the channel is fully configured, 503 with the names of what is missing
GET  /instagram/webhook   Instagram's handshake (channels/instagram/graph.challenge)
POST /instagram/webhook   Instagram direct messages, checked with X-Hub-Signature-256 and INSTAGRAM_APP_SECRET (403
                otherwise), answered with 200 at once and handled like WhatsApp messages: deduplicated on the message
                id, one sender at a time, the same agent and the same accounts. Mounted only when INSTAGRAM_TOKEN,
                INSTAGRAM_APP_SECRET, INSTAGRAM_VERIFY_TOKEN and INSTAGRAM_ACCOUNT_ID are all set; otherwise both
                are 404 and one log line at start says what is missing (names, never values).
/admin          the admin dashboard (accounts/admin), only when ADMIN_PASSWORD (or ADMIN_PASSWORD_HASH) is set;
                otherwise every /admin path is 404. The wallet's own HTTP routes are never mounted here.

Background jobs (one thread, every JOB_SECONDS): expire holds past their 7 days (refund, fee included),
deliver waiting notices to people inside their WhatsApp window, and the daily backup of the accounts
database (the first tick after BACKUP_HOUR, Baghdad time).

The identity document step of sign-up (SIGNUP_KYC, kyc/) reads photos through the model layer's "vision" role
(llm/, configured in the environment). When that role has no usable route, or KYC_PEPPER is missing, the step is
off and one log line at start says so.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from accounts import backup
from accounts import voiceprint as voiceprints
from accounts.admin import AdminContext, make_router
from channels.instagram import graph as ig_graph
from channels.instagram.config import Settings as InstagramSettings
from channels.instagram.wiring import Instagram
from channels.whatsapp import ycloud
from channels.whatsapp.channel import Channel
from channels.whatsapp.config import Settings
from channels.whatsapp.graph import Graph, GraphClient, parse, signature_ok
from wallet.db import pin_pepper

log = logging.getLogger("whatsapp.app")

MAX_BODY = 1_000_000
JOB_SECONDS = 600


def _jobs(channel: Channel, stop: threading.Event) -> None:
    while not stop.wait(JOB_SECONDS):
        run_jobs(channel)


def run_jobs(channel: Channel) -> dict:
    """One round of the background jobs (also called by tests). Never raises."""
    done: dict = {}
    try:
        done.update(channel.tick())
    except Exception:
        log.exception("background job failed (holds / notices)")
    try:
        written = backup.daily(channel.settings.accounts_path, channel.clock())
        done["backup"] = bool(written)
        if written:
            channel.accounts.log("system", "backup", None, written.name)
    except Exception:
        log.exception("daily backup failed")
    return done


def _kyc_reader(settings: Settings) -> tuple[Any, Any]:
    """(reader, model layer) for the identity document step, from the "vision" role as the environment configures
    it. (None, None) when SIGNUP_KYC is off, the configuration cannot be read, or no route of that role is usable:
    the channel then runs with the step off."""
    if settings.signup_kyc == "off":
        return None, None
    import kyc
    from llm import LLM, LLMError, Registry

    try:
        llm = LLM(Registry.from_env())
    except LLMError as exc:  # its message names the variable, never a value
        log.warning("the model layer's configuration could not be read (%s): no document reader", exc)
        return None, None
    if not kyc.can_read(llm):
        llm.close()
        return None, None
    return kyc.reader_from(llm), llm


def _client(settings: Settings) -> Graph:
    if settings.is_ycloud:
        return ycloud.YCloudClient(settings.ycloud_api_key, settings.ycloud_from)
    return GraphClient(settings.token, settings.phone_number_id, settings.graph_version)


def _instagram(settings: InstagramSettings, client: Any) -> Instagram | None:
    """The Instagram door, when its settings are complete. One log line either way: names only."""
    if not settings.ready:
        given = len(settings.missing) < 4 or settings.api_base != InstagramSettings().api_base
        if given:
            log.warning("Instagram is off: %s missing (/instagram/webhook is 404)", ", ".join(settings.missing))
        else:
            log.info("Instagram is off: no INSTAGRAM_* settings (/instagram/webhook is 404)")
        return None
    log.info("Instagram is on: /instagram/webhook")
    return Instagram.from_settings(settings, client)


def create_app(settings: Settings | None = None, *, graph: Graph | None = None, core: Any = None,
               seed: dict | None = None, clock: Callable[[], datetime] | None = None,
               kyc_reader: Callable[[bytes, str], Any] | None = None, kyc_llm: Any = None,
               instagram: InstagramSettings | None = None, instagram_client: Any = None,
               voiceprint: Any = None) -> FastAPI:
    """Build the channel. Tests pass a fake `graph`, a small `seed` and a `clock` they control, and a fake
    `kyc_reader` when they want the identity document step. A real run (no `graph` given) builds the reader from
    the model layer's vision role; a run with a fake `graph` never reads the model layer's environment.

    Instagram: a real run reads INSTAGRAM_* from the environment. A run with a fake `graph` has no Instagram unless
    it passes `instagram` settings (and a fake `instagram_client`).

    Voice approval: with VOICE_APPROVAL, a real run loads the speaker model (VOICEPRINT_MODEL); a run with a fake
    `graph` has none unless it passes `voiceprint` (a fake embedder), so tests never load a model."""
    settings = settings or Settings.from_env()
    own_graph = graph is None
    if graph is None:
        graph = _client(settings)
    own_llm = None
    if kyc_reader is None and own_graph:
        kyc_reader, own_llm = _kyc_reader(settings)
        kyc_llm = kyc_llm or own_llm
    if instagram is None:
        instagram = InstagramSettings.from_env() if own_graph else InstagramSettings()
    ig = _instagram(instagram, instagram_client)
    own_ig_client = ig is not None and instagram_client is None
    if voiceprint is None and own_graph and settings.voice_approval:
        voiceprint = voiceprints.load(settings.voiceprint_model)
    channel = Channel(settings, graph, core=core, seed=seed, clock=clock, kyc_reader=kyc_reader, kyc_llm=kyc_llm,
                      instagram=ig, voiceprint=voiceprint)
    if settings.unsigned_allowed:
        log.warning("WHATSAPP_ALLOW_UNSIGNED is on: webhooks are not checked. Local testing only.")

    if settings.is_ycloud and not (settings.sender_salt or settings.app_secret):
        log.warning("WHATSAPP_SENDER_SALT is empty: open PIN steps and sign-ups from before the switch are forgotten.")

    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        channel.replay()
        worker = threading.Thread(target=_jobs, args=(channel, stop), name="billpay-jobs", daemon=True)
        worker.start()
        yield
        stop.set()
        channel.close()
        if own_graph:
            graph.close()
        if own_llm is not None:
            own_llm.close()
        if own_ig_client:
            ig.client.close()

    app = FastAPI(title="Bill-pay WhatsApp channel", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.channel = channel
    if settings.admin_enabled:
        secret = hashlib.sha256(("admin|" + settings.sender_secret + "|" + (settings.admin_password or
                                                                           settings.admin_password_hash)).encode()).digest()
        app.state.admin = AdminContext(
            accounts=channel.accounts, wallet=channel.wallet, db_path=settings.accounts_path, user=settings.admin_user,
            password=settings.admin_password, password_hash=settings.admin_password_hash, store=channel.store,
            daily_budget_usd=settings.daily_budget_usd, meta_price_usd=settings.meta_price_usd,
            clock=channel.clock, secret=secret)
        app.include_router(make_router(app.state.admin))

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        missing = list(settings.missing)
        if settings.credential and not pin_pepper():  # without it every PIN hash is unpeppered
            missing.append("ACCOUNTS_PIN_PEPPER")
        if settings.signup_kyc != "off" and channel.kyc_reader is not None and not settings.kyc_pepper_ok:
            missing.append("KYC_PEPPER")  # a reader is configured, but no document can be fingerprinted
        # Public and unauthenticated: only whether the service is configured. Account counts and the schema
        # version are on the /admin overview.
        body = {"ok": not missing, "missing": missing, "voice": channel.voice}
        if channel.instagram is not None:  # a flood of wrong link codes: guessers wait, a right code still links
            body["instagram_linking_paused"] = channel.instagram.linking_paused()
        return JSONResponse(body, status_code=200 if not missing else 503)

    @app.get("/webhook")
    def verify(request: Request) -> PlainTextResponse:
        q = request.query_params
        if (q.get("hub.mode") == "subscribe" and settings.verify_token
                and secrets.compare_digest(q.get("hub.verify_token", "").encode("utf-8", "replace"),
                                           settings.verify_token.encode("utf-8", "replace"))):
            return PlainTextResponse(q.get("hub.challenge", ""))
        raise HTTPException(403, "verification failed")

    @app.post("/webhook")
    async def webhook(request: Request) -> dict:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY:
            raise HTTPException(413, "too large")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "too large")
        if settings.is_ycloud:
            signed = ycloud.signature_ok(raw, request.headers.get("YCloud-Signature"), settings.ycloud_webhook_secret,
                                         now=channel.clock().timestamp())
        else:
            signed = signature_ok(raw, request.headers.get("X-Hub-Signature-256"), settings.app_secret)
        if not (settings.unsigned_allowed or signed):
            log.warning("rejected a webhook with a bad or missing signature")
            raise HTTPException(403, "bad signature")
        try:
            body = json.loads(raw)
        except ValueError:
            return {"ok": True}
        if isinstance(body, dict):
            items = (ycloud.parse(body, settings.ycloud_from, settings.service_number) if settings.is_ycloud
                     else parse(body, settings.phone_number_id))
            channel.accept(items, door="whatsapp")  # quick: dedup + queue; a sender reading «ig:…» is dropped
        return {"ok": True}

    if ig is not None:
        _mount_instagram(app, channel, ig)
    return app


def _mount_instagram(app: FastAPI, channel: Channel, ig: Instagram) -> None:
    """The Instagram webhook. Never unsigned: WHATSAPP_ALLOW_UNSIGNED does not reach this door."""

    @app.get("/instagram/webhook")
    def ig_verify(request: Request) -> PlainTextResponse:
        q = request.query_params
        value = ig_graph.challenge(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"),
                                   ig.settings.verify_token)
        if value is None:
            raise HTTPException(403, "verification failed")
        return PlainTextResponse(value)

    @app.post("/instagram/webhook")
    async def ig_webhook(request: Request) -> dict:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY:
            raise HTTPException(413, "too large")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "too large")
        if not ig_graph.signature_ok(raw, request.headers.get("X-Hub-Signature-256"), ig.settings.app_secret):
            log.warning("rejected an Instagram webhook with a bad or missing signature")
            raise HTTPException(403, "bad signature")
        try:
            body = json.loads(raw)
        except ValueError:
            return {"ok": True}
        channel.accept(ig.inbound(body), door="instagram")  # quick: dedup + queue; the route names the door
        return {"ok": True}

"""Terminal demo: chat with the agent as one synthetic user, offline.

    python -m agent.demo --user u01                        # fresh temp wallet from data/wallet_seed.json
    python -m agent.demo --user u01 --url http://127.0.0.1:8000   # a running `uvicorn wallet.api:app`

Type Iraqi Arabic (or Arabizi) as the user would. Card buttons are typed as commands:
    /pay [pin]   tap "إي، ادفع" (asks for the PIN if you leave it out)
    /change      tap "غيّر"
    /cancel      tap "لا، بطّلت"
    /balance     show the wallet balance
    /fault MODE  break the wallet on purpose (none, drop_after_commit, timeout_before_commit,
                 error_500, biller_down, biller_decline_after_debit), fires once
    /check       re-check payments whose outcome was unknown
    /help, /quit
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from agent.session import Reply, Session
from agent.wallet_client import WalletClient, WalletError, WalletUnavailable

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / "data" / "wallet_seed.json"
BUTTONS = {"/pay": "confirm", "/change": "change", "/cancel": "cancel"}


def _show(reply: Reply) -> None:
    print()
    for line in reply.render().splitlines():
        print(f"  {line}")
    if reply.card is not None:
        print("  (/pay  /change  /cancel)")
    print()


def _pick_user(seed: dict, wanted: str) -> dict:
    for user in seed["users"]:
        if user["id"].lower() == wanted.lower():
            return user
    ids = ", ".join(u["id"] for u in seed["users"])
    raise SystemExit(f"unknown user {wanted!r}; the seed has: {ids}")


def chat(session: Session, wallet: WalletClient, user: dict) -> None:
    uid = user["id"]
    open_quote: str | None = None
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        command, _, arg = text.partition(" ")
        if command in ("/quit", "/exit"):
            return
        if command == "/help":
            print(__doc__)
            continue
        if command == "/balance":
            print(f"  balance: {wallet.user(uid)['balance']:,} IQD\n")
            continue
        if command == "/fault":
            try:
                print(f"  fault: {wallet.set_fault(arg.strip() or 'none', times=1 if arg.strip() not in ('', 'none') else None)}\n")
            except (WalletError, WalletUnavailable) as exc:
                print(f"  fault not set: {exc}\n")
            continue
        if command == "/check":
            _show(session.reconcile(uid))
            continue
        if command in BUTTONS:
            if open_quote is None:
                print("  (no card is open)\n")
                continue
            pin = arg.strip() or None
            if BUTTONS[command] == "confirm" and pin is None:
                pin = input("  PIN: ").strip()
            reply = session.handle_button(uid, open_quote, BUTTONS[command], pin=pin)
        else:
            reply = session.handle_text(uid, text)
        open_quote = reply.quote_id if reply.card is not None else None
        _show(reply)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chat with the bill-pay agent in the terminal.")
    parser.add_argument("--user", default="u01", help="seed user id, e.g. u01")
    parser.add_argument("--url", help="use a running wallet API at this URL instead of a temp one")
    parser.add_argument("--seed", type=Path, default=SEED_PATH, help="wallet seed JSON")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Arabic on a Windows console

    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    user = _pick_user(seed, args.user)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        app = None
        if args.url:
            wallet = WalletClient.over_http(args.url)
        else:
            from wallet.api import create_app

            app = create_app(str(Path(tmp) / "demo.db"), seed=seed)
            wallet = WalletClient.in_process(app)
        session = Session(wallet)
        profile = wallet.user(user["id"])
        print(f"Bill-pay agent demo (offline, synthetic data). You are {profile['name']} ({user['id']}), "
              f"balance {profile['balance']:,} IQD, demo PIN {user['pin']}.")
        print("Try: ادفع المولدة | دز لأحمد خمسين | اشحن زين بخمسة | شكد رصيدي   (/help for commands)\n")
        chat(session, wallet, user)
        wallet.http.close()
        if app is not None:
            app.state.wallet.db.close()  # release the temp database before its folder is removed
    return 0


if __name__ == "__main__":
    sys.exit(main())

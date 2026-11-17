"""Voice approval inside the wallet: the channel is not the only gate.

Wallet.grant_voice compares a voice note's speaker vector with the account's kept voice and records a one-time grant
bound to one quote (its amount and payee). execute(factor="voice") pays with no PIN only on that unconsumed grant, with
every rule still holding (the feature on, the cap, a known payee, no PIN lock, the voice still kept); it consumes the
grant and writes a voice_approved audit row with the score. Anything else is 403 voice_not_approved: nothing moved,
nothing counted. A voice is kept only from a quote paid with the PIN. Offline: an in-memory wallet, made-up vectors."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from accounts import billing, pins
from accounts.voiceprint import Voiceprint
from wallet.api import create_app
from wallet.db import Database
from wallet.migrations import LATEST

VISITOR = "9647811112222"
PIN = "4719"
MODEL = "fake-campplus"
OWNER = Voiceprint((1.0, 0.0, 0.0), 3.0, MODEL)
OWNER_AGAIN = Voiceprint((0.96, 0.28, 0.0), 2.4, MODEL)
STRANGER = Voiceprint((0.1, 0.0, 0.995), 3.0, MODEL)


@pytest.fixture
def world(tmp_path):
    db = Database(str(tmp_path / "accounts.db"))
    billing.ensure_catalogue(db)
    app = create_app(db)
    w = app.state.wallet
    w.configure_voice(enabled=True, threshold=0.70, max_iqd=100_000)
    uid = w.open_demo_account(VISITOR, "كرار", language="iq", pin_hash=pins.hash_pin(PIN))["user"]["id"]
    yield w, uid, app
    db.close()


def brother(w, uid) -> str:
    return next(c["contact_id"] for c in w.contacts(uid) if c.get("alias") == "أخوي")


def transfer(w, uid, amount=20_000, target=None) -> str:
    return w.create_quote(uid, "transfer", target or {"contact_id": brother(w, uid)}, amount)["id"]


def enrol(w, uid) -> None:
    qid = transfer(w, uid)
    assert w.execute(qid, PIN)["state"] == "SUCCEEDED"
    assert w.enroll_voice(uid, qid, OWNER) == {"added": True, "first": True, "samples": 1, "reason": None}


def refused(call) -> dict:
    with pytest.raises(HTTPException) as caught:
        call()
    return {"status": caught.value.status_code, **caught.value.detail}


def audit(w, uid, action) -> list[dict]:
    with w.db.connect() as conn:
        return [json.loads(r["details_json"]) for r in conn.execute(
            "SELECT details_json FROM audit_log WHERE user_id = ? AND action = ? ORDER BY id", (uid, action))]


def test_the_schema_has_the_voice_tables(world):
    w, _, _ = world
    with w.db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST == 6
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"voiceprints", "voice_grants"} <= tables
        assert "approved_by" in {r[1] for r in conn.execute("PRAGMA table_info(quotes)")}
        columns = {r[1] for r in conn.execute("PRAGMA table_info(voiceprints)")}
        assert not columns & {"audio", "media", "sound", "wav", "ogg"}  # a vector, never audio


def test_no_grant_no_voice_payment_and_nothing_is_counted(world):
    w, uid, _ = world
    enrol(w, uid)
    qid = transfer(w, uid)
    balance = w.user_profile(uid)["balance"]
    error = refused(lambda: w.execute(qid, None, factor="voice"))
    assert error["status"] == 403 and error["error"] == "voice_not_approved" and error["reason"] == "no_grant"
    assert w.user_profile(uid)["balance"] == balance and w.get_quote(qid)["state"] == "QUOTED"
    with w.db.connect() as conn:
        assert conn.execute("SELECT pin_failures FROM users WHERE id = ?", (uid,)).fetchone()[0] == 0
    assert w.execute(qid, PIN)["state"] == "SUCCEEDED"  # the PIN still works on the same card


def test_a_grant_pays_once_with_an_audit_row_and_is_consumed(world):
    w, uid, _ = world
    enrol(w, uid)
    qid = transfer(w, uid, 30_000)
    grant = w.grant_voice(qid, uid, OWNER_AGAIN)
    assert grant["granted"] and grant["score"] > 0.9
    balance = w.user_profile(uid)["balance"]
    done = w.execute(qid, None, factor="voice")
    assert done["state"] == "SUCCEEDED" and w.user_profile(uid)["balance"] == balance - 30_250
    assert done["target"]["demo_payee"] is True and done["target"]["payee_user_id"] is None  # _demo_only still rules
    again = w.execute(qid, None, factor="voice")  # a replay: the stored result, never a second debit
    assert again["result"] == done["result"] and w.user_profile(uid)["balance"] == balance - 30_250
    [row] = audit(w, uid, "voice_approved")
    assert row["amount"] == 30_000 and 0.9 < row["score"] <= 1.0
    with w.db.connect() as conn:
        assert conn.execute("SELECT approved_by FROM quotes WHERE id = ?", (qid,)).fetchone()[0] == "voice"
        assert conn.execute("SELECT consumed_at FROM voice_grants WHERE quote_id = ?", (qid,)).fetchone()[0]
    assert w.audit()["ok"]


def test_a_grant_belongs_to_its_quote_and_is_used_once(world):
    w, uid, _ = world
    enrol(w, uid)
    first, second = transfer(w, uid), transfer(w, uid, 25_000)
    assert w.grant_voice(first, uid, OWNER_AGAIN)["granted"]
    assert refused(lambda: w.execute(second, None, factor="voice"))["reason"] == "no_grant"
    with w.db.connect() as conn:  # a grant already used (consumed) can never be used again
        conn.execute("UPDATE voice_grants SET consumed_at = '2026-01-01T00:00:00Z' WHERE quote_id = ?", (first,))
        conn.commit()
    assert refused(lambda: w.execute(first, None, factor="voice"))["reason"] == "no_grant"
    assert w.get_quote(first)["state"] == "QUOTED"


def test_over_the_cap_is_never_granted_nor_paid(world):
    w, uid, _ = world
    enrol(w, uid)
    big = transfer(w, uid, 150_000)
    assert w.grant_voice(big, uid, OWNER_AGAIN) == {"granted": False, "reason": "over_cap", "score": None}
    small = transfer(w, uid, 60_000)
    assert w.grant_voice(small, uid, OWNER_AGAIN)["granted"]
    w.configure_voice(enabled=True, threshold=0.70, max_iqd=50_000)  # the cap lowered after the card was made
    assert refused(lambda: w.execute(small, None, factor="voice"))["reason"] == "over_cap"


def test_another_voice_an_unknown_payee_a_lock_and_the_switch_are_refused(world):
    w, uid, _ = world
    enrol(w, uid)
    qid = transfer(w, uid)
    no = w.grant_voice(qid, uid, STRANGER)
    assert no["granted"] is False and no["reason"] == "no_match" and no["score"] < 0.7
    assert audit(w, uid, "voice_checked")[-1]["matched"] is False
    stranger_number = transfer(w, uid, target={"phone": "07809990002"})
    assert w.grant_voice(stranger_number, uid, OWNER_AGAIN)["reason"] == "unknown_payee"
    with w.db.connect() as conn:
        conn.execute("UPDATE users SET pin_locked_until = '2099-01-01T00:00:00Z' WHERE id = ?", (uid,))
        conn.commit()
    assert w.grant_voice(qid, uid, OWNER_AGAIN)["reason"] == "locked"
    with w.db.connect() as conn:
        conn.execute("UPDATE users SET pin_locked_until = NULL WHERE id = ?", (uid,))
        conn.commit()
    assert w.grant_voice(qid, uid, OWNER_AGAIN)["granted"]
    w.configure_voice(enabled=False)
    assert refused(lambda: w.execute(qid, None, factor="voice"))["reason"] == "off"
    assert w.grant_voice(qid, uid, OWNER_AGAIN)["reason"] == "off"


def test_a_grant_dies_with_the_deleted_voice(world):
    w, uid, _ = world
    enrol(w, uid)
    qid = transfer(w, uid)
    assert w.grant_voice(qid, uid, OWNER_AGAIN)["granted"]
    assert w.delete_voice(uid) == 1 and w.voiceprint_samples(uid) == 0
    assert refused(lambda: w.execute(qid, None, factor="voice"))["reason"] == "no_grant"
    assert audit(w, uid, "voice_deleted") == [{"samples": 1}]
    assert w.grant_voice(qid, uid, OWNER_AGAIN)["reason"] == "not_enrolled"


def test_a_voice_is_kept_only_from_a_quote_paid_with_the_pin(world):
    w, uid, _ = world
    open_one = transfer(w, uid)
    assert w.enroll_voice(uid, open_one, OWNER)["reason"] == "not_pin_paid"  # not paid
    enrol(w, uid)
    voiced = transfer(w, uid)
    assert w.grant_voice(voiced, uid, OWNER_AGAIN)["granted"]
    assert w.execute(voiced, None, factor="voice")["state"] == "SUCCEEDED"
    assert w.enroll_voice(uid, voiced, OWNER_AGAIN)["reason"] == "not_pin_paid"  # paid by voice: proves nothing
    other = w.open_demo_account("9647811113333", "علي", language="iq", pin_hash=pins.hash_pin("5821"))["user"]["id"]
    theirs = transfer(w, other)
    assert w.execute(theirs, "5821")["state"] == "SUCCEEDED"
    assert w.enroll_voice(uid, theirs, OWNER)["reason"] == "not_pin_paid"  # someone else's payment


def test_at_most_three_samples_each_from_a_pin_payment(world):
    w, uid, _ = world
    enrol(w, uid)
    for _ in range(2):
        qid = transfer(w, uid)
        w.execute(qid, PIN)
        assert w.enroll_voice(uid, qid, OWNER_AGAIN)["added"]
    qid = transfer(w, uid)
    w.execute(qid, PIN)
    assert w.enroll_voice(uid, qid, OWNER)["reason"] == "full" and w.voiceprint_samples(uid) == 3


def test_over_http_there_is_no_way_to_grant_and_no_pin_is_no_payment(world):
    w, uid, app = world
    enrol(w, uid)
    qid = transfer(w, uid)
    client = TestClient(app)
    voice = client.post(f"/quotes/{qid}/execute", json={"factor": "voice"}, headers={"Idempotency-Key": qid})
    assert voice.status_code == 403 and voice.json()["detail"]["error"] == "voice_not_approved"
    empty = client.post(f"/quotes/{qid}/execute", json={}, headers={"Idempotency-Key": qid})
    assert empty.status_code == 400 and empty.json()["detail"]["error"] == "pin_required"
    odd = client.post(f"/quotes/{qid}/execute", json={"factor": "face"}, headers={"Idempotency-Key": qid})
    assert odd.status_code == 422
    assert not [r.path for r in app.routes if "voice" in getattr(r, "path", "") or "grant" in getattr(r, "path", "")]
    assert w.get_quote(qid)["state"] == "QUOTED"


def test_a_bill_of_the_account_is_a_known_payee(world):
    w, uid, _ = world
    enrol(w, uid)
    account = next(a for a in w.bill_accounts(uid) if a["biller_id"] == "generator")
    qid = w.create_quote(uid, "bill", {"account_id": account["id"]}, None)["id"]
    assert w.grant_voice(qid, uid, OWNER_AGAIN)["granted"]
    assert w.execute(qid, None, factor="voice")["state"] == "SUCCEEDED"

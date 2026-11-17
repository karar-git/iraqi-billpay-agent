"""Link codes: expiry, single use, rate limits, the lockout, and that the table never holds a code. Offline."""

from __future__ import annotations

import logging
import sqlite3
import threading

import pytest

from channels.instagram.link import LinkCodes, LinkError, TooManyCodes, looks_like_code

IG = "1200000000000042"  # an Instagram-scoped user id (synthetic)
IG2 = "1200000000000077"


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def make(**options) -> tuple[LinkCodes, Clock, sqlite3.Connection]:
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    clock = Clock()
    return LinkCodes(conn, secret="test-secret-not-real", clock=clock, **options), clock, conn


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


# ---------------------------------------------------------------- the happy path


def test_a_code_from_whatsapp_links_the_instagram_sender():
    codes, _, _ = make()
    code = codes.issue("u_ali")
    assert len(code) == 6 and code.isdigit()
    assert codes.linked(IG) is None
    assert codes.redeem(IG, code) == "u_ali"
    assert codes.linked(IG) == "u_ali" and codes.sender_of("u_ali") == IG
    assert codes.linked(IG2) is None and codes.sender_of("u_other") is None


def test_codes_are_always_six_digits_with_leading_zeros():
    codes, _, _ = make(randbelow=lambda n: 42)
    assert codes.issue("u_ali") == "000042"
    assert codes.redeem(IG, "000042") == "u_ali"


def test_a_code_is_read_however_it_is_typed():
    assert looks_like_code("123456") == "123456"
    assert looks_like_code(" ١٢٣ ٤٥٦ ") == "123456"  # Eastern digits, a space in the middle
    assert looks_like_code("۱۲۳۴۵۶") == "123456"
    assert looks_like_code("123-456") == "123456" and looks_like_code("«123456»") == "123456"
    for other in ("12345", "1234567", "12345a", "", "الرمز 123456", "دز لأحمد 123456", None, 123456, "1" * 60):
        assert looks_like_code(other) is None
    codes, _, _ = make(randbelow=lambda n: 123456)
    codes.issue("u_ali")
    assert codes.redeem(IG, "١٢٣ ٤٥٦") == "u_ali"


# ---------------------------------------------------------------- expiry and single use


def test_a_code_expires_after_ten_minutes():
    codes, clock, _ = make()
    code = codes.issue("u_ali")
    clock.tick(599)
    assert codes.redeem(IG, code) == "u_ali"
    late = codes.issue("u_sara")
    clock.tick(600)
    assert codes.redeem(IG2, late) is None and codes.linked(IG2) is None


def test_a_code_works_once():
    codes, _, _ = make()
    code = codes.issue("u_ali")
    assert codes.redeem(IG, code) == "u_ali"
    assert codes.redeem(IG2, code) is None and codes.linked(IG2) is None  # a second phone gets nothing
    assert codes.redeem(IG, code) is None and codes.linked(IG) == "u_ali"  # typing it again changes nothing


def test_a_new_code_replaces_the_old_one():
    codes, _, _ = make()
    first = codes.issue("u_ali")
    second = codes.issue("u_ali")
    assert first != second
    assert codes.redeem(IG, first) is None
    assert codes.redeem(IG, second) == "u_ali"


def test_two_accounts_never_hold_the_same_live_code():
    draws = iter([111111, 111111, 111111, 222222])
    codes, _, _ = make(randbelow=lambda n: next(draws))
    assert codes.issue("u_ali") == "111111"
    assert codes.issue("u_sara") == "222222"  # 111111 was drawn twice more and refused
    assert codes.redeem(IG, "111111") == "u_ali" and codes.redeem(IG2, "222222") == "u_sara"
    stuck, _, _ = make(randbelow=lambda n: 7)
    stuck.issue("u_ali")
    with pytest.raises(LinkError):
        stuck.issue("u_sara")


def test_each_code_goes_to_its_own_account():
    codes, _, _ = make()
    ali, sara = codes.issue("u_ali"), codes.issue("u_sara")
    assert codes.redeem(IG2, sara) == "u_sara" and codes.redeem(IG, ali) == "u_ali"
    assert (codes.linked(IG), codes.linked(IG2)) == ("u_ali", "u_sara")


# ---------------------------------------------------------------- rate limit on asking


def test_three_codes_an_hour_per_account():
    codes, clock, _ = make()
    for _ in range(3):
        codes.issue("u_ali")
        clock.tick(60)
    with pytest.raises(TooManyCodes) as error:
        codes.issue("u_ali")
    assert error.value.retry_after == pytest.approx(3600 - 180)
    assert isinstance(codes.issue("u_sara"), str)  # another account is not held back
    clock.tick(error.value.retry_after - 1)
    with pytest.raises(TooManyCodes):
        codes.issue("u_ali")
    clock.tick(1)
    live = codes.issue("u_ali")
    assert codes.redeem(IG, live) == "u_ali"


def test_a_refused_request_leaves_the_live_code_alone():
    codes, _, _ = make()
    codes.issue("u_ali"), codes.issue("u_ali")
    live = codes.issue("u_ali")
    with pytest.raises(TooManyCodes):
        codes.issue("u_ali")
    assert codes.redeem(IG, live) == "u_ali"


def test_issue_needs_a_user():
    codes, _, _ = make()
    for bad in ("", None):
        with pytest.raises(ValueError):
            codes.issue(bad)


# ---------------------------------------------------------------- wrong codes and the lockout


def test_five_wrong_codes_lock_the_sender_for_an_hour_even_for_the_right_code():
    codes, clock, _ = make()
    code = codes.issue("u_ali")
    assert codes.tries_left(IG) == 5 and codes.locked_until(IG) is None
    for n in range(4):
        assert codes.redeem(IG, wrong(code)) is None
        assert codes.tries_left(IG) == 4 - n and codes.locked_until(IG) is None
    assert codes.redeem(IG, wrong(code)) is None  # the fifth
    assert codes.tries_left(IG) == 0 and codes.locked_until(IG) == pytest.approx(clock.now + 3600)
    assert codes.redeem(IG, code) is None and codes.linked(IG) is None  # locked: the right code does not pass
    assert codes.redeem(IG2, code) == "u_ali"  # another sender is not locked, and the code was not burnt
    clock.tick(3599)
    assert codes.locked_until(IG) is not None
    clock.tick(1)
    assert codes.locked_until(IG) is None and codes.tries_left(IG) == 5
    fresh = codes.issue("u_sara")
    assert codes.redeem(IG, fresh) == "u_sara"


def test_tries_while_locked_do_not_extend_the_lock():
    codes, clock, _ = make()
    code = codes.issue("u_ali")
    for _ in range(5):
        codes.redeem(IG, wrong(code))
    until = codes.locked_until(IG)
    clock.tick(1800)
    for _ in range(10):
        codes.redeem(IG, wrong(code))
    assert codes.locked_until(IG) == until


def test_wrong_tries_older_than_an_hour_are_forgotten_and_a_right_code_clears_them():
    codes, clock, _ = make()
    code = codes.issue("u_ali")
    for _ in range(4):
        codes.redeem(IG, wrong(code))
    clock.tick(3601)
    assert codes.tries_left(IG) == 5
    code = codes.issue("u_ali")
    for _ in range(4):
        codes.redeem(IG, wrong(code))
    assert codes.redeem(IG, code) == "u_ali" and codes.tries_left(IG) == 5


def test_what_is_not_a_code_is_never_counted_as_a_try():
    codes, _, _ = make()
    codes.issue("u_ali")
    for text in ("هلا", "شكد رصيدي", "12345", "1234567", "", None):
        assert codes.redeem(IG, text) is None
    assert codes.tries_left(IG) == 5
    assert codes.redeem("", "123456") is None and codes.redeem(None, "123456") is None


def test_an_expired_or_used_code_counts_as_a_wrong_try():
    codes, clock, _ = make()
    code = codes.issue("u_ali")
    clock.tick(601)
    assert codes.redeem(IG, code) is None and codes.tries_left(IG) == 4


def test_a_flood_of_wrong_codes_brakes_the_guessers_but_never_the_person_with_a_live_code():
    codes, clock, _ = make(global_wrong_per_hour=20)
    code = codes.issue("u_ali")
    assert codes.paused_until() is None
    for n in range(20):  # twenty senders, one guess each: none of them is locked on its own
        assert codes.redeem(f"99{n:04d}", wrong(code)) is None
    assert codes.paused_until() == pytest.approx(clock.now + 3600)
    assert codes.locked_until("990003") == pytest.approx(clock.now + 3600)  # a guesser may not guess again
    assert codes.redeem("990003", code) is None and codes.linked("990003") is None
    assert codes.locked_until(IG) is None and codes.tries_left(IG) == 5  # no wrong code of its own: not held
    assert codes.redeem(IG2, wrong(code)) is None  # a fresh sender's first wrong code during the flood holds it
    assert codes.locked_until(IG2) == pytest.approx(clock.now + 3600) and codes.tries_left(IG2) == 0
    assert codes.redeem(IG, code) == "u_ali"  # the real user, with the live code, links all the same
    clock.tick(3600)
    assert codes.paused_until() is None and codes.locked_until("990003") is None
    calm, _, _ = make(global_wrong_per_hour=0)  # the brake can be switched off
    code = calm.issue("u_ali")
    for n in range(150):
        calm.redeem(f"99{n:04d}", wrong(code))
    assert calm.paused_until() is None
    assert calm.redeem(IG, code) == "u_ali"


def test_the_regression_twenty_throwaway_accounts_cannot_switch_linking_off():
    """Twenty senders x five wrong codes used to lock every sender in the world for an hour."""
    codes, _, _ = make()
    code = codes.issue("u_ali")
    for n in range(20):
        for _ in range(5):
            codes.redeem(f"99{n:04d}", wrong(code))
    assert codes.paused_until() is not None
    assert codes.redeem(IG, code) == "u_ali"


# ---------------------------------------------------------------- links


def test_one_instagram_account_per_wallet_and_the_newest_link_wins():
    codes, _, _ = make()
    assert codes.redeem(IG, codes.issue("u_ali")) == "u_ali"
    assert codes.redeem(IG2, codes.issue("u_ali")) == "u_ali"  # the same wallet from a new Instagram account
    assert codes.linked(IG) is None and codes.linked(IG2) == "u_ali"
    assert codes.redeem(IG2, codes.issue("u_sara")) == "u_sara"  # the same Instagram account moves to another wallet
    assert codes.linked(IG2) == "u_sara" and codes.sender_of("u_ali") is None


def test_unlink_cuts_the_link_and_voids_the_live_code():
    codes, _, _ = make()
    assert codes.unlink("u_ali") is False
    codes.redeem(IG, codes.issue("u_ali"))
    pending = codes.issue("u_ali")
    assert codes.unlink("u_ali") is True
    assert codes.linked(IG) is None and codes.redeem(IG, pending) is None
    assert codes.unlink("u_ali") is False


# ---------------------------------------------------------------- what the table holds, and what is logged


def test_the_table_holds_only_a_salted_hash_and_nothing_logs_a_code(caplog):
    caplog.set_level(logging.DEBUG)
    codes, _, conn = make()
    code = codes.issue("u_ali")
    other = codes.issue("u_sara")
    rows = conn.execute("SELECT * FROM ig_link_codes ORDER BY user_id").fetchall()
    assert len(rows) == 2
    for row in rows:
        for value in row:
            as_text = value.hex() if isinstance(value, bytes) else str(value)
            assert code not in as_text and other not in as_text
            if isinstance(value, bytes):
                assert code.encode() not in value and other.encode() not in value
    (salt_a, hash_a), (salt_b, hash_b) = [(r[1], r[2]) for r in rows]
    assert len(salt_a) == 16 and len(hash_a) == 32 and salt_a != salt_b and hash_a != hash_b
    codes.redeem(IG, wrong(code))
    codes.redeem(IG, code)
    dump = "\n".join(conn.iterdump())
    assert code not in dump and other not in dump and wrong(code) not in dump
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert code not in logged and other not in logged and "u_ali" not in logged and IG not in logged


def test_the_same_code_hashes_differently_per_row_and_per_secret():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    a = LinkCodes(conn, secret="one", randbelow=lambda n: 555555)
    a.issue("u_ali")
    first = conn.execute("SELECT salt, code_hash FROM ig_link_codes").fetchone()
    a.unlink("u_ali")
    a.issue("u_ali")
    second = conn.execute("SELECT salt, code_hash FROM ig_link_codes").fetchone()
    assert first[0] != second[0] and first[1] != second[1]  # a fresh salt each time
    b = LinkCodes(conn, secret="two")  # another key: the stored hash matches nothing
    assert b.redeem(IG, "555555") is None
    assert a.redeem(IG, "555555") == "u_ali"


# ---------------------------------------------------------------- the connection


def test_tables_are_created_once_and_survive_a_second_instance(tmp_path):
    path = tmp_path / "accounts.db"
    conn = sqlite3.connect(path, isolation_level=None)
    codes = LinkCodes(conn, secret="s")
    codes.redeem(IG, codes.issue("u_ali"))
    conn.close()
    again = sqlite3.connect(path, isolation_level=None)
    assert LinkCodes(again, secret="s").linked(IG) == "u_ali"
    again.close()


def test_works_on_a_default_connection_and_commits_its_own_work(tmp_path):
    path = tmp_path / "plain.db"
    conn = sqlite3.connect(path)  # python's default transaction handling
    codes = LinkCodes(conn, secret="s")
    code = codes.issue("u_ali")
    assert not conn.in_transaction
    assert codes.redeem(IG, code) == "u_ali" and not conn.in_transaction
    other = sqlite3.connect(path)
    assert other.execute("SELECT user_id FROM ig_links").fetchone() == ("u_ali",)  # committed: another reader sees it
    other.close()
    conn.close()


def test_works_on_a_connection_that_is_always_in_a_transaction(tmp_path):
    path = tmp_path / "pep249.db"
    conn = sqlite3.connect(path, autocommit=False)
    codes = LinkCodes(conn, secret="s")
    assert codes.redeem(IG, codes.issue("u_ali")) == "u_ali"
    other = sqlite3.connect(path)
    assert other.execute("SELECT user_id FROM ig_links").fetchone() == ("u_ali",)  # committed
    other.close()
    conn.close()


def test_inside_the_callers_transaction_the_caller_decides():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    codes = LinkCodes(conn, secret="s")
    conn.execute("BEGIN")
    code = codes.issue("u_ali")
    assert conn.in_transaction
    conn.execute("ROLLBACK")
    assert codes.redeem(IG, code) is None  # the caller rolled the code back


def test_a_failure_inside_rolls_back_and_leaves_the_connection_usable():
    def broken(n: int) -> int:
        raise RuntimeError("no randomness")

    conn = sqlite3.connect(":memory:", isolation_level=None)
    codes = LinkCodes(conn, secret="s", randbelow=broken)
    with pytest.raises(RuntimeError):
        codes.issue("u_ali")
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM ig_link_issues").fetchone()[0] == 0


def test_one_code_redeemed_from_two_threads_links_once():
    codes, _, _ = make()
    code = codes.issue("u_ali")
    results: list[str | None] = []
    start = threading.Barrier(8)

    def go(sender: str) -> None:
        start.wait()
        results.append(codes.redeem(sender, code))

    threads = [threading.Thread(target=go, args=(f"98{n:04d}",)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("u_ali") == 1 and results.count(None) == 7

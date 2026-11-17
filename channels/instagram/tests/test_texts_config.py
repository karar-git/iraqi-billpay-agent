"""The Instagram channel's texts (three languages, the text rules) and its settings. Offline."""

from __future__ import annotations

import string
from pathlib import Path

import pytest

from channels.instagram import texts
from channels.instagram.client import TEXT_MAX_BYTES, utf8_len
from channels.instagram.config import REQUIRED, Settings

PACKAGE = Path(__file__).resolve().parents[1]
SLOTS = {"wa_link": texts.wa_link("9647700000000", "iq"), "phrase": texts.link_phrase("msa"), "minutes": 60,
         "left": 4, "code": "123456", "count": 100}


# ---------------------------------------------------------------- texts


def test_every_text_exists_in_three_languages_with_the_same_slots():
    def slots(template: str) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(template) if name}

    assert set(texts.IQ) == set(texts.MSA) == set(texts.EN)
    for key, reference in texts.IQ.items():
        variants = list(reference.values()) if isinstance(reference, dict) else [reference]
        wanted = slots(variants[0])
        for lang in ("iq", "msa", "en"):
            entry = texts.BY_LANG[lang][key]
            for template in (entry.values() if isinstance(entry, dict) else [entry]):
                assert slots(template) == wanted, (key, lang)
            if isinstance(entry, dict):
                assert set(entry) == {"f", "m"}, (key, lang)


def test_the_needed_texts_are_there():
    for key in ("not_linked", "link_ok", "wrong_code", "locked", "link_code", "voice_not_here", "too_many_codes",
                "linked_notice", "unlinked"):
        for lang in ("iq", "msa", "en"):
            for gender in ("f", "m", None):
                assert texts.say(key, gender, lang, **SLOTS).strip()


def test_iraqi_writes_jeem_and_nothing_has_an_em_dash():
    for text in texts.all_texts():
        assert "چ" not in text and "—" not in text and "–" not in text
    iraqi = " ".join(texts.all_texts("iq"))
    assert "محفظتج" in iraqi and "يوصلج" in iraqi  # the feminine is written with ج
    for path in PACKAGE.rglob("*"):
        if path.suffix in (".py", ".md") and path.name != Path(__file__).name:
            content = path.read_text(encoding="utf-8")
            assert "—" not in content and "–" not in content, path.name
            assert "چ" not in content, path.name


def test_every_text_fits_one_instagram_message():
    for lang in ("iq", "msa", "en"):
        slots = {**SLOTS, "wa_link": texts.wa_link("9647700000000", lang), "phrase": texts.link_phrase(lang)}
        for key in texts.IQ:
            for gender in ("f", "m"):
                assert utf8_len(texts.say(key, gender, lang, **slots)) <= TEXT_MAX_BYTES, (key, lang, gender)


def test_gender_and_language_pick_the_text():
    f = texts.say("link_ok", "f", "iq")
    m = texts.say("link_ok", "m", "iq")
    assert "محفظتج" in f and "محفظتك" in m
    assert texts.say("link_ok", None, "iq") == m  # someone unknown: the masculine
    assert texts.say("link_ok", "f", "xx") == f and texts.say("link_ok", "f") == f  # iq is the default
    assert texts.say("link_ok", "f", "en").startswith("Linked")
    assert "123456" in texts.say("link_code", "m", "iq", code="123456", minutes=10)
    assert "0042" in texts.say("link_code", "m", "en", code="000042", minutes=10)  # a code is never reformatted


def test_the_not_linked_text_tells_the_whole_way():
    for lang in ("iq", "msa", "en"):
        link = texts.wa_link("+964 770 000 0000", lang)
        text = texts.say("not_linked", "m", lang, wa_link=link, phrase=texts.link_phrase(lang), minutes=10)
        assert link in text and texts.link_phrase(lang) in text and "10" in text and "6" in text


def test_wa_link_is_a_plain_wa_me_link_with_the_request_typed():
    link = texts.wa_link("+964 770 000 0000", "en")
    assert link == "https://wa.me/9647700000000?text=link%20instagram"
    assert texts.wa_link("9647700000000").startswith("https://wa.me/9647700000000?text=%D8%A7")
    assert " " not in texts.wa_link("9647700000000", "msa")


def test_minutes_round_up():
    assert [texts.minutes(s) for s in (0, 1, 59, 60, 61, 3600, -5)] == [1, 1, 1, 1, 2, 60, 1]


def test_link_and_unlink_requests_are_recognised_in_three_languages():
    for lang in ("iq", "msa", "en"):
        assert texts.wants_link(texts.link_phrase(lang)) and not texts.wants_unlink(texts.link_phrase(lang))
        assert texts.wants_unlink(texts.unlink_phrase(lang)) and not texts.wants_link(texts.unlink_phrase(lang))
    for said in ("اربط انستغرام", "اربطي الانستا", "ربط إنستغرام.", "Link Instagram", "connect insta",
                 "اربط حساب انستكرام"):
        assert texts.wants_link(said), said
    for said in ("افصل انستغرام", "إلغاء ربط إنستغرام", "unlink instagram", "Disconnect Instagram!"):
        assert texts.wants_unlink(said), said
    for said in ("اربط", "انستغرام", "دز لأحمد خمسين", "ادفع المولدة", "link", "اربط انستغرام ودز لأحمد خمسين",
                 "", None, 5, "اربط انستغرام " * 20):
        assert not texts.wants_link(said) and not texts.wants_unlink(said), said


# ---------------------------------------------------------------- settings


ENV = {"INSTAGRAM_TOKEN": "IGAA-token-not-real", "INSTAGRAM_APP_SECRET": "app-secret-not-real",
       "INSTAGRAM_VERIFY_TOKEN": "verify-not-real", "INSTAGRAM_ACCOUNT_ID": "17841400000000001"}


def test_settings_from_env_never_show_secrets():
    s = Settings.from_env({**ENV, "INSTAGRAM_API_BASE": "https://graph.facebook.com", "INSTAGRAM_API_VERSION": "v25.0"})
    assert (s.token, s.app_secret, s.verify_token) == (ENV["INSTAGRAM_TOKEN"], ENV["INSTAGRAM_APP_SECRET"],
                                                       ENV["INSTAGRAM_VERIFY_TOKEN"])
    for secret in (ENV["INSTAGRAM_TOKEN"], ENV["INSTAGRAM_APP_SECRET"], ENV["INSTAGRAM_VERIFY_TOKEN"]):
        assert secret not in repr(s) and secret not in str(s)
    assert "17841400000000001" in repr(s)
    assert (s.api_base, s.api_version) == ("https://graph.facebook.com", "v25.0")
    assert s.ready and s.missing == []


def test_defaults_and_ready():
    s = Settings.from_env(ENV)
    assert (s.api_base, s.api_version) == ("https://graph.instagram.com", "v27.0") and s.ready
    empty = Settings.from_env({})
    assert not empty.ready and empty.missing == list(REQUIRED)
    for name in REQUIRED:
        partial = Settings.from_env({**ENV, name: "  "})
        assert not partial.ready and partial.missing == [name]
    assert Settings().ready is False


def test_a_wrong_host_version_or_id_is_refused_at_start_up():
    for bad in ({"INSTAGRAM_API_BASE": "https://graph.instagram.com.evil.example"},
                {"INSTAGRAM_API_BASE": "http://graph.facebook.com"},
                {"INSTAGRAM_API_VERSION": "latest"},
                {"INSTAGRAM_ACCOUNT_ID": "my_shop"}):
        with pytest.raises(ValueError) as error:
            Settings.from_env({**ENV, **bad})
        assert ENV["INSTAGRAM_TOKEN"] not in str(error.value)


def test_the_link_secret_is_derived_never_the_app_secret_itself():
    s = Settings.from_env(ENV)
    assert len(s.link_secret) == 32 and ENV["INSTAGRAM_APP_SECRET"].encode() not in s.link_secret
    assert s.link_secret == Settings.from_env(ENV).link_secret
    assert s.link_secret != Settings.from_env({**ENV, "INSTAGRAM_APP_SECRET": "another"}).link_secret


def test_settings_read_the_process_environment_by_default(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("INSTAGRAM_API_BASE", raising=False)
    monkeypatch.delenv("INSTAGRAM_API_VERSION", raising=False)
    assert Settings.from_env().ready

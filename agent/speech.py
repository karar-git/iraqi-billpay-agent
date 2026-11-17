"""Voice replies: when the agent answers with a voice note, and what that note says.

The voice itself comes from the core, when it can speak (core.interface.SpeechCore). This module only
holds the policy and the words, so the web chat and the WhatsApp channel behave the same:

- VOICE_REPLY_MODE: "mirror" (default) answers a voice note with a voice note; "always" speaks every
  reply; "off" never speaks. The text (and the card with its buttons) always goes out too.
- What is said: the reply's lines, then for a card its short spoken summary (who, how much in words,
  the fee, its first warning, which button confirms). Never a PIN: the orchestrator never repeats one,
  and any value the channel passes in `never` (the demo PIN) drops the whole line it appears in, as
  digits (Western or Eastern, «١٢٣٤»), as words («ألف وميتين واربعة وتلاثين») or as digits said one by one.
  A line written by a conversing core (Session.talk) is said like any other line, under the same rule.
- Made for the ear: money written as digits («404,750 دينار») is said in words in the reply's language,
  a list «1) أحمد، 2) علي» is said with ordinals, and the tatweel of «لـ هادي» is dropped. The line with the
  transaction reference (Reply.reference) is left to the text: it is for the eye. Options to tap are never read out.
- A voice that fails is skipped: the text still goes out.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from payments.quote import amount_in_words

log = logging.getLogger("agent.speech")

MODES = ("mirror", "always", "off")
DEFAULT_MODE = "mirror"
MAX_SPOKEN = 700  # characters: a short voice note, and a cap on what it costs
_MARKS = re.compile(r"[«»“”\"*_~`•·|]|[\U0001F000-\U0001FAFF☀-➿️]")
_MONEY = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+)\s*(?:دينار|dinars?|IQD)", re.IGNORECASE)
_OPTION = re.compile(r"(?:(?<=\s)|(?<=^)|(?<=[،,:]))([1-9])\)\s*")
_TATWEEL = re.compile(r"ـ\s*(?=[^\d\s])|ـ")
_WESTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
ORDINAL_WORDS = {
    "iq": ("الأول", "الثاني", "الثالث", "الرابع", "الخامس", "السادس", "السابع", "الثامن", "التاسع"),
    "msa": ("الأول", "الثاني", "الثالث", "الرابع", "الخامس", "السادس", "السابع", "الثامن", "التاسع"),
    "en": ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"),
}


DIGIT_WORDS = {
    "iq": ("صفر", "واحد", "اثنين", "تلاثة", "اربعة", "خمسة", "ستة", "سبعة", "ثمنية", "تسعة"),
    "msa": ("صفر", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"),
    "en": ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"),
}
# The end of a phone number or an account number is said digit by digit («…4567», «ينتهي بـ 4567»), never as a
# number («أربعة آلاف وخمسمية…»); a year or an amount is left alone.
_LAST_DIGITS = re.compile(r"(…|\.\.\.|ينتهي بـ ?|المنتهي بـ ?|ending in |رقم |number )(\d{3,12})(?!\d)")


def _digit_words(digits: str, lang: str) -> str:
    return " ".join(DIGIT_WORDS[lang][int(d)] for d in digits)


def for_the_ear(line: str, lang: str) -> str:
    """One line made for speech: money in words, the end of a number digit by digit, «1)» as an ordinal,
    no tatweel."""
    lang = lang if lang in ORDINAL_WORDS else "iq"
    line = _LAST_DIGITS.sub(lambda m: m.group(1).replace("…", "").replace("...", "") + _digit_words(m.group(2), lang),
                            line)

    def money(m: re.Match) -> str:
        try:
            return amount_in_words(int(m.group(1).replace(",", "")), lang)
        except ValueError:
            return m.group(0)

    line = _MONEY.sub(money, line)
    line = _OPTION.sub(lambda m: f"{ORDINAL_WORDS[lang][int(m.group(1)) - 1]}: ", line)
    return _TATWEEL.sub("", line)


def _pin_forms(secret: str) -> list[str]:
    """A PIN as digits and as amount words in each reply language (so it is never said in any form)."""
    forms = [secret]
    if secret.isdigit() and int(secret) > 0:
        for lang in ORDINAL_WORDS:
            try:
                forms.append(amount_in_words(int(secret), lang).rsplit(" ", 1)[0])  # without «دينار»
            except ValueError:
                pass
    return forms


def reply_mode(env: dict[str, str] | None = None) -> str:
    """VOICE_REPLY_MODE from the environment: mirror | always | off (anything else: mirror)."""
    env = os.environ if env is None else env
    mode = (env.get("VOICE_REPLY_MODE") or "").strip().lower()
    if mode in ("never", "none", "0", "false", "no"):
        return "off"
    return mode if mode in MODES else DEFAULT_MODE


def wanted(mode: str, user_sent_voice: bool) -> bool:
    """Whether this reply should also go out as a voice note."""
    return mode == "always" or (mode == "mirror" and user_sent_voice)


def can_speak(core: Any) -> bool:
    return callable(getattr(core, "speak", None))


def spoken_text(reply: Any, *, never: tuple[str, ...] | list[str] = ()) -> str:
    """The words of a voice reply: the reply's lines, then the card's spoken summary, made for the ear."""
    parts = [str(line).strip() for line in getattr(reply, "lines", []) or [] if str(line).strip()]
    card = getattr(reply, "card", None)
    if card is not None and getattr(card, "spoken", ""):
        parts.append(card.spoken)
    lang = getattr(reply, "lang", None) or "iq"
    secrets = [str(s) for s in never if s and len(str(s)) >= 3]
    digit_secrets = [re.compile(rf"(?<!\d){re.escape(s)}(?!\d)") for s in secrets]
    # as words only when they are unmistakable (3+ words): a round PIN like 2000 must not mute every amount
    word_secrets = [form for s in secrets for form in _pin_forms(s)[1:] if len(form.split()) >= 3]
    # and digit by digit («واحد اثنين تلاثة اربعة»), the way the end of a number is said
    digit_by_digit = [_digit_words(s, code) for s in secrets if s.isdigit() for code in DIGIT_WORDS]
    reference = str(getattr(reply, "reference", None) or "")
    kept = []
    for part in parts:
        for line in part.splitlines():
            if reference and reference in line:
                continue  # a transaction reference is for the eye: letters and digits read out help nobody
            western = line.translate(_WESTERN)
            if any(rx.search(western) or rx.search(re.sub(r"(?<=\d)[,٬\s](?=\d)", "", western))
                   for rx in digit_secrets):
                continue  # never say a PIN out loud (Eastern digits too, or spaced «1 2 3 4»)
            line = for_the_ear(line, lang)
            if any(form in line for form in word_secrets) or any(form in line for form in digit_by_digit):
                continue  # nor the PIN as words («ألف وميتين واربعة وتلاثين»), nor digit by digit
            line = re.sub(r"\s+", " ", _MARKS.sub(" ", line)).strip()
            if line:
                kept.append(line)
    text = " ".join(kept)
    if len(text) > MAX_SPOKEN:
        cut = text[:MAX_SPOKEN]
        end = max(cut.rfind("."), cut.rfind("؟"), cut.rfind("?"), cut.rfind("،"))
        text = cut[: end + 1] if end > MAX_SPOKEN // 2 else cut
    return text


def speak(core: Any, text: str, language: str, context: dict[str, Any]) -> tuple[bytes, str] | None:
    """The core's voice for `text`, or None (no voice, nothing to say, or it failed)."""
    if not text.strip() or not can_speak(core):
        return None
    try:
        out = core.speak(text, language, context)
    except Exception as exc:  # a voice that fails never blocks the text reply
        log.warning("voice reply failed: %s", type(exc).__name__)
        return None
    if not (isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], (bytes, bytearray)) and out[0]):
        return None
    return bytes(out[0]), str(out[1] or "audio/ogg")

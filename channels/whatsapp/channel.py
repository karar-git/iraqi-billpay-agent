"""The WhatsApp channel: real accounts, recognised by their WhatsApp number, driven through the orchestrator.

Every person is one account in one shared database (DATA_DIR/accounts.db): the number they write from is
the account. The money is still a mock ledger in IQD inside that database (no payment network yet), but the
experience is a real account: sign up from WhatsApp (channels/whatsapp/onboarding.py), keep the people you
know, send money to any Iraqi number (an account gets it at once; a number with no account has it held for
7 days), pay your own saved bills, read your history, change or reset your PIN.

The orchestrator (agent/session.py) decides everything about money. This module turns its replies into
WhatsApp messages:
- a confirmation card becomes an interactive message with the three reply buttons;
- a question with a closed set of answers (which Ahmed, which bill, which month, yes or no) carries them to tap:
  reply buttons for one to three, a list for four to ten, and the numbered text as before when there are more
  or the transport sends no lists. A tapped option's id («opt_…») goes to Session.handle_option, which looks it
  up against the open question: the title that comes back with a tap is never read, and no option can pay. The
  card's own buttons («confirm:<quote id>») are a different shape and keep their own path;
- the main menu is a list: when asked for («القائمة», "menu"), and after a receipt or a refusal (with «شلون تأكدت؟»
  as its first row). With WHATSAPP_MENU_ON_HELLO=1 it also follows the greeting, the help command and the end of
  sign-up, as a second message (off by default: a hello is answered with one message). «ليش» / "why" says what was
  checked, and leaves a card open. The card itself keeps its three buttons, which is all a message may hold, so
  «شلون تأكدت؟» comes right under it as a one-button message (CARD_WHY_BUTTON=0 leaves it out). Its id carries the
  card's quote id, so its tap explains that card's checks, and only while that card is open: the card, and its PIN
  step, stay open;
- «إي، ادفع» asks for the PIN, which the user types as a message. That message is caught here and handed
  to Session.handle_button with the card's quote id: it never reaches the core;
- a PIN-shaped message is never handed to the orchestrator while a card is open, nor right after the tapped
  card was voided (see Channel._guard). Small talk during the PIN step («لحظة») keeps the card open;
- account commands (contacts, bill accounts, history, my account, change / forgot PIN) are answered by
  agent/manage.py before the core, with no model and no phone number leaving the service;
- an expired card is refused, a second tap on a paid card never pays again and shows its receipt;
- a receipt is built from the wallet's own record of the quote;
- notices to other people (money received, a hold claimed) go out inside their 24-hour window
  (channels/whatsapp/notify.py), or wait for their next message;
- a photo is read only as an identity document, and only when one was asked for: at the document step of sign-up
  (SIGNUP_KYC, channels/whatsapp/onboarding.py) or after «وثّق حسابي» / "verify my account" from an account at
  level none or pending. It is downloaded into memory, read once and dropped: never on disk, in a database, in a
  log or in the outbox. At any other moment a photo gets one line saying so. The level decides the limits, in code
  (kyc.policy): the daily limit on the account, and the most in one payment as a hard stop of payments.rules.

Instagram (channels/instagram/, mounted by app.py when its settings are complete) is a second door to the same
accounts. An Instagram sender is «ig:<id>» here (channels/instagram/wiring.py), never a phone: the account comes
from the link made with a code issued in the WhatsApp chat («اربط انستغرام»). Its conversation is a runtime of its
own (its own open card, question and PIN step), over the same wallet: the same balance, contacts, bills, holds and
limits. Two turns of one account never run at the same time, whichever app they came from, and an Instagram turn
checks its link again once it holds the account's turn: a link cut while it waited is cut for it too. Replies go out
with the Instagram client; no voice reply is made there. Sign-up, changing or resetting the PIN and the identity
document stay on WhatsApp. Which door a message came through is decided by the route that took it (Channel.accept's
`door`), never by the text of its sender. With an allow-list, an Instagram sender that is not linked gets the same
silence as a stranger on WhatsApp, except for a message that is a link code.

Language: every reply follows the sender's language, message by message (Iraqi, Modern Standard Arabic for
any other Arabic, or English); the account remembers the last one for notices.

Voice: a voice note is heard by the core (with the user's saved names, bill labels and what was just asked,
when the core can), read like typed text, and answered with a voice note too (VOICE_REPLY_MODE=mirror). What
was heard is shown («سمعتك: …») only when the reply is a question or a refusal: a card already says who and how
much. A PIN is never said: during sign-up and the PIN steps a voice note gets «type it, please». A voice note while
a payment's PIN is asked for is not even heard (its audio never goes to the core), and it can never pay.

Demo accounts (SIGNUP_MODE=demo, channels/whatsapp/demo.py): the first message from a new sender opens an account
with pretend money, the two-bubble intro (the second one is the PIN alone) goes out first, then the person's own
first messages are answered by the normal turn. A sender whose WhatsApp shows no number gets one too, under an
account key nobody can send to. A demo account's own PIN is recognised inside any message (checked against its
hash), so it never reaches the core; «نسيت الرمز» gives it a fresh PIN in the chat.

After a payment confirmed with a typed PIN, one line asks the person to delete that message with «Delete for
everyone»: WhatsApp has no way for a business to delete it.

Voice approval (VOICE_APPROVAL=1 with a working speaker model, accounts/voiceprint.py): a voice note (a real voice
note, never an audio file and never a forwarded one, on WhatsApp only) is also turned into a speaker vector here, on
this server, while it is heard. When that note makes a card, the wallet compares the vector with the account's kept
voice and, if it matches and the rest holds (the cap, a known payee, an account with no PIN lock), records a one-time
grant for that card's quote (Wallet.grant_voice): the card then says the voice matched, and «إي، ادفع» pays with no
PIN (Session.handle_button(voice=True), which the wallet accepts only on that grant). Anything else is the PIN, as
before. The voice is kept only from a voice note whose card was then paid with the right PIN (Wallet.enroll_voice),
which is said once in one line; «امسح صوتي» / "delete my voice" deletes it. A voice note while the PIN is asked for is
still never heard and never pays.

Pacing: the typing dots are shown at once (for a voice note too) and refreshed every TYPING_REFRESH_SECONDS while a
turn works; with HUMAN_PACE each text bubble waits a moment scaled to its length (at most PACE_MAX_SECONDS).

Small talk the channel answers itself (a hello, thanks, a sticker or a photo it cannot read) goes through the
session's writer when it has one (Session.talk), with the fixed line as its fallback. The balance is mentioned at the
first hello of a Baghdad day at most. «ما انخصم منك شي» is said once per message, however many lines carried it.

Guard rails: the hourly message cap per sender, the optional allow-list, the daily budget of the core's spend
(voice included), the wallet's daily limit per account, and the 24 h cap after an admin PIN reset.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient

from accounts import billing, phones
from accounts import texts as account_texts
from accounts import voiceprint as voiceprints
from accounts.service import Accounts
from agent import speech
from agent.manage import ManageReply, command_language, pin_command
from agent.pins import looks_like_pin, mentions_pin, pin_in
from agent.session import EXPLAIN_OPTION, MENU_HELP_OPTION, Option, Reply, Session, biller_name
from agent.wallet_client import WalletClient, WalletError, WalletUnavailable
from channels.instagram import texts as ig_texts
from channels.instagram.link import looks_like_code
from channels.instagram.wiring import Instagram, is_instagram, unscoped
from channels.whatsapp import texts
from channels.whatsapp.config import Settings
from channels.whatsapp.demo import Demo
from channels.whatsapp.dispatch import Dispatcher
from channels.whatsapp.graph import (BUTTON_TITLE_MAX, LIST_BUTTON_MAX, LIST_ROW_DESCRIPTION_MAX, LIST_ROW_TITLE_MAX,
                                     LIST_ROWS_MAX, Graph, Inbound, list_payload)
from channels.whatsapp.notify import KEYS as NOTICE_KEYS
from channels.whatsapp.notify import Notifier
from channels.whatsapp.onboarding import CANCEL as STEP_CANCEL
from channels.whatsapp.onboarding import Onboarding
from channels.whatsapp.store import REDACTED, ChannelStore, sender_key
from core.loader import load_core
from payments import language, names
from payments.messages import format_iqd, format_period, render
from payments.text import fold
from wallet.api import create_app as create_wallet
from wallet.db import Database, verify_pin

log = logging.getLogger("whatsapp.channel")

BAGHDAD = timezone(timedelta(hours=3))
MAX_AGE_SECONDS = 15 * 60  # backlog replayed by Meta after downtime is dropped
MAX_TEXT = 4000  # WhatsApp takes 4096 characters in one text
MAX_BUTTON_BODY = 1024  # and 1024 above a row of buttons
IDLE_SECONDS = 30 * 60  # an idle sender's conversation is closed (the account stays)
MAX_OPEN = 64  # conversations kept open at once
DEMO_KEEP_DAYS = 7  # the old per-sender demo wallets are kept this long after the move, then deleted
TYPING_REFRESH_SECONDS = 20.0  # WhatsApp drops the typing dots after about 25 s: a long turn shows them again
PACE_MAX_SECONDS = 2.0  # the typing pause before a text bubble (HUMAN_PACE), at most this long
DEMO_PIN_LEN = 4  # a demo account's PIN (accounts.pins.new_pin): what is looked for inside a message
DEMO_PIN_CHECKS = 3  # at most this many 4-digit numbers of one message are checked against the PIN's hash
VOICEPRINT_WAIT = 15.0  # seconds a card waits for its voice note's speaker vector (it is made while the note is heard)
VOICE_CARDS_KEPT = 8  # cards of one chat whose voice check is remembered
# The "nothing was taken" sentence, said once per message: every failure line carries it, and a turn can stack two.
# Its forms: «وما انخصم منج شي.», «ولم يُخصم منك شيء.», "and nothing was taken." (the conjunction is group 1). Only a
# sentence that ends there counts: «لم يُخصم منك شيء بسبب…» is not the disclaimer.
_NOTHING_TAKEN = re.compile(r"(?:(?<=^)|(?<=[\s،,]))(و|and )?(?:ما انخصم من[كج] شي|لم يُ?خصم منك شيء|"
                            r"[Nn]othing was taken)\.")


def _norm_set(*words: str) -> frozenset[str]:
    return frozenset(names.normalize(w) for w in words)


COMMANDS = {
    # «what can you do?» too: answered with the full help and the menu, and an open card stays open
    "help": _norm_set("مساعدة", "مساعده", "الاوامر", "اوامر", "الأوامر", "help", "/help", "commands", "/start",
                      "شنو تگدر تسوي", "شنو تكدر تسوي", "شتگدر تسوي", "شتكدر تسوي", "what can you do",
                      "ماذا تستطيع أن تفعل"),
    # the person's kept voice (voice approval) is deleted: typed or said
    "voice_delete": _norm_set("امسح صوتي", "امسحوا صوتي", "احذف صوتي", "احذفوا صوتي", "امسح بصمة صوتي",
                              "احذف بصمة صوتي", "امسح بصمتي الصوتية", "احذف بصمتي الصوتية", "امسح البصمة الصوتية",
                              "احذف البصمة الصوتية", "delete my voice", "remove my voice", "forget my voice",
                              "delete my voiceprint", "delete my voice print"),
}
GREETINGS = _norm_set("هلا", "هلو", "هلاو", "اهلا", "مرحبا", "السلام عليكم", "سلام عليكم", "سلام", "هاي",
                      "صباح الخير", "مساء الخير", "شلونك", "شلونج", "hi", "hello", "hey", "start",
                      "good morning", "good evening", "أهلاً", "how are you", "how r u", "how are u",
                      "how are you doing", "what's up", "whats up", "how's it going")
# A message made only of these words is a hello, «مرحبا شلونج» too.
GREETING_WORDS = _norm_set("هلا", "هلو", "هلاو", "اهلا", "اهلين", "مرحبا", "مرحبتين", "السلام", "سلام", "عليكم",
                           "عليكو", "ورحمة", "الله", "وبركاته", "صباح", "مساء", "الخير", "النور", "شلونك", "شلونج",
                           "شلونكم", "شخبارك", "شخبارج", "هاي", "حبيبي", "حبيبتي", "عيني", "اخي", "اختي",
                           "hi", "hello", "hey", "start", "good", "morning", "evening", "there", "how", "are",
                           "you", "doing")
# «شلونك؟» inside a hello: answered with how we are (folded text, Arabic and English).
HOW_ARE_YOU = ("شلون", "شخبار", "كيف حال", "كيفك", "how are", "how r u", "how you doing", "how's it going")
# During the PIN step: a short «wait» or «ok» keeps the card open and asks for the PIN again.
ACK_WORDS = _norm_set("تمام", "اوك", "اوكي", "ok", "okay", "زين", "هسه", "لحظة", "دقيقة", "ثواني", "ثانية",
                      "انتظر", "انتظري", "اصبر", "اصبري", "ماشي", "طيب", "يلا", "شوية", "حسنا", "لحظات",
                      "wait", "moment", "sure", "one", "a", "sec", "second")
# After a receipt (no card open, nothing asked): thanks get a short answer and never reach the core.
THANK_WORDS = _norm_set("شكرا", "شكر", "تسلم", "تسلمين", "تسلمي", "تسلملي", "مشكور", "مشكورة", "عاشت", "ايدك",
                        "ايدج", "ايدكم", "جزيلا", "كلش", "حبيبي", "حبيبتي", "عيني", "اوك", "تمام", "ok", "thanks",
                        "thank", "you", "thx")
THANK_EMOJI = frozenset({"👍", "🙏", "❤", "👌", "🌹", "️"})
ECHO_KINDS = ("ask", "refuse", "failed")  # «سمعتك: …» only before a question or a refusal
BUTTONS_MAX = 3  # reply buttons under one message; more options than that go in a list
# The notices of an admin's decision on an identity check (accounts.service.kyc_decide), said like the others.
NOTICE_KEYS.update({"kyc_approved": "n_kyc_approved", "kyc_rejected": "n_kyc_rejected"})
NEXT_AFTER = ("done", "refuse")  # replies followed by the «what next» list (how did you check, and the menu)
_NUMBER_ENDING = re.compile(r"\(?[0-9٠-٩][0-9٠-٩ ,.\-]*\)?$")


def fit(text: Any, limit: int) -> str:
    """`text` in at most `limit` characters. A number at its end («…0001», «(4567)», «50,000») is what tells two
    choices apart, so it is never cut: the words before it give way."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    ending = _NUMBER_ENDING.search(text)
    if ending and ending.start() > 0 and len(ending.group()) <= limit - 2:
        tail = ending.group()
        return text[:limit - len(tail) - 1].rstrip() + "…" + tail
    return text[:limit - 1].rstrip() + "…"


def button_titles(options: list[Option], limit: int = BUTTON_TITLE_MAX) -> list[str]:
    """The titles of reply buttons (which have no second line) and of list rows: when two options share a title
    (two contacts saved as «أحمد»), each gets its tail in brackets («أحمد (0001)»), whole."""
    titles = [fit(option.title, limit) for option in options]
    if len(set(titles)) == len(titles):
        return titles
    out = []
    for option in options:
        suffix = f" ({option.tail})" if option.tail else ""
        out.append(fit(option.title, limit - len(suffix)) + suffix if len(suffix) < limit - 1 else fit(option.title, limit))
    return out


def _words(text: str) -> list[str]:
    return names.normalize(text or "").split()


def _all_in(text: str, words: frozenset[str]) -> bool:
    found = _words(text)
    return bool(found) and all(w in words for w in found)


def _baghdad(moment: str | None) -> str:
    if not moment:
        return ""
    try:
        when = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
    except ValueError:
        return ""
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return when.astimezone(BAGHDAD).strftime("%Y-%m-%d %H:%M")


def _last4(phone: Any) -> str:
    return re.sub(r"\D", "", str(phone or ""))[-4:]


def once_per_message(text: str) -> str:
    """«ما انخصم منك شي.» (and its MSA and English forms) kept only where it is said last in this message. A line
    left with nothing else is dropped; blank lines between paragraphs stay."""
    found = list(_NOTHING_TAKEN.finditer(text or ""))
    if len(found) < 2:
        return text
    out, offset = [], 0
    for line in text.split("\n"):
        start, end = offset, offset + len(line)
        offset = end + 1
        cut = [m for m in found[:-1] if start <= m.start() < end]
        if not cut:
            out.append(line)
            continue
        for m in reversed(cut):  # «… البطاقة ولم يُخصم منك شيء. ثم» -> «… البطاقة. ثم»: the sentence still ends
            line = line[:m.start() - start] + ("." if m.group(1) else "") + line[m.end() - start:]
        line = re.sub(r"[ \t]{2,}", " ", line)
        line = re.sub(r"[ \t،,]+\.", ".", line).rstrip()
        line = re.sub(r"^[.\s]+", "", line)
        if not line.strip(" \t،,."):
            continue
        if line.endswith(("،", ",")):
            line = line[:-1] + "."
        elif line[-1] not in ".!?؟:🙂":
            line += "."
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


_DIGIT_FORMS = {str(d): f"[{d}{chr(0x0660 + d)}{chr(0x06F0 + d)}]" for d in range(10)}
_ANY_DIGIT_CHAR = r"0-9٠-٩۰-۹"
_PIN_WORD_BEFORE = re.compile(r"(?:[وب]?(?:ال)?رمز(?:ي|ه|ك|ج)?(?:\s+(?:ال)?سري)?|(?:my\s+)?pin(?:\s+is)?|password)"
                              r"\s*[:=]?\s*$", re.IGNORECASE)
_PIN_WORDS_ONLY = re.compile(r"[وب]?(?:ال)?رمز(?:ي|ه|ك|ج)?|(?:ال)?سري|pin|password|my|is", re.IGNORECASE)


def _without_pin(text: str, pin: str) -> str:
    """`text` with the PIN `pin` (in any digit script) and a PIN word right before it taken out, or "" when nothing
    but the PIN is left. The PIN itself never goes on."""
    pattern = "".join(_DIGIT_FORMS.get(ch, re.escape(ch)) for ch in pin)
    m = re.search(rf"(?<![{_ANY_DIGIT_CHAR}]){pattern}(?![{_ANY_DIGIT_CHAR}])", text or "")
    if m is None:
        return ""
    left = _PIN_WORD_BEFORE.sub("", text[:m.start()]).rstrip(" \t،,:")
    rest = " ".join((left + " " + text[m.end():]).split())
    words = [w for w in re.findall(r"[^\W\d_]{2,}", rest) if not _PIN_WORDS_ONLY.fullmatch(w)]
    return rest if words and pin_in(rest) is None else ""


def _tidy(text: str) -> str:
    """«مساء الخير !» (a greeting with no name) -> «مساء الخير!»."""
    return re.sub(r"[ \t]+([!؟?.،,])", r"\1", text or "")


@dataclass
class VoiceCard:
    """The voice check of one card made from a voice note: the note's speaker vector (to keep, if the card is then paid
    with the PIN) and whether the wallet granted it a payment with no PIN."""

    print_: Any
    granted: bool = False


@dataclass
class Runtime:
    """One account's open conversation: a wallet client over the shared wallet, and an orchestrator."""

    key: str
    user_id: str
    http: TestClient
    wallet: WalletClient
    session: Session
    # The quote id of the card whose «إي، ادفع» was tapped. It outlives a text that voids that card, so a
    # PIN typed straight after is still refused; the next ordinary message clears it.
    awaiting_pin: str | None = None
    unseen_card: bool = False  # a card a newer message replaced before it was sent: its voiding is no news
    via_instagram: bool = False  # this conversation is the account's Instagram chat (replies go out there)
    told_no_voice: bool = False  # Instagram: «no voice replies here» was said once
    # What was checked before each card this chat was sent (quote id -> checks), so «شلون تأكدت؟» under a card
    # explains that card, whatever came after it.
    card_checks: OrderedDict = field(default_factory=OrderedDict)
    demo: bool = False  # a demo account (SIGNUP_MODE=demo): its own PIN is looked for inside every message
    # message digest -> index of the 4-digit token that is this demo account's PIN, or -1 (never the PIN itself)
    pin_memo: OrderedDict = field(default_factory=OrderedDict)
    variants: dict = field(default_factory=dict)  # text key -> how often a fixed line was sent (Channel._variant)
    voice_note: Any = None  # the speaker vector (a Future) of the voice note this turn answers, until a card takes it
    voice_cards: OrderedDict = field(default_factory=OrderedDict)  # quote id -> VoiceCard
    busy: int = 0
    last_used: float = field(default_factory=time.monotonic)
    greeted_at: datetime | None = None  # the last time this chat was greeted (the intro, a hello): not again soon

    def close(self) -> None:
        self.http.close()


class Channel:
    def __init__(self, settings: Settings, graph: Graph, *, core: Any = None, seed: dict | None = None,
                 clock: Callable[[], datetime] | None = None,
                 kyc_reader: Callable[[bytes, str], dict | None] | None = None, kyc_llm: Any = None,
                 instagram: Instagram | None = None, voiceprint: voiceprints.Embedder | None = None):
        """`seed` (tests only) preloads an empty accounts database with synthetic accounts.

        `kyc_reader` reads the photo of an identity document into raw fields (kyc.reader_from over the model
        layer's vision role; a fake in tests). Without one, or without KYC_PEPPER, the identity step is off
        whatever SIGNUP_KYC says. `kyc_llm` is the model layer behind the reader, when its spend should count
        into the daily budget.

        `instagram` (channels/instagram/wiring.py) opens the second door; None keeps the channel WhatsApp only.

        `voiceprint` turns a voice note into a speaker vector (accounts.voiceprint.load(); a fake in tests). Voice
        approval is on only with VOICE_APPROVAL and one: without it every payment needs the PIN, and nothing says
        otherwise."""
        self.settings = settings
        self.graph = graph
        self.kyc_reader = kyc_reader
        self.kyc_llm = kyc_llm
        self.kyc_mode = settings.kyc_mode(kyc_reader is not None)  # off | optional | required, as in force
        if settings.signup_kyc != "off" and self.kyc_mode == "off":  # one line at start: names, never values
            missing = "no usable vision route in the model layer (LLM_VISION)" if kyc_reader is None else \
                "KYC_PEPPER is missing or shorter than 16 bytes"
            log.warning("SIGNUP_KYC=%s but %s: the identity document step is off", settings.signup_kyc, missing)
        self.core = core if core is not None else load_core()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.root = Path(settings.data_dir) / "whatsapp"
        self.store = ChannelStore(self.root / "channel.db")
        self._drop_demo_wallets()
        self.db = Database(settings.accounts_path, seed=seed)
        self.wallet_app = create_wallet(self.db, clock=self.clock)  # in-process only: never mounted on a route
        self.wallet = self.wallet_app.state.wallet
        self.accounts = Accounts(self.db, self.wallet, clock=self.clock)
        billing.ensure_catalogue(self.db)
        self.embedder = voiceprint
        self.voice_approval_on = bool(settings.voice_approval and voiceprint is not None)
        if settings.voice_approval and voiceprint is None:
            log.warning("VOICE_APPROVAL=1 but no speaker model could be loaded: every payment needs the PIN")
        self.wallet.configure_voice(enabled=self.voice_approval_on, threshold=settings.voice_match_threshold,
                                    max_iqd=settings.voice_approval_max_iqd)
        self._voiceprints = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="wa-voiceprint")
                             if self.voice_approval_on else None)
        self.onboarding = Onboarding(self)
        self.demo = Demo(self)
        self.notifier = Notifier(self)
        self.voice = callable(getattr(self.core, "transcribe", None)) or callable(getattr(self.core, "hear", None))
        self.speaks = speech.can_speak(self.core) and callable(getattr(graph, "send_audio", None)) \
            and callable(getattr(graph, "upload_media", None))
        self._open: OrderedDict[str, Runtime] = OrderedDict()
        self._lock = threading.Lock()
        self._spend_lock = threading.Lock()
        self._last_spent = self._core_spent() or 0.0
        self._media: dict[str, Future] = {}
        self._fetch = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wa-media")
        self._outbox: dict[str, list[tuple[Inbound, str]]] = {}  # one sender's texts of the turn now running
        self._held_buttons: dict[str, tuple[Inbound, str, list] | None] = {}  # a sign-up turn's question, sent last
        # sender key -> a demo PIN shown in this turn's intro: for this turn only (the guard, the voice's never=),
        # dropped when the turn ends. It is never written anywhere.
        self._secrets: dict[str, str] = {}
        # sender key -> bubble one of the intro sent in this turn: put in the writer's memory once the runtime is
        # built, so the next reply neither greets again nor repeats the intro. Dropped when the turn ends.
        self._intro_said: dict[str, str] = {}
        self._turn_started: dict[str, float] = {}  # sender -> when its turn began (HUMAN_PACE)
        self._bubbled: set[str] = set()  # senders whose turn has sent its first bubble
        self.typing_every = TYPING_REFRESH_SECONDS
        self.sleep = time.sleep  # the typing pause (HUMAN_PACE); tests swap it
        # The menu as a second message after a hello and after sign-up: Settings.menu_on_hello when it has one, else
        # WHATSAPP_MENU_ON_HELLO. Off unless asked for: a hello gets one message.
        chosen = getattr(settings, "menu_on_hello", None)
        self.menu_on_hello = bool(chosen) if chosen is not None else (
            (os.environ.get("WHATSAPP_MENU_ON_HELLO") or "").strip().lower() in ("1", "true", "yes", "on"))
        self.dispatcher = Dispatcher(self.handle_batch, debounce=settings.debounce_seconds,
                                     ready=lambda item: bool(pin_in(item.text or "")))
        self.instagram = instagram
        if instagram is not None:
            instagram.bind(settings.accounts_path, self._now, settings.service_number)
        self._turns: dict[str, threading.Lock] = {}  # one account's turns, one at a time (WhatsApp and Instagram)
        # «شلون تأكدت؟» under every card: on unless CARD_WHY_BUTTON says otherwise (it is one more message a card).
        self.why_on_cards = (os.environ.get("CARD_WHY_BUTTON") or "1").strip().lower() not in ("0", "false", "no", "off")

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self.dispatcher.close()
        self._fetch.shutdown(wait=False, cancel_futures=True)
        if self._voiceprints is not None:
            self._voiceprints.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            while self._open:
                _, rt = self._open.popitem()
                rt.close()
        self.store.close()
        if self.instagram is not None:
            self.instagram.close()
        self.db.close()

    def join(self, timeout: float = 30.0) -> bool:
        return self.dispatcher.join(timeout)

    def sender_key(self, sender: str) -> str:
        return sender_key(sender, self.settings.sender_secret)

    def _route(self, sender: str) -> tuple[Graph, str]:
        """The client a reply to this sender goes out with, and the address it takes. An Instagram sender is
        answered by the Instagram client, at its Instagram-scoped id; everyone else by WhatsApp, as before."""
        if is_instagram(sender) and self.instagram is not None:
            return self.instagram.client, unscoped(sender)
        return self.graph, sender

    def _mark_read(self, item: Inbound) -> None:
        graph, to = self._route(item.sender)
        if is_instagram(item.sender):
            graph.mark_read(item.msg_id, to=to)  # Instagram marks a conversation, so it needs the sender
        else:
            graph.mark_read(item.msg_id)

    def _media_of(self, item: Inbound) -> tuple[bytes, str]:
        return self._route(item.sender)[0].media(item.media_id)

    def _now(self) -> float:
        return self.clock().timestamp()

    def _day(self) -> str:
        return self.clock().astimezone(BAGHDAD).strftime("%Y-%m-%d")

    def _drop_demo_wallets(self) -> None:
        """The demo's per-sender synthetic wallets: renamed once (an undo window), deleted after a week."""
        old = self.root / "wallets"
        if old.is_dir():
            target = self.root / f"wallets.dropped-{self.clock().strftime('%Y%m%d')}"
            try:
                old.rename(target)
                log.info("demo wallets set aside (%d files)", sum(1 for _ in target.glob("*.db")))
            except OSError as exc:
                log.warning("could not set the demo wallets aside: %s", type(exc).__name__)
        for path in self.root.glob("wallets.dropped-*"):
            try:
                day = datetime.strptime(path.name.rsplit("-", 1)[1], "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if self.clock() - day > timedelta(days=DEMO_KEEP_DAYS):
                shutil.rmtree(path, ignore_errors=True)

    # ------------------------------------------------------------ intake (webhook thread)

    def accept(self, items: list[Inbound], door: str = "whatsapp") -> int:
        """Take parsed webhook messages: drop backlog, strangers (allow-list) and repeats, queue the rest.

        `door` is the route the messages came through ("whatsapp" or "instagram"). It decides, not the sender's text:
        a WhatsApp-door message whose sender reads «ig:…» is dropped, so nobody can type their way into an
        Instagram chat (and past its signature). A voice note's audio starts downloading at once, while the
        sender's earlier turn may still run; on Instagram only for a linked sender."""
        now = self._now()
        taken = 0
        for item in items:
            if item.timestamp and now - item.timestamp > MAX_AGE_SECONDS:
                continue
            linked: str | None = None
            if is_instagram(item.sender) != (door == "instagram"):
                log.warning("dropped a %s-door message with a sender of the other door", door)
                continue
            if door == "instagram":
                if self.instagram is None:
                    continue  # the Instagram door is closed
                linked = self.instagram.linked(item.sender)
                if not self._instagram_let_in(item, linked):
                    continue
            elif not self.settings.allowed(item.sender):
                continue
            demo_mode = self.settings.signup_mode == "demo"  # a demo PIN can hide in a sentence: kept redacted
            if not self.store.first_time(item.msg_id, now, asdict(item), any_four=demo_mode):
                continue  # Meta's retry of a message we already have
            self.store.count(self._day(), "msgs_in")
            fetch = door != "instagram" or linked is not None  # a stranger's audio is never downloaded
            if item.kind in ("voice", "audio") and item.media_id and self.voice and fetch:
                self._media[item.msg_id] = self._fetch.submit(self._media_of, item)
            self.dispatcher.submit(item)
            if item.kind in ("text", "voice", "audio"):  # the typing dots at once: the answer waits for the burst
                self._fetch.submit(self._mark_read, item)
            taken += 1
        if taken:
            self.store.prune(now)
        return taken

    def _instagram_let_in(self, item: Inbound, linked: str | None) -> bool:
        """The allow-list on the Instagram door. It holds numbers, so a linked chat is let in by its account's
        number. A chat that is not linked has no number: with an allow-list it gets silence, as a stranger does
        on WhatsApp, unless the message is a link code (a code is issued only to an allowed WhatsApp number, so a
        real user loses nothing). Without an allow-list everyone is let in, and a stranger is told how to link."""
        if not self.settings.allowed_numbers:
            return True
        if linked is None:
            return item.kind == "text" and looks_like_code(item.text) is not None
        user = self.accounts.get(linked) or {}
        return self.settings.allowed(user.get("phone_e164") or "")

    def replay(self) -> int:
        """After a restart: answer messages that were taken but never answered."""
        count = 0
        for raw in self.store.pending(self._now() - MAX_AGE_SECONDS):
            try:
                item = Inbound(**raw)
            except TypeError:
                continue
            self.dispatcher.submit(item)
            count += 1
        if count:
            log.info("replaying %d unanswered message(s)", count)
        return count

    def tick(self) -> dict[str, int]:
        """The background jobs (every few minutes): expire holds, deliver waiting notices."""
        expired = self.wallet.expire_holds()
        sent = self.notifier.sweep()
        self.accounts.kyc_sweep()  # what a document check left for a sign-up that never finished
        return {"holds_expired": expired, "notices": sent}

    # ------------------------------------------------------------ one batch (worker thread)

    def handle_batch(self, sender: str, batch: list[Inbound]) -> None:
        key = self.sender_key(sender)
        rt: Runtime | None = None
        user: dict | None = None
        everything = list(batch)
        instagram = is_instagram(sender)
        typing: threading.Event | None = None
        try:
            if instagram and self.instagram is None:
                return  # replayed after a restart with the Instagram door closed
            if self._limited(key, batch):
                return
            log.info("turn sender=%s kinds=%s", key[:8], ",".join(item.kind for item in batch))
            self._turn_started[sender] = time.monotonic()
            self._bubbled.discard(sender)
            self._mark_read(batch[-1])
            typing = self._keep_typing(batch[-1])
            if instagram:
                user, batch = self._instagram_turn(key, batch)
                if user is None or not batch:
                    return
            else:
                has_phone = batch[-1].has_phone
                e164 = phones.canonical(sender) if has_phone else None
                # a sender with no number in the webhook (a WhatsApp username) can only have a demo account, kept
                # under a key made from its user id that is never a number (Demo.account_key)
                account_key = e164 or self.demo.account_key(sender, has_phone)
                user = self.accounts.by_phone(account_key)
                if user is not None and user.get("status") == "closed":
                    self._send_text(batch[-1], account_texts.say("account_closed", user.get("gender"),
                                                                 user.get("language")))
                    return
                if self.demo.on and account_key and (user is None or self.demo.pending(key, user)):
                    # one demo opening per account at a time: two sender strings of one number never both make
                    # the intro (the second would replace the PIN the first just showed)
                    with self._turn_lock("demo:" + account_key):
                        user = self.accounts.by_phone(account_key)
                        if user is None or self.demo.pending(key, user):
                            user, batch = self._demo_turn(key, account_key, user, batch)
                    if not batch:  # a hello only: the intro was the answer, and the writer's memory keeps it
                        intro_rt = self._runtime(key, user["id"])  # never `rt`: this turn holds no busy count
                        self._remember_intro(intro_rt)
                        return
                if self.onboarding.needed(key, user):
                    user, batch = self._signup_turn(key, e164, user, batch)
                    if not batch:
                        if self.menu_on_hello and user is not None and not self.onboarding.needed(key, user):
                            self._send_menu(self._runtime(key, user["id"]), everything[-1])  # the account is ready
                        return
            uid = user["id"]
            if not instagram:
                self.accounts.touch_inbound(uid)  # the WhatsApp window: an Instagram message does not open it
            rt = self._runtime(key, uid, instagram=instagram)
            rt.busy += 1
            self._remember_intro(rt)
            with self._turn_lock(uid):  # the account's other chat waits: one turn of one account at a time
                if instagram and not self._still_linked(rt, sender):
                    # cut from WhatsApp while this turn waited for the lock: it is cut for this turn too
                    self._send_text(batch[-1], self.instagram.not_linked(self._lang(rt)))
                    return
                if instagram:
                    self._notices_here(rt, batch[-1])  # waiting notices go first, in the chat the person is in
                else:
                    fresh = self.accounts.get(uid) or user
                    here = None if phones.dialable(fresh.get("phone_e164")) else (batch[-1].sender, batch[-1].has_phone)
                    self.notifier.deliver(fresh, force=True, to=here)  # waiting notices go first
                self.accounts.ensure_bills(uid)
                self._handle(rt, batch)
        except Exception:
            log.exception("turn failed (sender %s)", key[:8])
            if rt is not None:
                self._send_text(batch[-1], self._say(rt, "error"))
            else:
                self._send_text(batch[-1], texts.say("error", "m"))
        finally:
            if typing is not None:
                typing.set()
            self._secrets.pop(key, None)  # a PIN shown in this turn's intro is forgotten with the turn
            self._intro_said.pop(key, None)
            self._turn_started.pop(sender, None)
            self._bubbled.discard(sender)
            if rt is not None:
                rt.busy -= 1
                rt.last_used = time.monotonic()
                try:
                    self.accounts.set_language(rt.user_id, self._lang(rt))
                    self.notifier.after_turn()  # e.g. the payee of the payment just made
                except Exception:
                    log.exception("after-turn work failed")
                self._close_if_dropped(rt)
            for item in everything:
                self._media.pop(item.msg_id, None)
            self.store.done([item.msg_id for item in everything])  # the inbox kept a PIN only as REDACTED

    def _demo_turn(self, key: str, account_key: str, user: dict | None,
                   batch: list[Inbound]) -> tuple[dict, list[Inbound]]:
        """SIGNUP_MODE=demo: the account (made now if there is none), then its intro if it never went out to this
        sender, before anything is answered. What comes back is the batch for the normal turn: all of it, but the
        greetings, which the intro has answered."""
        pin = None
        if user is None:
            user, pin = self.demo.open(key, account_key, batch)
        if self.demo.pending(key, user):
            self._secrets[key], self._intro_said[key] = self.demo.intro(key, user, batch, pin)
            batch = [item for item in batch if not (item.kind == "text" and self._is_hello(item.text))]
        return user, batch

    def _remember_intro(self, rt: Runtime) -> None:
        """The intro just sent joins the writer's memory (digits masked there, never the PIN bubble)."""
        said = self._intro_said.pop(rt.key, None)
        if said:
            rt.greeted_at = self.clock()  # the intro greets: a hello right after it is answered without a second one
        session = rt.session
        if not said or not callable(getattr(session, "restore_talk_history", None)):
            return
        note = "[demo intro sent]" if self._lang(rt) == "en" else "[انرسلت مقدمة الحساب التجريبي]"
        try:
            session.restore_talk_history(rt.user_id, session.talk_history(rt.user_id)
                                         + [{"role": "assistant", "text": f"{note} {said}"}])
        except Exception as exc:  # memory only: never in the way of the turn
            log.info("intro memory failed: %s", type(exc).__name__)

    def _keep_typing(self, item: Inbound) -> threading.Event:
        """The typing dots again every `typing_every` seconds until the returned event is set (the turn ended)."""
        stop = threading.Event()

        def refresh() -> None:
            while not stop.wait(self.typing_every):
                try:
                    self._mark_read(item)
                except Exception as exc:  # cosmetic: never in the way of a reply
                    log.info("typing refresh failed: %s", type(exc).__name__)

        threading.Thread(target=refresh, name="wa-typing", daemon=True).start()
        return stop

    def _turn_lock(self, uid: str) -> threading.Lock:
        with self._lock:
            return self._turns.setdefault(uid, threading.Lock())

    def _still_linked(self, rt: Runtime, sender: str) -> bool:
        """An Instagram chat's link still names this runtime's account (always true for a WhatsApp chat)."""
        if not rt.via_instagram:
            return True
        return self.instagram is not None and self.instagram.linked(sender) == rt.user_id

    def _close_if_dropped(self, rt: Runtime) -> None:
        """A runtime forgotten while a turn still used it (an unlink from inside that turn) is closed once idle."""
        with self._lock:
            dropped = rt.busy == 0 and self._open.get(rt.key) is not rt
        if dropped:
            rt.close()

    # ------------------------------------------------------------ Instagram

    def _instagram_turn(self, key: str, batch: list[Inbound]) -> tuple[dict | None, list[Inbound]]:
        """Who an Instagram turn is about. A linked sender: their account, and the whole batch. A sender that is not
        linked yet: a code in the batch makes the link (what follows it is the account's first turn), a wrong code
        says so, anything else gets how to link. (None, []) when this turn was answered here."""
        ig = self.instagram
        last = batch[-1]
        uid = ig.linked(last.sender)
        rest = batch
        if uid is None:
            uid, rest = self._link_from_instagram(batch)
            if uid is None:
                return None, []
        user = self.accounts.get(uid)
        if user is None:
            self._send_text(last, ig.not_linked(language.detect(last.text or "", language.DEFAULT)))
            return None, []
        gender, lang = user.get("gender") or "m", user.get("language") or language.DEFAULT
        if user.get("status") == "closed":
            self._send_text(last, account_texts.say("account_closed", gender, lang))
            return None, []
        if self.settings.allowed_numbers and not self.settings.allowed(user.get("phone_e164") or ""):
            return None, []  # the allow-list holds numbers: an Instagram chat is let in by its account's number
        if self.onboarding.needed(key, user):  # invited, or a PIN reset: the PIN is set on WhatsApp
            self._send_text(last, ig.whatsapp_only(gender, lang))
            return None, []
        return user, rest

    def _link_from_instagram(self, batch: list[Inbound]) -> tuple[str | None, list[Inbound]]:
        """A sender that is not linked: the first code in the batch is tried. (user id, the messages after the code)
        when it linked; (None, []) when this turn was answered here."""
        ig = self.instagram
        for index, item in enumerate(batch):
            if item.kind != "text":
                continue
            lang = language.detect(item.text or "", language.DEFAULT)
            uid, words = ig.redeem(item.sender, item.text or "", lang)
            if words is not None:
                self._send_text(item, words)
                news = ig.flood_news()  # the brake on a flood of wrong codes has just come on: the admin hears once
                if news and self.settings.manager:
                    self._notify_manager(news)
                return None, []
            if uid is None:
                continue
            self._drop_instagram_chats(uid, keep=self.sender_key(item.sender))  # the newest link wins
            user = self.accounts.get(uid) or {}
            gender, lang = user.get("gender") or "m", user.get("language") or lang
            self._send_text(item, ig_texts.say("link_ok", gender, lang))
            self._tell_whatsapp(user, ig_texts.say("linked_notice", gender, lang, phrase=ig_texts.unlink_phrase(lang)))
            return uid, batch[index + 1:]
        last = batch[-1]
        self._send_text(last, ig.not_linked(language.detect(last.text or "", language.DEFAULT)))
        return None, []

    def _drop_instagram_chats(self, uid: str, keep: str | None = None) -> None:
        """Close the open Instagram conversations of this account (an unlink, or a newer link that replaced the
        old one): their card, question and PIN step go with them, and the PIN step kept in the store too. A turn
        of theirs waiting for the account's lock finds the link gone (handle_batch) and is not run."""
        with self._lock:
            keys = [key for key, rt in self._open.items() if rt.via_instagram and rt.user_id == uid and key != keep]
        for key in keys:
            self.forget(key)
            self.store.update_sender(key, awaiting_pin=None)

    def _tell_whatsapp(self, user: dict, text: str) -> None:
        """A line to the account's WhatsApp chat (the link notice). The code was asked for there minutes ago, so the
        window is open; best effort all the same."""
        phone = user.get("phone_e164")
        if not phones.dialable(phone):  # none, or a username account's key: nothing to send it to
            return
        try:
            self.graph.send_text(phone, True, text)
            self.store.count(self._day(), "msgs_out_text")
        except Exception as exc:
            log.warning("link notice send failed: %s", type(exc).__name__)

    def _notices_here(self, rt: Runtime, item: Inbound) -> None:
        """Waiting notices (money received, a hold claimed) into the Instagram chat the person writes from now. A
        notice that cannot be sent stays pending, for WhatsApp or the next turn."""
        user = self.accounts.get(rt.user_id) or {}
        rows = self.accounts.pending_notices(rt.user_id) if user else []
        if not rows:
            return
        text = "\n".join(self.notifier.lines(user, rows))
        if text:
            graph, to = self._route(item.sender)
            try:
                graph.send_text(to, False, text)
                self.store.count(self._day(), "msgs_out_text")
            except Exception as exc:
                log.warning("notice send failed: %s", type(exc).__name__)
                return
        self.accounts.mark_delivered([row["id"] for row in rows])

    def _link_command(self, text: str) -> str | None:
        """«اربط انستغرام» / «افصل انستغرام» (or in English), when the Instagram door is open."""
        if self.instagram is None:
            return None
        if ig_texts.wants_unlink(text):
            return "unlink"
        if ig_texts.wants_link(text):
            return "link"
        return None

    def _link_action(self, rt: Runtime, item: Inbound, action: str) -> None:
        """A link code (only in the WhatsApp chat, where the number is the account), or the link cut (from either
        chat). Nothing else is touched: an open card stays open."""
        st = rt.session.state(rt.user_id)
        st.lang = ig_texts.request_language(item.text, language.detect(item.text, st.lang))
        gender, lang = self._gender(rt), self._lang(rt)
        if action == "unlink":
            self._send_text(item, self.instagram.unlink(rt.user_id, gender, lang))
            self._drop_instagram_chats(rt.user_id)  # cut at once: the Instagram chat's open card goes with it
        elif rt.via_instagram:
            self._send_text(item, ig_texts.say("already_linked", gender, lang))
        else:
            self._send_text(item, self.instagram.issue(rt.user_id, gender, lang))

    def _limited(self, key: str, batch: list[Inbound]) -> bool:
        now = self._now()
        count = 0
        for _ in batch:
            count = self.store.hit(key, now)
        limit = self.settings.max_msgs_per_user_hour
        if limit <= 0 or count <= limit:
            return False
        row = self.store.sender(key) or {}
        notice = row.get("limit_notice")
        self.store.count(self._day(), "rate_limited")
        if not notice or now - float(notice) > 3600:
            if row:
                self.store.update_sender(key, limit_notice=now)
            user = self._account_of(batch[-1])
            lang = (user or {}).get("language") or language.detect(batch[-1].text or "", language.DEFAULT)
            self._send_text(batch[-1], texts.say("rate_limited", (user or {}).get("gender") or "m", lang, limit=limit))
            if not row:
                self.store.add_sender(key, (user or {}).get("id") or "", now)
                self.store.update_sender(key, limit_notice=now)
        return True

    def _account_of(self, item: Inbound) -> dict | None:
        """The account a message is from, before its turn: by the link on Instagram, by the number on WhatsApp."""
        if is_instagram(item.sender):
            uid = self.instagram.linked(item.sender) if self.instagram is not None else None
            return self.accounts.get(uid) if uid else None
        return self.accounts.by_phone(phones.canonical(item.sender))

    def _signup_turn(self, key: str, e164: str | None, user: dict | None,
                     batch: list[Inbound]) -> tuple[dict | None, list[Inbound]]:
        """A burst during sign-up: one answer, holding only the question still open. What is left of the batch once the
        account is ready comes back, for the ordinary turn."""
        sender = batch[-1].sender
        box: list[tuple[Inbound, str]] = []
        self._outbox[sender], self._held_buttons[sender] = box, None
        rest: list[Inbound] = []
        try:
            for index, item in enumerate(batch):
                if index and self.onboarding.says_nothing_new(key, item):
                    continue  # the offer is on its way already
                before, said, held = self.onboarding.step(key), len(box), self._held_buttons[sender]
                self._held_buttons[sender] = None
                self.onboarding.handle(key, item, user)
                user = self.accounts.by_phone(e164)
                if self.onboarding.step(key) != before:
                    del box[:said]  # what came before asked a question this message has just answered
                elif self._held_buttons[sender] is None:
                    self._held_buttons[sender] = held
                if not self.onboarding.needed(key, user):
                    rest = batch[index + 1:]
                    break
        finally:
            self._send_signup_turn(sender)
        return user, rest

    def _send_signup_turn(self, sender: str) -> None:
        held = self._held_buttons.pop(sender, None)
        box = self._outbox.get(sender) or []
        box[:] = list({text: (item, text) for item, text in box}.values())  # the same words are said once
        if held is not None:
            item, body, buttons = held
            joined = "\n\n".join([text for _, text in box] + [body])
            if len(joined) <= MAX_BUTTON_BODY:
                box.clear()
                body = joined
            self._send_outbox(sender)
            self._post_buttons(item, body, buttons)
        self._send_outbox(sender)
        self._outbox.pop(sender, None)

    def _handle(self, rt: Runtime, batch: list[Inbound]) -> None:
        sender = batch[-1].sender
        self._outbox[sender] = []  # however many messages this turn holds, its texts go out as one
        try:
            buffer: list[Inbound] = []
            for item in batch:
                if not self._still_linked(rt, item.sender):
                    buffer = []  # «افصل انستغرام» in this burst: what came after it is not this account's any more
                    break
                if item.kind == "text" and not self._standalone(rt, item.text):
                    buffer.append(item)  # a burst of texts is read as one message
                    continue
                self._flush(rt, buffer)
                buffer = []
                self._one(rt, item)
            self._flush(rt, buffer)
            self._account_spend()
        finally:
            self._send_outbox(sender)
            self._outbox.pop(sender, None)

    def _standalone(self, rt: Runtime, text: str) -> bool:
        """A command, or a message the channel answers itself (a PIN, small talk during the PIN step, an open
        account step): handled on its own, never joined with other texts."""
        manage = rt.session.manage
        return (self._command_of(text) is not None or self._guard(rt, text) is not None
                or self._link_command(text) is not None
                or (manage is not None and manage.busy(rt.user_id))
                or Session.wants_explanation(text) or Session.wants_menu(text) or self._verify_command(rt, text))

    @staticmethod
    def _is_hello(text: str) -> bool:
        return names.normalize(text) in GREETINGS or _all_in(text, GREETING_WORDS)

    def _guard(self, rt: Runtime, text: str) -> tuple[str, str] | None:
        """What the channel answers itself, before a text can reach the orchestrator (and the core).

        - the card was tapped and is still open: a PIN goes to _pin; «نسيت الرمز» says the PIN is secret and the
          card is still open; «لحظة» / «تمام» asks for the PIN again. The card stays open in all three;
        - a card is open but was not tapped: a PIN gets «tap first» and the same card again;
        - the tapped card was voided (by a new request, or a restart): a PIN is refused, never read;
        - no card: a PIN named as one («الرمز 5678»), or 4 to 6 bare digits with nothing asked, is never read;
          thanks get a short answer.
        None for anything else: the orchestrator's turn. A bare number answering our question («شكد؟» ->
        «50000») goes on (the core reads digits locally, never sends them).
        """
        st = rt.session.state(rt.user_id)
        manage = rt.session.manage
        if manage is not None and manage.wants_pin(rt.user_id):
            return None  # the change-PIN step: manage reads it, never the orchestrator
        card_qid = st.card.quote.get("id") if st.card is not None else None
        found = pin_in(text, self._secrets.get(rt.key) or "") or self._own_pin(rt, text)
        tapped = rt.awaiting_pin
        if card_qid is not None and tapped == card_qid:
            if found:
                return "pin", found[1]
            if mentions_pin(text):
                return "pin_hint", ""
            if _all_in(text, ACK_WORDS):
                return "pin_wait", ""
            return None
        if card_qid is not None:
            return ("tap_first", "") if found else None
        if found and tapped is not None and (found[0] != "bare" or len(found[1]) <= 6):
            return "pin_stale", ""
        if found and found[0] == "demo":  # «دز لزينب 5000 والرمز 4719»: the PIN is cut out, the request goes on
            rest = _without_pin(text, found[1])
            if rest and not _all_in(rest, ACK_WORDS) and not self._is_thanks(rest):  # «4719 تمام»: only the PIN
                return "pin_in_request", rest
        if found and found[0] != "bare":
            return "pin_no_card", ""
        idle = st.question is None and not st.queue and (manage is None or not manage.busy(rt.user_id))
        if found and idle and len(found[1]) <= 6:  # a PIN typed with nothing open: never read, never sent on
            return "pin_no_card", ""
        if st.question is None and not st.queue and tapped is None and self._is_thanks(text):
            return "thanks", ""
        return None

    def _own_pin(self, rt: Runtime, text: str) -> tuple[str, str] | None:
        """A demo account's PIN anywhere in a message («دز لأخوي 5000 والرمز 4719»), checked against the account's
        hash: ("demo", digits), else None. Only for demo accounts; what is remembered between the calls of one
        message is which token matched, never the PIN."""
        if not rt.demo:
            return None
        tokens = list(dict.fromkeys(t for t in re.findall(r"\d+", fold(text or "")) if len(t) == DEMO_PIN_LEN))
        if not tokens:
            return None
        # Each check is a slow hash (PBKDF2): a message full of 4-digit numbers must not tie up a worker. A PIN
        # sent to pay is one number in a short message; only the first few are checked.
        tokens = tokens[:DEMO_PIN_CHECKS]
        pin_hash = (self.accounts.get(rt.user_id) or {}).get("pin_hash") or ""
        digest = hashlib.sha256(f"{pin_hash}\0{text}".encode()).hexdigest()
        index = rt.pin_memo.get(digest)
        if index is None:
            index = next((i for i, token in enumerate(tokens) if verify_pin(token, pin_hash)), -1)
            rt.pin_memo[digest] = index
            while len(rt.pin_memo) > 16:
                rt.pin_memo.popitem(last=False)
        return ("demo", tokens[index]) if index >= 0 else None

    @staticmethod
    def _is_thanks(text: str) -> bool:
        raw = (text or "").strip()
        if raw and all(ch in THANK_EMOJI or ch.isspace() for ch in raw):
            return True
        return _all_in(text, THANK_WORDS)

    def _flush(self, rt: Runtime, items: list[Inbound]) -> None:
        text = "\n".join(item.text.strip() for item in items if item.text.strip())
        if text:
            self._text(rt, items[-1], text)

    def _one(self, rt: Runtime, item: Inbound) -> None:
        if item.kind == "text":
            command = self._command_of(item.text)
            guard = self._guard(rt, item.text) if command is None else None
            link = self._link_command(item.text) if command is None and guard is None else None
            if link is not None:
                self._link_action(rt, item, link)
            elif command is None and self._verify_command(rt, item.text):
                self._verify_start(rt, item, item.text)
            elif command is not None:
                self._command(rt, item, command)
            elif guard is not None:
                self._guarded(rt, item, *guard)
            else:
                self._text(rt, item, item.text.strip())
        elif item.kind == "button":
            self._button(rt, item)
        elif item.kind in ("voice", "audio"):
            self._voice(rt, item)
        elif item.kind == "reaction":
            return  # a 👍 on a receipt is not a request
        elif item.kind == REDACTED:
            self._lost(rt, item)
        elif item.kind == "image":
            self._image(rt, item)
        elif item.kind in ("sticker", "location"):
            fallback = self._say(rt, self._variant(rt, f"unsupported_{item.kind}"), voice_or=self._voice_or(rt))
            self._send_text(item, self._talk(rt, "unsupported_media", fallback, facts={"media": item.kind}))
        else:
            fallback = self._say(rt, self._variant(rt, "unsupported_other"), voice_send=self._voice_send(rt))
            self._send_text(item, self._talk(rt, "unsupported_media", fallback, facts={"media": item.kind}))

    # ------------------------------------------------------------ the identity document, after sign-up

    def _verify_command(self, rt: Runtime, text: str) -> bool:
        """«وثّق حسابي» / "verify my account". With the identity step off, only an account that has a level is
        answered here: for everyone else nothing changed, and the words go on as they did."""
        if not self.onboarding.wants_verification(text):
            return False
        return self.kyc_mode != "off" or (self.accounts.get(rt.user_id) or {}).get("kyc_level") is not None

    def _verify_start(self, rt: Runtime, item: Inbound, text: str = "") -> None:
        st = rt.session.state(rt.user_id)
        if text:
            st.lang = language.detect(text, st.lang)  # "verify my account" is answered in English
        if rt.via_instagram:  # the document is read only where the number is the account
            self._send_text(item, self.instagram.whatsapp_only(self._gender(rt), self._lang(rt)))
            return
        user = self.accounts.get(rt.user_id) or {}
        self.onboarding.start_verification(rt.key, item, user, self._lang(rt), self._gender(rt))

    def _cancels_verification(self, rt: Runtime, text: str) -> bool:
        """«الغاء» while the photo is awaited and nothing else is open (a card or a question keeps its own «لا»)."""
        if names.normalize(text) not in STEP_CANCEL:
            return False
        st, manage = rt.session.state(rt.user_id), rt.session.manage
        if st.card is not None or st.question is not None or st.queue or (manage is not None and manage.busy(rt.user_id)):
            return False
        return self.onboarding.verifying(rt.key) is not None

    def _image(self, rt: Runtime, item: Inbound) -> None:
        """A photo from an account. It is read only while its «verify my account» step is open; at any other
        moment one line says so. With the identity step off and no level on the account: the old words."""
        user = self.accounts.get(rt.user_id) or {}
        gender, lang = self._gender(rt), self._lang(rt)
        row = self.onboarding.verifying(rt.key) if self.kyc_mode != "off" else None
        if row is not None:
            self.onboarding.verify_photo(rt.key, item, row, user, lang, gender)
            return
        if self.kyc_mode == "off" and user.get("kyc_level") is None:
            fallback = self._say(rt, self._variant(rt, "unsupported_image"), voice_or=self._voice_or(rt))
            self._send_text(item, self._talk(rt, "unsupported_media", fallback, facts={"media": "image"}))
            return
        lines = [account_texts.say("kyc_photo_not_now", gender, lang)]
        if self.kyc_mode != "off" and user.get("kyc_level") in ("none", "pending"):
            lines.append(account_texts.say("kyc_verify_how", gender, lang))
        self._send_text(item, "\n".join(lines))

    # ------------------------------------------------------------ turns

    def _text(self, rt: Runtime, item: Inbound, text: str, *, source: str = "text", heard: Any = None,
              voice_note: bool = False) -> None:
        if Session.wants_explanation(text):  # «ليش»: said from what was recorded. No model, and a card stays open
            st = rt.session.state(rt.user_id)
            st.lang = Session.command_language(text, st.lang)  # "why" and "details" are answered in English
            self._explain(rt, item)
            return
        if self._resting(rt, item):
            return
        st = rt.session.state(rt.user_id)
        st.lang = language.detect(text, st.lang)
        if voice_note and self._verify_command(rt, text):  # said, not typed: the same step
            self._verify_start(rt, item)
            return
        if voice_note and self._command_of(text) == "voice_delete":  # «امسح صوتي» said, not typed
            self._set_awaiting(rt, None)
            self._voice_delete(rt, item, text)
            return
        if self._cancels_verification(rt, text):
            self.onboarding.cancel_verification(rt.key, item, self._lang(rt), self._gender(rt))
            return
        if self._is_hello(text) and st.card is None and st.question is None:
            self._set_awaiting(rt, None)
            self._welcome(rt, item, text)
            return
        manage = rt.session.manage
        if rt.via_instagram and manage is not None and pin_command(text) is not None:
            # changing or resetting the PIN, the only payment factor, stays where the number is the account
            st.lang = command_language(text, st.lang)
            self._send_text(item, self.instagram.whatsapp_only(self._gender(rt), self._lang(rt)))
            return
        if manage is not None:
            idle = st.card is None and st.question is None and not st.queue
            lang = command_language(text, st.lang)
            out = manage.try_handle(rt.user_id, text, lang, st.gender, idle=idle)
            if out is not None:
                st.lang = lang
            if out is not None:
                self._set_awaiting(rt, None)
                self._manage_out(rt, item, out, voice_note=voice_note, heard=text if voice_note else None)
                return
        if not (rt.awaiting_pin and st.card is not None and st.card.quote.get("id") == rt.awaiting_pin):
            self._set_awaiting(rt, None)  # the user moved on: the PIN step is over
        # else: a new request while the tapped card is open. The orchestrator voids that card; the PIN
        # step is kept one more message, so a PIN typed straight after is refused (_guard), never read.
        reply = rt.session.handle_text(rt.user_id, text, source=source, heard=heard)
        if rt.unseen_card:  # the user never saw that card, so being told it was cancelled means nothing
            reply.lines = [line for line in reply.lines if line != self._render(rt, "card_voided")]
            rt.unseen_card = False
        if not voice_note and self._overtaken(rt, item, reply):
            return
        if voice_note:
            reply.heard = text
        self._maybe_voice(rt, item, reply, user_sent_voice=voice_note)
        if voice_note and reply.kind in ECHO_KINDS:
            reply.lines = [self._say(rt, "heard", text=text)] + reply.lines
        self._send_reply(rt, item, reply)

    def _overtaken(self, rt: Runtime, item: Inbound, reply: Reply) -> bool:
        """The user wrote again while this was being worked out, so the next turn answers both. Only a question or a
        card can wait: the state keeps them, and a refusal, a failure or a result is always said. A message the channel
        answers itself (a PIN, a command) does not read this reply, so it still goes out."""
        if reply.kind not in ("ask", "card"):
            return False
        waiting = self.dispatcher.waiting(item.sender)
        if not waiting or any(next_one.kind != "text" or self._standalone(rt, next_one.text) for next_one in waiting):
            return False
        rt.unseen_card = reply.kind == "card"
        return True

    def _manage_out(self, rt: Runtime, item: Inbound, out: ManageReply, *, voice_note: bool = False,
                    heard: str | None = None) -> None:
        if out.send_to:
            reply = rt.session.start_transfer(rt.user_id, out.send_to)
            self._maybe_voice(rt, item, reply, user_sent_voice=voice_note)
            self._send_reply(rt, item, reply)
            return
        reply = Reply(list(out.lines), kind=out.kind, lang=self._lang(rt))
        self._maybe_voice(rt, item, reply, user_sent_voice=voice_note, never=(out.secret,) if out.secret else ())
        lines = list(out.lines)
        if heard and out.kind in ECHO_KINDS:
            lines = [self._say(rt, "heard", text=heard)] + lines
        if out.buttons:
            self._send_buttons(item, "\n".join(lines), out.buttons)
        elif lines:
            self._send_text(item, "\n".join(lines))

    def _welcome(self, rt: Runtime, item: Inbound, text: str | None = None) -> None:
        """A hello: greeted back by the time of day, with the balance at the first hello of a Baghdad day only. The
        session's writer may word it (situation "hello"); the fixed line is its fallback."""
        hour = self.clock().astimezone(BAGHDAD).hour
        lang = self._lang(rt)
        user = self.accounts.get(rt.user_id) or {}
        first = (user.get("name") or "").split(" ")[0]
        if rt.demo and user.get("name") in (account_texts.say("demo_guest", None, "iq"),
                                            account_texts.say("demo_guest", None, "en")):
            first = ""  # a demo account with no usable profile name is not greeted as «ضيف»
        day = self._day()
        first_today = (self.store.sender(rt.key) or {}).get("hello_day") != day
        if first_today:
            self.store.update_sender(rt.key, hello_day=day)
        balance = int(user.get("balance") or 0)
        said = item.text if text is None else text  # the message as the turn read it (a PIN already cut out)
        hello = texts.answer_salam(said, texts.hello(hour, lang), lang)  # «السلام عليكم» gets its answer
        if first_today:
            key = "welcome_back"
        else:  # later hellos of the day: other wordings in turn, and «شلونك» is answered
            asked = any(cue in fold(said or "") for cue in HOW_ARE_YOU)
            base = "welcome_how" if asked else "welcome_again"
            greeted = rt.greeted_at is not None and self.clock() - rt.greeted_at < timedelta(seconds=IDLE_SECONDS)
            if greeted:  # greeted a moment ago (the intro, a hello): the wordings with no «صباح الخير» in them
                plain = [k for k in (f"{base}_2", f"{base}_3") if k in account_texts.IQ]
                n = rt.variants.get(base, 0)
                rt.variants[base] = n + 1
                key = plain[n % len(plain)]
            else:
                key = self._variant(rt, base, lambda k: k in account_texts.IQ)
        fallback = _tidy(account_texts.say(key, self._gender(rt), lang, hello=hello, first=first, balance=balance))
        facts = {"name": first} if first else {}
        if first_today:
            facts["balance"] = balance  # a money fact: the template keeps its own «دينار» after it
        self._send_text(item, self._talk(rt, "hello", fallback, text=said, facts=facts,
                                         required=("balance",) if first_today else ()))
        rt.greeted_at = self.clock()
        if self.menu_on_hello:
            self._send_menu(rt, item)

    def _send_menu(self, rt: Runtime, item: Inbound) -> None:
        """The main menu as a list, under a line of its own (after a greeting, after sign-up)."""
        menu = rt.session.menu(rt.user_id)
        self._send_choices(rt, item, self._say(rt, "menu_body"), menu.options, menu.options_title, as_list=True)

    def _explain(self, rt: Runtime, item: Inbound) -> None:
        """«شلون تأكدت؟»: the last turn's checks as text. Nothing else is touched: an open card stays open, and so
        does its PIN step."""
        reply = rt.session.explain(rt.user_id)
        self._send_text(item, "\n".join(str(line) for line in reply.lines if str(line).strip()))

    def _explain_card(self, rt: Runtime, item: Inbound, qid: str) -> None:
        """The tap on «شلون تأكدت؟» under one card: what was checked before that card, from what was recorded when
        it was sent, while it is the open card. A card no longer open (paid, cancelled, replaced, expired) is not
        explained with some other request's checks: one line says so, and «ليش» still explains the last request.
        Nothing else is touched: an open card stays open, and so does its PIN step."""
        st = rt.session.state(rt.user_id)
        open_qid = st.card.quote.get("id") if st.card is not None else None
        if not qid or open_qid != qid:
            self._send_text(item, self._say(rt, "card_why_closed"))
            return
        checks = rt.card_checks.get(qid)
        if checks is None:  # the open card, sent before a restart: its record went with the old process
            self._explain(rt, item)
            return
        if checks:
            lines = [self._say(rt, "card_why_title")] + [f"• {line}" for line in rt.session.why_lines(rt.user_id, checks)]
        else:
            lines = [self._render(rt, "why_nothing")]
        self._send_text(item, "\n".join(lines))

    def _option(self, rt: Runtime, item: Inbound) -> None:
        """A tapped option or list row. The orchestrator looks the id up against this person's open question (or
        the menu); the title that came with the tap is never read. No option pays: money moves only through
        _button's «confirm:<quote id>» and the PIN."""
        option_id = item.button_id
        if option_id == EXPLAIN_OPTION:
            self._explain(rt, item)
            return
        if self._resting(rt, item):
            return
        st = rt.session.state(rt.user_id)
        if not (rt.awaiting_pin and st.card is not None and st.card.quote.get("id") == rt.awaiting_pin):
            self._set_awaiting(rt, None)  # as with a text: a tapped card voided now still refuses a PIN typed next
        reply = rt.session.handle_option(rt.user_id, option_id)
        if option_id == MENU_HELP_OPTION and reply.lines:  # this channel's own help: its commands, not the short one
            reply.lines[-1] = account_texts.say("help", self._gender(rt), self._lang(rt))
        if rt.unseen_card:
            reply.lines = [line for line in reply.lines if line != self._render(rt, "card_voided")]
            rt.unseen_card = False
        self._maybe_voice(rt, item, reply)
        self._send_reply(rt, item, reply)

    def _guarded(self, rt: Runtime, item: Inbound, action: str, pin: str) -> None:
        """Answer a message that _guard kept away from the orchestrator."""
        if action != "pin":  # a bare PIN carries no language: every other guarded line follows this message's
            st = rt.session.state(rt.user_id)
            st.lang = language.detect(item.text or "", st.lang)
        if action == "pin":
            self._pin(rt, item, pin)
            return
        if action == "pin_hint":
            manage = rt.session.manage
            if rt.demo and manage is not None and not rt.via_instagram and pin_command(item.text) == "forgot":
                # a demo account that lost its PIN mid-payment: a fresh one now, and the card stays open for it
                out = manage.demo_pin(rt.user_id, self._lang(rt), self._gender(rt))
                self._send_text(item, "\n".join(out.lines + [self._say(rt, "pin_still_open")]))
                return
            self._send_text(item, "\n".join([account_texts.say("pin_is_secret", self._gender(rt), self._lang(rt)),
                                             self._say(rt, "pin_still_open")]))
        elif action == "pin_wait":
            self._send_text(item, self._say(rt, "pin_wait"))
        elif action == "tap_first":
            st = rt.session.state(rt.user_id)
            self._send_text(item, self._say(rt, "tap_first"))
            if st.card is not None:  # the same card again, so its buttons are right under the hint
                self._send_card(rt, item, Reply([], card=st.card.card, kind="card", state="QUOTED",
                                                quote_id=st.card.quote.get("id")), [], why=False)
        elif action == "pin_stale":
            self._stale_pin(rt, item)
        elif action == "pin_no_card":
            self._send_text(item, self._say(rt, "pin_no_card"))
        elif action == "pin_in_request":  # `pin` holds the message with the PIN cut out: it never pays from here
            self._send_text(item, self._say(rt, "pin_no_card"))
            self._text(rt, item, pin)
        elif action == "thanks":
            self._send_text(item, self._talk(rt, "thanks", self._say(rt, self._variant(rt, "thanks")), text=item.text))

    def _stale_pin(self, rt: Runtime, item: Inbound) -> None:
        """A PIN for a card that is no longer open. When that card was paid after all (the wallet committed,
        then a restart lost the reply), say so with its receipt: never «nothing was taken»."""
        qid = rt.awaiting_pin or ""
        self._set_awaiting(rt, None)
        quote = self._quote(rt, qid)
        if quote and quote.get("user_id") == rt.user_id and quote.get("state") == "SUCCEEDED":
            receipt = self._receipt(rt, qid)
            if receipt:
                self._send_text(item, "\n".join([self._say(rt, "paid_once"), "", receipt]))
                return
        self._send_text(item, self._render(rt, "stale_card"))

    def _lost(self, rt: Runtime, item: Inbound) -> None:
        """A message the inbox kept without its text (it carried a PIN or digits) and a restart replayed."""
        if rt.awaiting_pin:
            self._stale_pin(rt, item)
        else:
            self._send_text(item, self._say(rt, "message_lost"))

    def _button(self, rt: Runtime, item: Inbound) -> None:
        if Session.is_option(item.button_id):  # «opt_…»: never a card button, which is «<action>:<quote id>»
            self._option(rt, item)
            return
        action, _, qid = item.button_id.partition(":")
        if action == EXPLAIN_OPTION:  # «شلون تأكدت؟» under one card: «opt_why:<quote id>»
            self._explain_card(rt, item, qid)
            return
        if action == "m":
            sub, _, arg = qid.partition(":")
            if sub == "verify":  # «وثّق حسابي» under «حسابي»
                self._verify_start(rt, item)
                return
            if sub == "forgot" and rt.via_instagram:  # a PIN reset request stays on WhatsApp, as the typed one does
                self._send_text(item, self.instagram.whatsapp_only(self._gender(rt), self._lang(rt)))
                return
            manage = rt.session.manage
            st = rt.session.state(rt.user_id)
            out = manage.button(rt.user_id, sub, arg, self._lang(rt), st.gender) if manage else None
            if out is None:
                self._text(rt, item, item.text)
            else:
                self._manage_out(rt, item, out)
            return
        if action not in ("confirm", "change", "cancel") or not qid:
            self._text(rt, item, item.text)
            return
        if action == "confirm":
            if self._expired(rt, qid):  # refuse at once: never ask for a PIN on a dead card
                self._drop_card(rt, qid)
                self._set_awaiting(rt, None)
                self._send_text(item, self._render(rt, "quote_expired"))
                return
            if self._voice_granted(rt, qid):
                self._voice_confirm(rt, item, qid)
                return
            reply = rt.session.handle_button(rt.user_id, qid, "confirm", pin=None)
            if reply.kind == "card" and reply.quote_id == qid:  # the card is open: now the PIN
                self._set_awaiting(rt, qid)
                self._send_text(item, self._say(rt, "ask_pin"))
                return
            receipt = self._receipt(rt, qid)
            if receipt:  # an old card scrolled back to and tapped again: paid once, here is its receipt
                notes = [line for line in reply.lines
                         if str(line).strip() and line != self._render(rt, "card_already_paid")]
                self._send_text(item, "\n".join(notes + [self._say(rt, "paid_once"), "", receipt]))
                return
            self._send_reply(rt, item, reply)  # a stale card: the orchestrator's words
            return
        self._set_awaiting(rt, None)
        reply = rt.session.handle_button(rt.user_id, qid, action)
        self._maybe_voice(rt, item, reply)
        self._send_reply(rt, item, reply)

    def _pin(self, rt: Runtime, item: Inbound, pin: str) -> None:
        """The PIN typed as a message after «إي، ادفع». Only this tap + PIN can move money."""
        qid = rt.awaiting_pin or ""
        reply = rt.session.handle_button(rt.user_id, qid, "confirm", pin=pin)
        if reply.kind == "card" and reply.quote_id == qid:  # wrong PIN: nothing moved, the card stays open
            notes = [line for line in reply.lines if line != self._render(rt, "wrong_pin")]  # earlier outcomes stay
            self._send_text(item, "\n".join(notes + [self._say(rt, "wrong_pin")]))
            return
        self._set_awaiting(rt, None)
        if reply.kind == "failed":
            self._manager_blocked(rt)
        kept = self._keep_voice(rt, qid)  # a card made from a voice note, paid with the PIN: its voice is kept
        self._maybe_voice(rt, item, reply)
        self._send_reply(rt, item, reply, paid_qid=qid, typed_pin=True, after_receipt=kept)

    def _voice_confirm(self, rt: Runtime, item: Inbound, qid: str) -> None:
        """«إي، ادفع» on a card whose voice note matched: paid with no PIN, if the wallet still finds this quote's
        grant valid. If it does not, nothing moved and the PIN is asked for, as on any card."""
        rt.voice_cards.pop(qid, None)  # one tap: a second one is an ordinary tap
        reply = rt.session.handle_button(rt.user_id, qid, "confirm", voice=True)
        if reply.kind == "card" and reply.quote_id == qid:
            self._set_awaiting(rt, qid)
            self._send_text(item, "\n".join([self._say(rt, "voice_fell_back"), self._say(rt, "ask_pin")]))
            return
        self._set_awaiting(rt, None)
        if reply.kind == "failed":
            self._manager_blocked(rt)
        self._maybe_voice(rt, item, reply)
        self._send_reply(rt, item, reply, paid_qid=qid)

    def _manager_blocked(self, rt: Runtime) -> None:
        user = self.accounts.get(rt.user_id) or {}
        if self.settings.manager and user.get("pin_state") == "blocked":
            self._notify_manager(account_texts.say("manager_pin_blocked", name=user.get("name") or "",
                                                   number=phones.display(user.get("phone_e164"))))

    def _voice(self, rt: Runtime, item: Inbound) -> None:
        if not self.voice:
            self._send_text(item, self._say(rt, "voice_needs_full"))
            return
        manage = rt.session.manage
        if manage is not None and manage.wants_pin(rt.user_id):  # a PIN is never said out loud
            self._send_text(item, account_texts.say("voice_in_signup", self._gender(rt), self._lang(rt)))
            return
        if self._pin_step_open(rt):
            # The PIN is asked for: this note may carry it. It is not heard (its audio never leaves the service),
            # it pays nothing, and the card stays open for the PIN typed as a message.
            self._send_text(item, self._say(rt, "pin_by_voice"))
            return
        if self._resting(rt, item):
            return
        try:
            pending = self._media.pop(item.msg_id, None)
            audio, mime = pending.result(timeout=30) if pending is not None else self._media_of(item)
        except Exception as exc:
            log.warning("voice note download failed: %s", type(exc).__name__)
            self._send_text(item, self._render(rt, "voice_unclear"))
            return
        mime = (mime or item.mime or "audio/ogg").split(";")[0].strip() or "audio/ogg"
        print_job = self._voiceprint_job(rt, item, audio, mime)  # made while the note is heard
        heard, intent = rt.session.hear(rt.user_id, audio, mime)
        del audio  # what is kept of the note is its words and, for voice approval, its speaker vector
        if heard is None or not heard.strip():
            self._set_awaiting(rt, None)
            reply = rt.session.voice_failed(rt.user_id, "voice_unavailable" if heard is None else "voice_unclear")
            self._maybe_voice(rt, item, reply, user_sent_voice=True)
            self._send_reply(rt, item, reply)
            return
        heard = heard.strip()
        written = phones.spoken_digits(heard)  # a number said digit by digit is a number, not a name
        if written != heard:
            heard, intent = written, None  # the core reads the new text (its number masked first)
        guard = self._guard(rt, heard)
        if guard is not None:
            # A PIN said out loud («الرمز 1234», «1234.», «PIN 1234») is never sent to the core, never read as an
            # amount, never said back, the transcript is not echoed, and it never pays: only a typed PIN does.
            if guard[0] == "pin":
                self._send_text(item, self._say(rt, "pin_by_voice"))
            else:
                self._guarded(rt, item, *guard)
            return
        rt.voice_note = print_job  # the first card this note makes takes it (_bind_voice)
        try:
            self._text(rt, item, heard, source="voice", heard=intent, voice_note=True)
        finally:
            rt.voice_note = None

    # ------------------------------------------------------------ voice approval

    def _voiceprint_job(self, rt: Runtime, item: Inbound, audio: bytes, mime: str) -> Future | None:
        """Start the speaker vector of a voice note, when it may count for voice approval: the feature on, a voice
        note (WhatsApp's own, not an audio file), not forwarded, in the WhatsApp chat (where the number is the
        account)."""
        if not self.voice_approval_on or self._voiceprints is None:
            return None
        if item.kind != "voice" or item.forwarded or rt.via_instagram or is_instagram(item.sender):
            return None
        try:
            return self._voiceprints.submit(self.embedder.embed, audio, mime)
        except RuntimeError:  # shutting down
            return None

    def _bind_voice(self, rt: Runtime, qid: str) -> None:
        """The first card a voice note made: the wallet compares the note's voice with the account's kept one and
        grants this quote a payment with no PIN when it matches and every rule holds. The vector is remembered with
        the card either way, so a PIN payment of the card can keep it (_keep_voice)."""
        job, rt.voice_note = rt.voice_note, None
        if job is None or not qid:
            return
        try:
            print_ = job.result(timeout=VOICEPRINT_WAIT)
        except Exception as exc:
            log.warning("voiceprint not ready: %s", type(exc).__name__)
            return
        if print_ is None:
            return
        try:
            grant = self.wallet.grant_voice(qid, rt.user_id, print_)
        except Exception as exc:  # never in the way of the card: the PIN still works
            log.warning("voice grant failed: %s", type(exc).__name__)
            grant = {"granted": False, "reason": "error"}
        rt.voice_cards[qid] = VoiceCard(print_, bool(grant.get("granted")))
        while len(rt.voice_cards) > VOICE_CARDS_KEPT:
            rt.voice_cards.popitem(last=False)
        log.info("voice check sender=%s granted=%s reason=%s", rt.key[:8], bool(grant.get("granted")),
                 grant.get("reason"))

    def _voice_granted(self, rt: Runtime, qid: str) -> bool:
        card = rt.voice_cards.get(qid)
        return bool(self.voice_approval_on and card is not None and card.granted and not rt.via_instagram)

    def _keep_voice(self, rt: Runtime, qid: str) -> list[str]:
        """After a card made from a voice note was paid with the right PIN: keep that note's voice (the wallet checks
        it was paid with the PIN). The first time, one line says what that allows and how to delete it."""
        card = rt.voice_cards.pop(qid, None)
        if card is None or not self.voice_approval_on:
            return []
        try:
            kept = self.wallet.enroll_voice(rt.user_id, qid, card.print_)
        except Exception as exc:
            log.warning("voice enrolment failed: %s", type(exc).__name__)
            return []
        if kept.get("added") and kept.get("first"):
            return [self._say(rt, "voice_enrolled", cap=self.settings.voice_approval_max_iqd)]
        return []

    def _voice_delete(self, rt: Runtime, item: Inbound, text: str = "") -> None:
        """«امسح صوتي» / "delete my voice": the kept voice is deleted and every open voice grant of the account voided."""
        st = rt.session.state(rt.user_id)
        said = text or item.text or ""
        if said:
            st.lang = language.detect(said, st.lang)
        try:
            gone = self.wallet.delete_voice(rt.user_id)
        except Exception as exc:
            log.warning("voice delete failed: %s", type(exc).__name__)
            self._send_text(item, self._say(rt, "error"))
            return
        with self._lock:
            for other in self._open.values():
                if other.user_id == rt.user_id:
                    other.voice_cards.clear()
        self._send_text(item, self._say(rt, "voice_deleted" if gone else "voice_none"))

    def _pin_step_open(self, rt: Runtime) -> bool:
        """«إي، ادفع» was tapped on the card that is still open: the next message is the PIN."""
        st = rt.session.state(rt.user_id)
        return rt.awaiting_pin is not None and st.card is not None and st.card.quote.get("id") == rt.awaiting_pin

    # ------------------------------------------------------------ commands

    def _command_of(self, text: str) -> str | None:
        norm = names.normalize(text or "")
        if not norm:
            return None
        return next((name for name, forms in COMMANDS.items() if norm in forms), None)

    def _command(self, rt: Runtime, item: Inbound, name: str) -> None:
        if name == "voice_delete":
            self._set_awaiting(rt, None)
            self._voice_delete(rt, item)
            return
        st = rt.session.state(rt.user_id)
        st.lang = language.detect(item.text, st.lang)  # «help» in English is answered in English
        self._send_text(item, account_texts.say("help", self._gender(rt), self._lang(rt)))
        if self.menu_on_hello:  # the same switch as after a hello: the menu to tap, as a second message
            self._send_menu(rt, item)

    # ------------------------------------------------------------ budget

    def _core_spent(self) -> float | None:
        value = getattr(self.core, "spent_usd", None)
        if callable(value):
            value = value()
        try:
            spent = None if value is None else float(value)
        except (TypeError, ValueError):
            spent = None
        try:  # reading identity documents is paid work too: it counts into the same daily budget
            reading = float(getattr(self.kyc_llm, "spent_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            reading = 0.0
        if spent is None and not reading:
            return None
        return (spent or 0.0) + reading

    def _account_spend(self) -> None:
        """Add what the core spent since the last look to today's total (it survives restarts)."""
        spent = self._core_spent()
        if spent is None:
            return
        with self._spend_lock:
            delta, self._last_spent = spent - self._last_spent, spent
        if delta > 0:
            self.store.add_spend(self._day(), delta)

    def _resting(self, rt: Runtime, item: Inbound) -> bool:
        """Over today's budget: say so once a day per sender and do no paid work."""
        budget = self.settings.daily_budget_usd
        if budget <= 0:
            return False
        self._account_spend()
        day = self._day()
        spent = self.store.spent(day)
        if spent < budget:
            return False
        row = self.store.sender(rt.key) or {}
        if row.get("rest_notice") != day:
            self.store.update_sender(rt.key, rest_notice=day)
            self._send_text(item, self._say(rt, "resting"))
        if self.store.mark_budget_notified(day) and self.settings.manager:
            self._notify_manager(texts.say("manager_budget", spent=f"{spent:.2f}", budget=f"{budget:.2f}"))
        return True

    # ------------------------------------------------------------ replies

    def _over_budget(self) -> bool:
        budget = self.settings.daily_budget_usd
        if budget <= 0:
            return False
        self._account_spend()
        return self.store.spent(self._day()) >= budget

    def _maybe_voice(self, rt: Runtime, item: Inbound, reply: Reply, user_sent_voice: bool = False,
                     never: tuple[str, ...] = ()) -> None:
        """Send the reply as a voice note too, when VOICE_REPLY_MODE says so (before the text or card).

        Skipped (text only) when the core cannot speak, today's budget is spent, or the voice fails. `never`: values
        no voice may say (a demo PIN shown in this turn is added here), as digits or as words."""
        if not self.speaks or not speech.wanted(self.settings.voice_reply_mode, user_sent_voice):
            return
        graph, to = self._route(item.sender)
        if getattr(graph, "voice_replies", True) is False:  # Instagram: nothing to send it with, so nothing is spent
            if user_sent_voice and not rt.told_no_voice and self.instagram is not None:
                rt.told_no_voice = True
                self._send_text(item, ig_texts.say("voice_not_here", self._gender(rt), self._lang(rt)))
            return
        if self._over_budget():
            return
        secrets = tuple(s for s in (*never, self._secrets.get(rt.key) or "") if s)
        words = speech.spoken_text(reply, never=secrets)
        audio = speech.speak(self.core, words, self._lang(rt), {"user_id": rt.user_id, "gender": self._gender(rt)})
        self._account_spend()  # the voice counts into today's budget
        if audio is None:
            return
        data, mime = audio
        self._send_outbox(item.sender)
        try:
            media_id = graph.upload_media(data, mime)
            graph.send_audio(to, item.has_phone, media_id)
            self.store.count(self._day(), "msgs_out_audio")
        except Exception as exc:
            log.warning("voice reply send failed: %s", type(exc).__name__)

    def _send_reply(self, rt: Runtime, item: Inbound, reply: Reply, paid_qid: str | None = None,
                    typed_pin: bool = False, after_receipt: list[str] | None = None) -> None:
        lines = [line for line in reply.lines if str(line).strip()]
        first: list[str] = []
        receipt = self._receipt(rt, paid_qid) if paid_qid else None
        if receipt:
            reference = getattr(reply, "reference", None)  # the receipt has the wallet's reference in full
            lines = [line for line in lines
                     if not reference or line != self._render(rt, "done_reference", reference=reference)]
            first, lines = lines[:1] + ["", receipt], lines[1:]
            lines[0:0] = list(after_receipt or [])  # the voice just kept: before a warm closing line
            if typed_pin and not rt.via_instagram:  # the PIN sits in the chat, and only its sender can delete it
                lines.insert(0, self._say(rt, "delete_pin"))  # before a warm closing line, never after it
        if reply.card is not None and reply.quote_id:
            if rt.voice_note is not None:
                self._bind_voice(rt, reply.quote_id)  # the first card of a voice note's turn
            if first:
                self._send_text(item, "\n".join(first))
            self._send_card(rt, item, reply, lines)  # its own three buttons, then «شلون تأكدت؟» under it
            return
        text = "\n".join(first + ([""] if first and lines else []) + lines)
        options = list(getattr(reply, "options", None) or [])
        role = getattr(reply, "options_role", "answer")
        if text.strip() and options and reply.kind == "ask":
            # The answers to tap go out with the question (and the menu with its line), as one message.
            if self._send_choices(rt, item, text, options, reply.options_title, as_list=role == "next"):
                return
        if text.strip():
            self._send_text(item, text)  # also the fallback: the numbered text, answered by typing
        if options and role == "next" and reply.kind in NEXT_AFTER:
            # «أي شي ثاني؟» was just asked, or a warm line already offered more: the menu asks nothing more
            asked = text.rstrip().endswith(("?", "؟")) or getattr(reply, "warm", False)
            self._send_choices(rt, item, self._say(rt, "menu_body" if asked else "next_body"), options,
                               reply.options_title, as_list=True)

    def _send_choices(self, rt: Runtime, item: Inbound, body: str, options: list[Option], title: str | None,
                      as_list: bool = False) -> bool:
        """`body` with options to tap under it: reply buttons for one to three, a list for four to ten (or when
        `as_list`). False when it cannot be done: more than ten options, a transport with no lists, titles that
        would not tell the choices apart, or a send the transport refused. The caller then sends the text as
        before (the numbered text, answered by typing), so a question is never lost with its buttons."""
        if not 1 <= len(options) <= LIST_ROWS_MAX or not (body or "").strip():
            return False
        if any(not Session.is_option(option.id) for option in options):
            return False  # only ids the orchestrator resolves: never a card's «confirm:<quote id>»
        graph, to = self._route(item.sender)
        lead = None
        if len(body) > MAX_BUTTON_BODY:  # too long to sit above buttons: the text first, whole, then the choices
            lead, body = body, self._say(rt, "choose_body")
        send = None
        if len(options) <= BUTTONS_MAX and not as_list:
            titles = button_titles(options)
            if all(titles) and len(set(titles)) == len(titles):
                buttons = [(option.id, shown) for option, shown in zip(options, titles)]
                send = lambda: graph.send_buttons(to, item.has_phone, body, buttons)  # noqa: E731
                what = "buttons"
        if send is None:
            send_list = getattr(graph, "send_list", None)
            if not callable(send_list):
                return False
            button = fit(title or self._render(rt, "options_button"), LIST_BUTTON_MAX)
            rows = [(option.id, shown, fit(option.description, LIST_ROW_DESCRIPTION_MAX))
                    for option, shown in zip(options, button_titles(options, LIST_ROW_TITLE_MAX))]
            if len({shown for _, shown, _ in rows}) != len(rows):
                return False
            try:
                list_payload(item.sender, item.has_phone, body, button, rows)  # what the API would refuse is refused here
            except ValueError:
                return False
            send = lambda: send_list(to, item.has_phone, body, button, rows)  # noqa: E731
            what = "list"
        if lead:
            self._send_text(item, lead)
        self._send_outbox(item.sender)
        try:
            send()
        except Exception as exc:
            log.warning("%s send failed: %s", what, type(exc).__name__)
            return lead is not None  # the question itself went out as text already, or the caller sends it now
        log.info("sent sender=%s kind=%s options=%d", self.sender_key(item.sender)[:8], what, len(options))
        self.store.count(self._day(), "msgs_out_interactive")
        return True

    def _send_card(self, rt: Runtime, item: Inbound, reply: Reply, lead: list[str], *, why: bool = True) -> None:
        """The card with its three buttons (pay, change, cancel), then «شلون تأكدت؟» as a message of its own: a
        message holds three reply buttons at most, and none of the card's may go. `why=False` for a card sent again
        (after «tap first»), whose explanation button is already right above it."""
        card, qid = reply.card, reply.quote_id or ""
        if self._voice_granted(rt, qid):
            lead = list(lead) + [self._say(rt, "voice_matched")]
        body = "\n".join(lead + ([""] if lead else []) + [card.text])
        if len(body) > 1024 and lead:  # too long for one card: the lead lines go first, on their own
            self._send_text(item, "\n".join(lead))
            body = card.text
        quote = self._quote(rt, qid) or {}
        kind = quote.get("kind") if quote.get("kind") in ("transfer", "bill", "topup") else "transfer"
        expires = _baghdad(quote.get("expires_at"))[-5:]
        lang = getattr(card, "lang", None) or self._lang(rt)
        footer = texts.say("card_footer", lang=lang, time=expires) if expires else ""
        header = getattr(card, "title", "") or texts.say(f"card_header_{kind}", lang=lang)
        buttons = [(f"{b.id}:{qid}", b.label) for b in card.buttons]
        graph, to = self._route(item.sender)
        self._send_outbox(item.sender)
        try:
            graph.send_buttons(to, item.has_phone, body, buttons, header=header, footer=footer)
            log.info("sent sender=%s kind=card chars=%d", self.sender_key(item.sender)[:8], len(body))
            self.store.count(self._day(), "msgs_out_interactive")
        except Exception as exc:
            log.warning("card send failed: %s", type(exc).__name__)
            return
        if why and self.why_on_cards:
            rt.card_checks[qid] = list(rt.session.state(rt.user_id).explained)  # what this card's turn checked
            while len(rt.card_checks) > 8:
                rt.card_checks.popitem(last=False)
            self._post_buttons(item, self._why_body(rt, lang), [self._why_button(rt, lang, qid)])

    def _why_body(self, rt: Runtime, lang: str) -> str:
        return texts.say("card_why_body", self._gender(rt), lang)

    def _why_button(self, rt: Runtime, lang: str, qid: str) -> tuple[str, str]:
        """«شلون تأكدت؟» under one card: «opt_why:<quote id>», so its tap explains that card and no later request
        (Channel._explain_card). It is never a card button: «opt_why» is no card action, and it moves nothing."""
        return f"{EXPLAIN_OPTION}:{qid}", fit(render("why_button", self._gender(rt), lang), BUTTON_TITLE_MAX)

    def _receipt(self, rt: Runtime, qid: str) -> str | None:
        """The receipt, from the wallet's own record of the quote (only for a payment that succeeded)."""
        quote = self._quote(rt, qid)
        if not quote or quote.get("state") != "SUCCEEDED":
            return None
        target = quote.get("target") if isinstance(quote.get("target"), dict) else {}
        result = quote.get("result") or {}
        amount, fee = int(quote.get("amount") or 0), int(quote.get("fee") or 0)
        kind = quote.get("kind")
        lang = self._lang(rt)
        if kind == "bill":
            head = self._say(rt, "receipt_bill", payee=biller_name(target, lang),
                             period=format_period(str(target.get("period") or ""), lang))
        elif kind == "topup":
            head = self._say(rt, "receipt_topup", last4=_last4(target.get("phone")))
        else:
            head = self._say(rt, "receipt_transfer", payee=target.get("display_name") or target.get("name") or "")
        lines = [self._say(rt, "receipt_title"), head, self._say(rt, "receipt_amount", amount=amount),
                 self._say(rt, "receipt_fee", fee=fee) if fee else self._say(rt, "receipt_no_fee"),
                 self._say(rt, "receipt_total", total=amount + fee)]
        if isinstance(result.get("balance_after"), int):
            lines.append(self._say(rt, "receipt_balance", balance=result["balance_after"]))
        if result.get("transaction_id"):
            lines.append(self._say(rt, "receipt_reference", reference=result["transaction_id"]))
        when = _baghdad(result.get("completed_at") or quote.get("created_at"))
        if when:
            lines.append(self._say(rt, "receipt_time", time=when))
        if kind in ("bill", "topup"):  # a bill paid here does not reach the biller yet: say so plainly
            lines.append(self._render(rt, "pilot_bill"))
        return "\n".join(lines)

    def _send_text(self, item: Inbound, text: str) -> None:
        box = self._outbox.get(item.sender)
        if box is not None:
            box.append((item, text))
        else:
            self._post_text(item, text)

    def _pace(self, item: Inbound, text: str) -> None:
        """The typing pause before a bubble. The first bubble of a turn waits only what the turn has not already
        taken (a slow model turn is pause enough); a later one shows the typing dots again first (WhatsApp hides
        them once a message goes out), then waits."""
        pause = min(PACE_MAX_SECONDS, 0.4 + len(text) / 120)
        started = self._turn_started.get(item.sender)
        if started is not None and item.sender not in self._bubbled:
            self._bubbled.add(item.sender)
            pause -= time.monotonic() - started
        else:
            try:
                self._mark_read(item)
            except Exception as exc:  # cosmetic only
                log.info("typing dots failed: %s", type(exc).__name__)
        if pause > 0:
            self.sleep(pause)

    def _post_text(self, item: Inbound, text: str) -> bool:
        """Send one text bubble now; False when the send failed (logged, never raised)."""
        graph, to = self._route(item.sender)
        text = once_per_message(text)
        if self.settings.human_pace:  # a short typing pause, longer for a longer bubble
            self._pace(item, text)
        try:
            graph.send_text(to, item.has_phone, text)
            log.info("sent sender=%s kind=text chars=%d", self.sender_key(item.sender)[:8], len(text))
            self.store.count(self._day(), "msgs_out_text")
            return True
        except Exception as exc:
            log.warning("text send failed: %s", type(exc).__name__)
            return False

    def _send_outbox(self, sender: str) -> None:
        """The turn's texts so far, as one message (as many as WhatsApp's length limit needs). Runs before a card or a
        voice note, so everything still arrives in the order it was said."""
        box = self._outbox.get(sender)
        if not box:
            return
        item, joined = box[0]
        for _, text in box[1:]:
            if len(joined) + len(text) + 2 > MAX_TEXT:
                self._post_text(item, joined)
                joined = text
            else:
                joined = f"{joined}\n\n{text}"
        self._post_text(item, joined)
        box.clear()

    def _send_buttons(self, item: Inbound, body: str, buttons: list[tuple[str, str]]) -> None:
        if item.sender in self._held_buttons:
            self._held_buttons[item.sender] = (item, body, buttons)
            return
        self._send_outbox(item.sender)
        self._post_buttons(item, body, buttons)

    def _post_buttons(self, item: Inbound, body: str, buttons: list[tuple[str, str]]) -> None:
        graph, to = self._route(item.sender)
        try:
            graph.send_buttons(to, item.has_phone, body, buttons)
            log.info("sent sender=%s kind=buttons chars=%d", self.sender_key(item.sender)[:8], len(body))
            self.store.count(self._day(), "msgs_out_interactive")
        except Exception as exc:
            log.warning("buttons send failed: %s", type(exc).__name__)

    def _notify_manager(self, text: str) -> None:
        """Best effort: it only lands if the manager wrote to the number in the last 24 hours."""
        try:
            self.graph.send_text(self.settings.manager, True, text)
        except Exception as exc:
            log.info("manager notification failed: %s", type(exc).__name__)

    # ------------------------------------------------------------ cards and quotes

    def _quote(self, rt: Runtime, qid: str) -> dict | None:
        try:
            return rt.wallet.quote(qid) if qid else None
        except (WalletError, WalletUnavailable):
            return None

    def _expired(self, rt: Runtime, qid: str) -> bool:
        quote = self._quote(rt, qid)  # reading a quote past its time marks it EXPIRED
        return bool(quote) and quote.get("user_id") == rt.user_id and quote.get("state") == "EXPIRED"

    def _drop_card(self, rt: Runtime, qid: str) -> None:
        """Forget an expired card, so the orchestrator never offers it again."""
        state = rt.session.state(rt.user_id)
        if state.card is not None and state.card.quote.get("id") == qid:
            state.card = None
            state.awaiting_pin = None

    def _set_awaiting(self, rt: Runtime, qid: str | None) -> None:
        if rt.awaiting_pin != qid:
            rt.awaiting_pin = qid
            self.store.update_sender(rt.key, awaiting_pin=qid)

    # ------------------------------------------------------------ conversations

    def _runtime(self, key: str, uid: str, *, instagram: bool = False) -> Runtime:
        """The open conversation of one chat with one account. It is keyed by the sender (its key), not by the
        account: the WhatsApp chat and the Instagram chat of one account are two conversations, each with its own
        card, question and PIN step (a PIN typed in one chat can never confirm the other's card), over the same
        wallet and accounts database."""
        with self._lock:
            self._evict()
            row = self.store.sender(key)
            if row is None or row.get("user_id") != uid:
                if row is None:
                    self.store.add_sender(key, uid, self._now())
                self.store.update_sender(key, user_id=uid, greeted=1, awaiting_pin=None)
                row = self.store.sender(key) or {}
                self._open.pop(key, None)
            rt = self._open.get(key)
            if rt is None:
                http = TestClient(self.wallet_app)
                wallet = WalletClient(http)
                session = Session(wallet, self.core, clock=self.clock, accounts=self.accounts)
                user = self.accounts.get(uid) or {}
                session.state(uid).lang = user.get("language") or language.DEFAULT
                session.state(uid).gender = "f" if user.get("gender") == "f" else "m"
                rt = Runtime(key, uid, http, wallet, session, awaiting_pin=row.get("awaiting_pin"),
                             via_instagram=instagram, demo=user.get("source") == "demo")
                self._open[key] = rt
            self._open.move_to_end(key)
            rt.last_used = time.monotonic()
            return rt

    def forget(self, key: str) -> None:
        """Close a sender's open conversation (after sign-up or a PIN reset: a fresh start)."""
        with self._lock:
            rt = self._open.pop(key, None)
        if rt is not None and rt.busy == 0:
            rt.close()

    def _evict(self) -> None:
        cutoff = time.monotonic() - IDLE_SECONDS
        for key in [k for k, rt in self._open.items() if rt.busy == 0 and rt.last_used < cutoff]:
            self._open.pop(key).close()
        for key in [k for k, rt in self._open.items() if rt.busy == 0][: max(0, len(self._open) - MAX_OPEN)]:
            self._open.pop(key).close()

    # ------------------------------------------------------------ small lookups

    def _gender(self, rt: Runtime) -> str:
        return rt.session.state(rt.user_id).gender

    @staticmethod
    def _lang(rt: Runtime) -> str:
        return rt.session.lang_of(rt.user_id)

    @staticmethod
    def _variant(rt: Runtime, key: str, have: Callable[[str], bool] | None = None) -> str:
        """`key`, then `key_2`, `key_3` (those that exist), in turn: the same fixed line twice in a row reads like a
        machine. The first use is always `key` itself."""
        have = have or (lambda k: k in texts.TEXTS)
        keys = [key] + [f"{key}_{n}" for n in (2, 3) if have(f"{key}_{n}")]
        n = rt.variants.get(key, 0)
        rt.variants[key] = n + 1
        return keys[n % len(keys)]

    def _say(self, rt: Runtime, key: str, **slots: Any) -> str:
        """A channel text in this sender's language and gender."""
        return texts.say(key, self._gender(rt), self._lang(rt), **slots)

    def _render(self, rt: Runtime, key: str, **slots: Any) -> str:
        """An orchestrator message (payments.messages) in this sender's language."""
        return render(key, self._gender(rt), self._lang(rt), **slots)

    def _talk(self, rt: Runtime, situation: str, fallback: str, *, text: str = "", facts: dict | None = None,
              required: tuple = ()) -> str:
        """A line the channel answers itself, worded by the session's writer when it has one (Session.talk), else
        `fallback`. The writer sees named facts only (never a PIN); a failure, an empty answer or a spent budget
        gives the fixed line."""
        talk = getattr(rt.session, "talk", None)
        if not callable(talk) or self._over_budget():
            return fallback
        try:
            out = talk(rt.user_id, situation, text=text, facts=dict(facts or {}), required=tuple(required),
                       fallback=fallback)
        except Exception as exc:
            log.warning("writer failed (%s): %s", situation, type(exc).__name__)
            return fallback
        return out.strip() if isinstance(out, str) and out.strip() else fallback

    def _voice_or(self, rt: Runtime) -> str:
        return self._say(rt, "voice_or") if self.voice else ""

    def _voice_send(self, rt: Runtime) -> str:
        return self._say(rt, "voice_send") if self.voice else ""


_ = looks_like_pin  # re-exported for older imports

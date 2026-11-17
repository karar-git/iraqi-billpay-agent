"""Opening an account from WhatsApp: the number is the account, the person picks a name, says how to be
addressed, and sets a PIN typed twice. State lives in the channel store (onboarding table), so a restart
in the middle resumes where the person was.

    offer -> name (their WhatsApp profile name offered) -> gender -> [identity document [-> its back]] -> PIN
          -> PIN again -> done

- The identity document step (SIGNUP_KYC=optional | required, see kyc/): a photo of a unified national card, a
  passport or a residence card, asked for with a plain notice (a pilot with a mock ledger; the photo is read once
  by an automated reader and is not kept; only the document type, the name, the last 4 digits and the birth year
  are stored). The photo is downloaded into memory, read by the reader and decided by kyc.check, in code:
  verified -> on to the PIN; retry (not a document, unreadable) -> why, and the question again, at most
  kyc.MAX_ATTEMPTS photos, then a person reviews; review (the name differs, a copy or a screen, the same document
  on another account) -> the account opens at level "pending" with the small limits and waits on the admin's
  requests page; refused (under 18, expired, a type not accepted) -> the sign-up stops with the reason and only
  an audit line without personal data is kept. optional offers «Later» (level "none", the small limits); required
  has no skip, and neither has a sender whose last document read under 18. The image bytes never reach the disk,
  the database, a log or the outbox.
- A national card whose front prints no birth date ("back_needed") is asked for its back (step document_back): the
  front's reading waits in memory only (FRONT_TTL_SECONDS, never on disk), and the two photos are decided as one
  card. A restart in between asks for the front again. Neither photo costs a try. The front waits at most
  FRONT_TTL_SECONDS from when it was read (a failed back does not restart that time), and every message purges
  the fronts that are older. A back verifies only when it carries the front's number (kyc.check._sides).
- A sender whose last document read under 18 gets no «Later», and a sign-up review that read no age it can trust
  (three bad photos, an untied back) stops the sign-up ("age_not_confirmed") instead of opening an account.
- A photo the reader gives nothing for (the vision call failed or timed out) costs no try: the person is asked to
  send it again in a moment. Every read still counts into KYC_READS_PER_DAY, so an outage cannot loop on the model.
- A file that is not a photo (a PDF) at the step is answered with why, and costs no try.
- With SIGNUP_KYC=off the step does not exist and sign-up is what it was before. With SIGNUP_KYC=optional and no
  reader or no KYC_PEPPER the same. With SIGNUP_KYC=required and no reader or no pepper, the step cannot run but a
  new account still opens at level "none" (the small limits), never with the full ones.
- «وثّق حسابي» / "verify my account" lets an account at level none or pending send the document later
  (Onboarding.start_verification, Onboarding.verify_photo): the same reading, the same decisions, the back of a
  national card too (step verify_back). The document is matched against the name typed at sign-up (or set by an
  admin), never one the person renamed the account to since (Accounts.kyc_match_name). A document that reads under
  18 there freezes the account at once (payments stop; money sent to it still arrives), puts it on the admin's
  requests page (level "pending", reason under_age, only Reject offered; no printed name or last 4 kept) and tells
  the manager. While it stays frozen «verify my account» is not opened, so no later document replaces that answer;
  only an admin's Unfreeze lifts it.
- A number the admin added (invited) or whose PIN the admin reset only sets a PIN (gender first if unknown).
- SIGNUP_MODE: open (default), invite (only numbers the admin added; others get a polite line and the
  manager is told), closed, demo (no sign-up at all: the channel opens a demo account at the first message,
  channels/whatsapp/demo.py; this flow then serves only invited numbers and PIN resets).
- The first PIN is kept only as a hash; the second is checked against it; weak PINs are refused
  (accounts.pins). Five weak or mismatched tries start the PIN step again. A PIN is never logged, never
  sent to the core and never spoken; a voice note during sign-up gets «type it, please».
- Done is one transaction in the wallet (Wallet.open_account): the account, the optional sign-up grant,
  every transfer held for this number, the notices and the audit. A second tap on the last step cannot
  make a second account (one account per number).
- «الغاء» / «cancel» drops the sign-up; a sign-up idle for 24 hours starts again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from typing import TYPE_CHECKING, Any

import kyc
from accounts import phones, pins
from accounts import texts as T
from accounts.service import clean_name
from channels.whatsapp import texts as channel_texts
from channels.whatsapp.graph import Inbound
from kyc import texts as kyc_texts
from kyc.check import name_words
from payments import language, names
from wallet.db import BAGHDAD

if TYPE_CHECKING:
    from channels.whatsapp.channel import Channel

log = logging.getLogger("whatsapp.onboarding")

IDLE_RESTART_SECONDS = 24 * 3600
MAX_PIN_TRIES = 5
DOCUMENT_STEP = "document"  # sign-up: waiting for the photo of an identity document
DOCUMENT_BACK_STEP = "document_back"  # sign-up: the front of a national card was read, waiting for its back
VERIFY_STEP = "verify"  # an account that verifies later: the same store row, never a sign-up
VERIFY_BACK_STEP = "verify_back"  # an account that verifies later: waiting for the back of its national card
DOCUMENT_STEPS = (DOCUMENT_STEP, DOCUMENT_BACK_STEP)
VERIFY_STEPS = (VERIFY_STEP, VERIFY_BACK_STEP)
FRONT_TTL_SECONDS = 30 * 60  # how long the reading of a card's front waits in memory for its back
KYC_READS_PER_DAY = 6  # photos read for one sender or one account in 24 hours (each one is a model call)


def _forms(*words: str) -> frozenset[str]:
    return frozenset(names.normalize(w) for w in words)


OPEN = _forms("افتح حساب", "افتح حسابا", "افتح حساباً", "افتحلي حساب", "اي", "إي", "نعم", "اكيد", "يلا", "تمام",
              "اوك", "ok", "yes", "open account", "open", "sign up", "signup", "register", "حساب جديد", "سجلني")
LATER = _forms("مو هسه", "بعدين", "ليس الآن", "ليس الان", "لا", "not now", "later", "no", "مو الحين")
CANCEL = _forms("الغاء", "إلغاء", "بطل", "بطلت", "cancel", "stop", "الغي")
YES = _forms("اي", "إي", "نعم", "اكيد", "صح", "yes", "ok", "تمام", "اوك", "هو", "زين")
MALE = _forms("رجال", "رجّال", "رجل", "ذكر", "ولد", "male", "man", "m", "مذكر", "اخ", "شب")
FEMALE = _forms("امرأة", "امراة", "مرة", "انثى", "أنثى", "بنت", "female", "woman", "f", "مؤنث", "اخت", "سيدة")
KYC_LATER = _forms("بعدين", "مو هسه", "مو الحين", "لاحقا", "لاحقاً", "ليس الآن", "ليس الان", "later", "not now", "skip",
                   "تخطي")
VERIFY = _forms("وثق حسابي", "وثّق حسابي", "وثقي حسابي", "وثق الحساب", "توثيق حسابي", "توثيق الحساب", "توثيق الهوية",
                "اريد اوثق حسابي", "أريد توثيق حسابي", "وثق هويتي", "verify my account", "verify account", "verify me",
                "verify my identity", "verify")


@dataclass
class DocumentCheck:
    """What came of one photo. `status` is a kyc status ("verified" | "retry" | "review" | "refused"), or "back"
    (the front of a national card was read and its back is needed; no attempt is counted), "later" (nothing was
    read: the download failed, today's budget is spent or the reader gave nothing; no attempt is counted) or
    "capped" (this sender had KYC_READS_PER_DAY photos read already). `reason` is the code the decision stands on;
    `why` is what kyc.check said about this photo (it differs from `reason` when the code here turned a verdict into
    a review). `record` is kyc.privacy.record() for a verified or a review outcome: the only thing that may be
    stored. `front` is the raw reading of a card's front for "back": held in memory for its back, never stored."""

    status: str
    reason: str
    why: str = ""
    record: dict | None = None
    front: dict | None = field(default=None, repr=False)


def bare_record(ch: "Channel", level: str, reason: str) -> dict:
    """The record of an account that showed no usable document (it skipped, ran out of tries, or the step could
    not run): the level, the reason and the time, every document field empty, in the shape of kyc.privacy.record().
    No number means no fingerprint, so no pepper is needed (a required step without a pepper still gets a level)."""
    moment = ch.clock()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return {**{key: None for key in kyc.RECORD_KEYS}, "level": level, "reason": reason,
            "checked_at": moment.astimezone(timezone.utc).isoformat(timespec="seconds")}


def read_document(ch: "Channel", item: Inbound, *, key: str, uid: str | None, phone: str, typed_name: str,
                  attempt: int, where: str, front: dict | None = None) -> DocumentCheck:
    """One photo of a document, start to finish. The bytes are downloaded into memory, handed to the reader (the
    model layer's vision role) and dropped: they are never written to disk, to a database, to a log or to the
    outbox. kyc.verify decides in code; this adds the rules that need the accounts or the conversation: the same
    document on another account is a review (never verified automatically), the last allowed bad photo is a review
    too, a reader that gave nothing costs no try ("later"), and for an account that verifies later a document that
    reads under 18 goes to a person. `front` is the raw reading of the card's front when this photo is its back.
    One audit line with no personal data is written per photo read."""
    since = ch.clock() - timedelta(hours=24)
    if ch.accounts.kyc_checks_since(since, uid=uid, ref=key) >= KYC_READS_PER_DAY:
        return DocumentCheck("capped", "daily_reads")
    if ch._over_budget():  # a reading is a paid model call: none once today's budget is spent
        return DocumentCheck("later", "budget")
    try:
        image, mime = ch.graph.media(item.media_id)
    except Exception as exc:
        log.warning("document photo download failed: %s", type(exc).__name__)
        return DocumentCheck("later", "download")
    answers: list = []

    def reader(data: bytes, kind: str) -> dict | None:  # what the model said, kept for this call only
        answer = ch.kyc_reader(data, kind)
        answers.append(answer)
        return answer

    try:
        verdict = kyc.verify(image, mime or item.mime, reader=reader, typed_name=typed_name or "",
                             today=ch.clock().astimezone(BAGHDAD).date(),
                             allow_specimen=ch.settings.kyc_allow_specimen, front=front)
    finally:
        del image  # the photo is gone as soon as it was read
        ch._account_spend()  # the reading counts into today's budget
    status, reason, record, kept = verdict.status, verdict.reason, None, None
    if answers and answers[0] is None:  # the vision call failed or timed out: not the person's fault, no try lost
        status, reason = "later", "no_reading"
    elif reason == "back_needed":
        status, kept = "back", answers[0]
    elif status == "refused" and reason == "under_age" and uid is not None:  # an account exists: a person looks
        status = "review"
        record = {**kyc.record(verdict, pepper=ch.settings.kyc_pepper_bytes, checked_at=ch.clock()),
                  "level": "pending"}
    if status in ("verified", "review") and record is None:
        record = kyc.record(verdict, pepper=ch.settings.kyc_pepper_bytes, checked_at=ch.clock())
        if reason == "sides_unconfirmed":  # the birth year came from a back that may be someone else's
            record = {**record, "birth_year": None}
        elif ch.accounts.kyc_same_document(record["number_fingerprint"], uid=uid, phone=phone):
            status, reason = "review", "duplicate_document"
            record = {**record, "level": "pending", "reason": reason}
    elif status == "retry" and attempt >= kyc.MAX_ATTEMPTS:
        status, reason = "review", "too_many_attempts"
        record = bare_record(ch, "pending", reason)
    if (status == "review" and reason in ("too_many_attempts", "sides_unconfirmed") and uid is None
            and ch.accounts.kyc_under_age_seen(key)):
        # this sender's last document read under 18, and this review read no age it can trust: no account opens
        # around that answer (with «Later» gone, three bad photos or a stranger's back must not be the way in)
        status, reason, record = "refused", "age_not_confirmed", None
    ch.accounts.kyc_note_check(uid=uid, ref=key, status=status, reason=reason, attempt=attempt,
                               checks=verdict.checks, where=where)
    answers.clear()
    log.info("document read (%s): %s %s", where, status, reason)
    return DocumentCheck(status, reason, verdict.reason, record, kept)


class Onboarding:
    def __init__(self, channel: "Channel"):
        self.ch = channel
        # sender key -> (raw reading of a national card's front, when it was read). Memory only, never written:
        # it holds the whole number until the back arrives, like the photo bytes during a read. The time is the
        # time the front was read and is never moved, so FRONT_TTL_SECONDS is a hard limit.
        self._fronts: dict[str, tuple[dict, float]] = {}

    def _purge_fronts(self) -> None:
        """Drop every front older than FRONT_TTL_SECONDS. Run on each message of any sender (and on each store),
        so a front whose sender went quiet does not stay in memory until that sender writes again."""
        now = self.ch._now()
        for other in [k for k, (_, at) in self._fronts.items() if now - at > FRONT_TTL_SECONDS]:
            self._fronts.pop(other, None)

    def _keep_front(self, key: str, front: dict | None) -> None:
        """A newly read front, timed from now. Only a new front is stored: a failed back leaves the held one, with
        its first time, where it is."""
        self._purge_fronts()
        if front is not None:
            self._fronts[key] = (front, self.ch._now())

    def _front(self, key: str) -> dict | None:
        """The front waiting for its back, or None (none, or older than FRONT_TTL_SECONDS: then it is dropped)."""
        self._purge_fronts()
        held = self._fronts.get(key)
        return held[0] if held else None

    def _forget_front(self, key: str) -> None:
        self._fronts.pop(key, None)

    # ------------------------------------------------------------ entry

    def needed(self, key: str, user: dict | None) -> bool:
        """This sender must go through sign-up (or set a PIN) before anything else."""
        if user is None:
            return True
        if user.get("status") == "invited" or user.get("pin_state") == "unset":
            return True
        return False

    def step(self, key: str) -> str | None:
        row = self.ch.store.onboarding(key)
        return row["step"] if row else None

    def says_nothing_new(self, key: str, item: Inbound) -> bool:
        """A later text of a burst, at the offer, that neither takes it nor declines it."""
        norm = names.normalize(item.text or "")
        return item.kind == "text" and self.step(key) == "offer" and norm not in OPEN and norm not in LATER

    def handle(self, key: str, item: Inbound, user: dict | None) -> None:
        ch = self.ch
        now = ch._now()
        self._purge_fronts()
        if not item.has_phone:
            self._send(item, T.say("signup_needs_number", "m", self._lang_of(item)))
            return
        e164 = phones.canonical(item.sender)
        if e164 is None:
            self._send(item, T.say("signup_needs_number", "m", self._lang_of(item)))
            return
        row = ch.store.onboarding(key)
        if row is not None and (row["step"] in VERIFY_STEPS  # left by «وثّق حسابي»: the PIN was reset since
                                or now - float(row["updated_at"]) > IDLE_RESTART_SECONDS):
            ch.store.end_onboarding(key)
            ch.accounts.kyc_drop(e164)
            self._forget_front(key)
            row = None
        if row is None:
            self._start(key, e164, item, user, now)
            return
        lang = row["lang"]
        if item.kind == "image":
            self._on_image(key, item, row, user)
            return
        if item.kind in ("voice", "audio"):
            if row["step"] in DOCUMENT_STEPS:
                self._send(item, T.say("kyc_needs_photo", row.get("gender") or "m", lang))
            else:
                self._send(item, T.say("voice_in_signup", row.get("gender") or "m", lang))
            self._repeat(item, row, user)
            return
        if item.kind == "document" and row["step"] in DOCUMENT_STEPS and ch.kyc_mode != "off":
            # a PDF or another file that is not a photo: say why, no try lost, nothing is read
            self._ask_step(item, row, [kyc_texts.render("kyc_image_type", row.get("gender") or "m", lang)])
            return
        if item.kind not in ("text", "button"):
            self._repeat(item, row, user)
            return
        text = (item.text or "").strip()
        norm = names.normalize(text)
        if item.kind == "text" and norm in CANCEL and row["step"] != "offer":
            ch.store.end_onboarding(key)
            ch.accounts.kyc_drop(row["phone_e164"])  # what a document check left for this sign-up goes with it
            self._forget_front(key)
            self._send(item, T.say("signup_cancelled", row.get("gender") or "m", lang))
            return
        step = row["step"]
        if step == "offer" and item.kind == "text":  # still free talk: answer in the language of this message
            lang = language.detect(text, lang)
            if lang != row["lang"]:
                ch.store.update_onboarding(key, lang=lang)
                row = {**row, "lang": lang}
        handler = getattr(self, f"_on_{step}", None)
        if handler is None:
            ch.store.end_onboarding(key)
            self._start(key, e164, item, user, now)
            return
        handler(key, item, row, user, text, norm)

    # ------------------------------------------------------------ steps

    def _start(self, key: str, e164: str, item: Inbound, user: dict | None, now: float) -> None:
        ch = self.ch
        lang = self._lang_of(item)
        hello = channel_texts.hello(ch.clock().astimezone(BAGHDAD).hour, lang)
        if user is not None:  # invited by the admin, or a PIN reset: only the PIN (and the gender if unknown)
            lang = user.get("language") or lang
            gender = user.get("gender")
            first = (user.get("name") or "").split(" ")[0]
            step = "gender" if gender not in ("m", "f") else "pin"
            row = ch.store.start_onboarding(key, e164, step, lang, now)
            ch.store.update_onboarding(key, name=user.get("name"), gender=gender if gender in ("m", "f") else None)
            balance = int(user.get("balance") or 0)
            if user.get("status") == "invited":
                lead = T.say("invited_hello", gender or "m", lang, hello=hello, first=first, balance=balance)
            else:
                lead = T.say("pin_reset_hello", gender or "m", lang, hello=hello, first=first)
                if step == "pin":  # the reset greeting already asks for the PIN
                    self._send(item, lead)
                    return
            if step == "gender":
                self._send(item, lead)
                self._ask_gender(item, lang)
            else:
                self._send(item, lead + "\n" + T.say("ask_pin", gender, lang))
            return
        mode = ch.settings.signup_mode
        if mode == "closed":
            self._send(item, T.say("signup_closed", "m", lang))
            return
        if mode == "invite":
            self._send(item, T.say("signup_invite_only", "m", lang))
            if ch.settings.manager:
                ch._notify_manager(T.say("manager_invite_request", number=phones.display(e164)))
            return
        ch.accounts.kyc_drop(e164)  # nothing of an earlier, unfinished sign-up's document check is carried over
        ch.store.start_onboarding(key, e164, "offer", lang, now)
        if item.kind == "text" and names.normalize(item.text) in OPEN - YES:
            self._on_offer(key, item, ch.store.onboarding(key), None, item.text, names.normalize(item.text))
            return
        self._offer(item, lang, e164, hello)
        ch.store.count(ch._day(), "signups_started")

    def _offer(self, item: Inbound, lang: str, e164: str, hello: str | None = None) -> None:
        hello = hello or channel_texts.hello(self.ch.clock().astimezone(BAGHDAD).hour, lang)
        body = T.say("offer", "m", lang, hello=hello, number=phones.display(e164)) + "\n\n" + T.say("pilot", "m", lang)
        self._buttons(item, body, [("o:open", T.say("btn_open", "m", lang)), ("o:later", T.say("btn_later", "m", lang))])

    def _on_offer(self, key: str, item: Inbound, row: dict, user: dict | None, text: str, norm: str) -> None:
        lang = row["lang"]
        if item.button_id == "o:open" or (item.kind == "text" and norm in OPEN):
            name = clean_name(item.name or "")
            if name:
                self.ch.store.update_onboarding(key, step="name_confirm", name=name, updated_at=self.ch._now())
                self._buttons(item, T.say("name_confirm", "m", lang, name=name),
                              [("o:name_yes", T.say("btn_yes", "m", lang)),
                               ("o:name_other", T.say("btn_other_name", "m", lang))])
            else:
                self.ch.store.update_onboarding(key, step="name", updated_at=self.ch._now())
                self._send(item, T.say("ask_name", "m", lang))
            return
        if item.button_id == "o:later" or (item.kind == "text" and norm in LATER):
            self.ch.store.end_onboarding(key)
            self._send(item, T.say("not_now", "m", lang))
            return
        self._offer(item, lang, row["phone_e164"])  # nothing was begun yet: the offer says all there is to say

    def _on_name_confirm(self, key: str, item: Inbound, row: dict, user: dict | None, text: str, norm: str) -> None:
        lang = row["lang"]
        if item.button_id == "o:name_yes" or (item.kind == "text" and norm in YES):
            if self._name_too_short(row.get("name"), user):
                self.ch.store.update_onboarding(key, step="name", updated_at=self.ch._now())
                self._send(item, T.say("kyc_name_two_parts", "m", lang))
                return
            self.ch.store.update_onboarding(key, step="gender", updated_at=self.ch._now())
            self._ask_gender(item, lang)
            return
        if item.button_id == "o:name_other":
            self.ch.store.update_onboarding(key, step="name", updated_at=self.ch._now())
            self._send(item, T.say("ask_name", "m", lang))
            return
        self._on_name(key, item, row, user, text, norm)

    def _on_name(self, key: str, item: Inbound, row: dict, user: dict | None, text: str, norm: str) -> None:
        lang = row["lang"]
        name = clean_name(text) if item.kind == "text" else None
        if name is None:
            self._send(item, T.say("bad_name", "m", lang))
            return
        if self._name_too_short(name, user):
            self._send(item, T.say("kyc_name_two_parts", "m", lang))
            return
        self.ch.store.update_onboarding(key, step="gender", name=name, updated_at=self.ch._now())
        self._ask_gender(item, lang)

    def _name_too_short(self, name: str | None, user: dict | None) -> bool:
        """With the identity step on, the typed name is matched against the document, and one word alone never
        matches (kyc.check.names_match): a new account is asked for two name parts."""
        return user is None and self.ch.kyc_mode != "off" and len(name_words(name, drop_titles=True)) < 2

    def _ask_gender(self, item: Inbound, lang: str) -> None:
        self._buttons(item, T.say("ask_gender", "m", lang),
                      [("o:m", T.say("btn_male", "m", lang)), ("o:f", T.say("btn_female", "m", lang))])

    def _on_gender(self, key: str, item: Inbound, row: dict, user: dict | None, text: str, norm: str) -> None:
        lang = row["lang"]
        gender = {"o:m": "m", "o:f": "f"}.get(item.button_id)
        if gender is None and item.kind == "text":
            gender = "m" if norm in MALE else "f" if norm in FEMALE else None
        if gender is None:
            self._ask_gender(item, lang)
            return
        if user is None and self.ch.kyc_mode != "off":  # a new account: the identity document, then the PIN
            self.ch.store.update_onboarding(key, step=DOCUMENT_STEP, gender=gender, tries=0,
                                            updated_at=self.ch._now())
            self._ask_document(item, {**row, "gender": gender})
            return
        self.ch.store.update_onboarding(key, step="pin", gender=gender, tries=0, updated_at=self.ch._now())
        self._send(item, T.say("ask_pin", gender, lang))

    # ------------------------------------------------------------ the identity document

    def _may_skip(self, row: dict) -> bool:
        """«Later» is offered: optional mode, and this sender's last document did not read under 18 (a fresh
        sign-up must not open an account around that answer; a document that passes still can)."""
        return self.ch.kyc_mode == "optional" and not self.ch.accounts.kyc_under_age_seen(row.get("key"))

    def _ask_document(self, item: Inbound, row: dict, lead: tuple[str, ...] | list[str] = ()) -> None:
        """The question, with the notice. optional: the small limits and «Later» to tap; required: no skip."""
        lang, gender = row["lang"], row.get("gender") or "m"
        self._ask_with_skip(item, row, [*lead, T.say("kyc_ask", gender, lang), T.say("kyc_notice", gender, lang)])

    def _ask_back(self, item: Inbound, row: dict, lead: tuple[str, ...] | list[str] = ()) -> None:
        """The front of a national card was read: its back, please (the same skip as the first question)."""
        gender = row.get("gender") or "m"
        self._ask_with_skip(item, row, [*lead, kyc_texts.render("kyc_back_needed", gender, row["lang"])])

    def _ask_step(self, item: Inbound, row: dict, lead: tuple[str, ...] | list[str] = ()) -> None:
        (self._ask_back if row["step"] == DOCUMENT_BACK_STEP else self._ask_document)(item, row, lead)

    def _ask_with_skip(self, item: Inbound, row: dict, lines: list[str]) -> None:
        lang, gender = row["lang"], row.get("gender") or "m"
        if self._may_skip(row):
            lines.append(T.say("kyc_later_hint", gender, lang, **kyc.limits_for("none")))
            self._buttons(item, "\n".join(lines), [("o:kyc_later", T.say("btn_kyc_later", gender, lang))])
        else:
            lines.append(T.say("kyc_required_hint", gender, lang))
            self._send(item, "\n".join(lines))

    def _to_pin(self, key: str, item: Inbound, row: dict, lead: list[str]) -> None:
        lang, gender = row["lang"], row.get("gender") or "m"
        self._forget_front(key)
        self.ch.store.update_onboarding(key, step="pin", tries=0, updated_at=self.ch._now())
        self._send(item, "\n".join([*lead, T.say("ask_pin", gender, lang)]))

    def _on_document(self, key: str, item: Inbound, row: dict, user: dict | None, text: str, norm: str) -> None:
        """A text or a tap at the document step: «Later» where it is offered, else the question again."""
        lang, gender = row["lang"], row.get("gender") or "m"
        mode = self.ch.kyc_mode
        if mode == "off":  # switched off since this sign-up began: straight to the PIN
            self._to_pin(key, item, row, [])
            return
        later = item.button_id == "o:kyc_later" or (item.kind == "text" and norm in KYC_LATER)
        if later and self._may_skip(row):
            self.ch.accounts.kyc_hold(row["phone_e164"], bare_record(self.ch, "none", "skipped"))
            self._to_pin(key, item, row, [T.say("kyc_later_done", gender, lang)])
            return
        self._ask_step(item, row)

    _on_document_back = _on_document  # a text or a tap while the back is awaited: the same answers

    def _on_image(self, key: str, item: Inbound, row: dict, user: dict | None) -> None:
        """A photo during sign-up. At the document step it is read; at any other step it is not."""
        ch = self.ch
        lang, gender = row["lang"], row.get("gender") or "m"
        if ch.kyc_mode == "off":
            if row["step"] in DOCUMENT_STEPS:
                self._to_pin(key, item, row, [])
            else:
                self._repeat(item, row, user)  # as before the feature: the open question again
            return
        if row["step"] not in DOCUMENT_STEPS:
            self._send(item, T.say("kyc_photo_not_now", gender, lang))
            self._repeat(item, row, user)
            return
        front = None
        if row["step"] == DOCUMENT_BACK_STEP:
            front = self._front(key)
            if front is None:  # the front's reading is gone (a restart, or it waited too long): the front again
                ch.store.update_onboarding(key, step=DOCUMENT_STEP, updated_at=ch._now())
                self._ask_document(item, {**row, "step": DOCUMENT_STEP}, [T.say("kyc_front_again", gender, lang)])
                return
        tries = int(row["tries"] or 0)
        phone = row["phone_e164"]
        check = read_document(ch, item, key=key, uid=None, phone=phone, typed_name=row.get("name") or "",
                              attempt=tries + 1, where="signup", front=front)
        if check.status in ("verified", "review", "refused"):
            self._forget_front(key)  # decided: the front is not needed any more
        if check.status == "capped":
            lines = [T.say("kyc_try_tomorrow", gender, lang)]
            if self._may_skip(row):  # «Later» still opens the account with the small limits
                lines.append(T.say("kyc_later_hint", gender, lang, **kyc.limits_for("none")))
                self._buttons(item, "\n".join(lines), [("o:kyc_later", T.say("btn_kyc_later", gender, lang))])
            else:
                self._send(item, "\n".join(lines))
        elif check.status == "later":
            self._ask_step(item, row, [kyc_texts.render("kyc_no_reading", gender, lang)])
        elif check.status == "back":
            self._keep_front(key, check.front)
            ch.store.update_onboarding(key, step=DOCUMENT_BACK_STEP, updated_at=ch._now())
            self._ask_back(item, {**row, "step": DOCUMENT_BACK_STEP})
        elif check.status == "verified":
            ch.accounts.kyc_hold(phone, check.record)
            self._to_pin(key, item, row, [kyc_texts.render("kyc_ok", gender, lang, **kyc.limits_for("verified"))])
        elif check.status == "review":
            ch.accounts.kyc_hold(phone, check.record)
            self._to_pin(key, item, row, [*review_lines(check, gender, lang),
                                          T.say("kyc_small_limits", gender, lang, **kyc.limits_for("pending"))])
        elif check.status == "refused":
            ch.store.end_onboarding(key)
            ch.accounts.kyc_drop(phone)
            lines = [kyc_texts.render(f"kyc_{check.reason}", gender, lang), T.say("kyc_stopped", gender, lang)]
            if check.reason != "under_age":  # another document can change the answer; another birthday cannot
                lines.append(T.say("kyc_start_again", gender, lang))
            self._send(item, "\n".join(lines))
        else:  # retry: why, and the question again (of the back, while its front is still good)
            step = row["step"]
            if step == DOCUMENT_BACK_STEP and check.reason == "sides_differ":
                step = DOCUMENT_STEP  # two different cards: both sides again, the front first
            if step != DOCUMENT_BACK_STEP:
                self._forget_front(key)  # else the held front stays, timed from when it was read
            ch.store.update_onboarding(key, step=step, tries=tries + 1, updated_at=ch._now())
            lead = [kyc_texts.render(f"kyc_{check.reason}", gender, lang),
                    T.say("kyc_attempt", gender, lang, attempt=tries + 1, attempts=kyc.MAX_ATTEMPTS)]
            self._ask_step(item, {**row, "step": step}, lead)

    # ------------------------------------------------------------ verifying an account later

    def wants_verification(self, text: str) -> bool:
        return names.normalize(text or "") in VERIFY

    def verifying(self, key: str) -> dict | None:
        """The open «verify my account» step of this sender, or None (none open, or left idle too long)."""
        self._purge_fronts()
        row = self.ch.store.onboarding(key)
        if row is None or row["step"] not in VERIFY_STEPS:
            return None
        if self.ch._now() - float(row["updated_at"]) > IDLE_RESTART_SECONDS:
            self.ch.store.end_onboarding(key)
            self._forget_front(key)
            return None
        return row

    def start_verification(self, key: str, item: Inbound, user: dict, lang: str, gender: str) -> None:
        """«وثّق حسابي»: an account at level none or pending is asked for the photo. Any other account is told
        where it stands, and nothing is opened."""
        ch = self.ch
        level = user.get("kyc_level")
        if level == "verified":
            self._send(item, T.say("kyc_already_verified", gender, lang, **kyc.limits_for(level)))
            return
        if level is None:  # an account from before the identity check: its limits are its own
            self._send(item, T.say("kyc_not_needed", gender, lang))
            return
        if ch.kyc_mode == "off":
            self._send(item, T.say("kyc_unavailable", gender, lang))
            return
        if ch.accounts.kyc_under_age_hold(user):  # a person decides first: no later document replaces that answer
            self._send(item, T.say("kyc_under_age_hold", gender, lang))
            return
        now = ch._now()
        self._forget_front(key)
        ch.store.start_onboarding(key, user["phone_e164"], VERIFY_STEP, lang, now)
        ch.store.update_onboarding(key, name=user.get("name"), gender=gender)
        self._send(item, "\n".join([T.say("kyc_ask", gender, lang), T.say("kyc_notice", gender, lang),
                                    T.say("kyc_verify_cancel_hint", gender, lang)]))

    def cancel_verification(self, key: str, item: Inbound, lang: str, gender: str) -> None:
        self.ch.store.end_onboarding(key)
        self._forget_front(key)
        self._send(item, T.say("kyc_verify_cancelled", gender, lang))

    def verify_file(self, key: str, item: Inbound, row: dict, lang: str, gender: str) -> None:
        """A file that is not a photo (a PDF) while «verify my account» waits: why, and the open question again.
        Nothing is read and no try is lost. The channel calls this for an Inbound of kind "document"."""
        ask = (kyc_texts.render("kyc_back_needed", gender, lang) if row["step"] == VERIFY_BACK_STEP
               else T.say("kyc_ask", gender, lang))
        self._send(item, "\n".join([kyc_texts.render("kyc_image_type", gender, lang), ask,
                                    T.say("kyc_verify_cancel_hint", gender, lang)]))

    def verify_photo(self, key: str, item: Inbound, row: dict, user: dict, lang: str, gender: str) -> None:
        """A photo from an account whose «verify my account» step is open: read, decided, and the level set."""
        ch = self.ch
        uid = user["id"]
        tries = int(row["tries"] or 0)
        front = None
        if row["step"] == VERIFY_BACK_STEP:
            front = self._front(key)
            if front is None:  # the front's reading is gone: the front again, no try lost
                ch.store.update_onboarding(key, step=VERIFY_STEP, updated_at=ch._now())
                self._send(item, "\n".join([T.say("kyc_front_again", gender, lang), T.say("kyc_ask", gender, lang)]))
                return
        # matched against the name typed at sign-up (or set by an admin), never one the person renamed it to since
        check = read_document(ch, item, key=key, uid=uid, phone=user["phone_e164"],
                              typed_name=ch.accounts.kyc_match_name(uid), attempt=tries + 1, where="verify",
                              front=front)
        if check.status in ("verified", "review", "refused"):
            self._forget_front(key)
        if check.status == "capped":
            ch.store.end_onboarding(key)
            self._forget_front(key)
            self._send(item, T.say("kyc_try_tomorrow", gender, lang))
        elif check.status == "later":
            self._send(item, kyc_texts.render("kyc_no_reading", gender, lang))
        elif check.status == "back":
            self._keep_front(key, check.front)
            ch.store.update_onboarding(key, step=VERIFY_BACK_STEP, updated_at=ch._now())
            self._send(item, "\n".join([kyc_texts.render("kyc_back_needed", gender, lang),
                                        T.say("kyc_verify_cancel_hint", gender, lang)]))
        elif check.status == "verified":
            ch.store.end_onboarding(key)
            limits = ch.accounts.kyc_apply(uid, check.record)
            self._send(item, kyc_texts.render("kyc_ok", gender, lang, **limits))
        elif check.status == "review":
            ch.store.end_onboarding(key)
            if check.reason == "under_age":  # payments stop now, not after the review
                ch.accounts.kyc_freeze_under_age(uid, check.record)
                ch.accounts.mark_delivered([n["id"] for n in ch.accounts.pending_notices(uid) if n["kind"] == "frozen"])
            else:
                ch.accounts.kyc_apply(uid, check.record)
            self._send(item, "\n".join(review_lines(check, gender, lang)))
            self.tell_manager_review(user)
        elif check.status == "refused":
            ch.store.end_onboarding(key)
            self._send(item, "\n".join([kyc_texts.render(f"kyc_{check.reason}", gender, lang),
                                        T.say("kyc_unchanged", gender, lang)]))
        else:
            step = row["step"]
            if step == VERIFY_BACK_STEP and check.reason == "sides_differ":
                step = VERIFY_STEP
            if step != VERIFY_BACK_STEP:
                self._forget_front(key)  # else the held front stays, timed from when it was read
            ch.store.update_onboarding(key, step=step, tries=tries + 1, updated_at=ch._now())
            lines = [kyc_texts.render(f"kyc_{check.reason}", gender, lang),
                     T.say("kyc_attempt", gender, lang, attempt=tries + 1, attempts=kyc.MAX_ATTEMPTS)]
            if step == VERIFY_BACK_STEP:
                lines.append(kyc_texts.render("kyc_back_needed", gender, lang))
            self._send(item, "\n".join(lines))

    def tell_manager_review(self, user: dict) -> None:
        if self.ch.settings.manager:
            self.ch._notify_manager(T.say("manager_kyc_review", name=user.get("name") or "",
                                          number=phones.display(user.get("phone_e164"))))

    # ------------------------------------------------------------ the PIN

    def _on_pin(self, key: str, item: Inbound, row: dict, user: dict | None, text: str, norm: str) -> None:
        lang, gender = row["lang"], row.get("gender") or "m"
        status, pin = pins.check(text if item.kind == "text" else "", row["phone_e164"])
        if status != pins.OK:
            tries = int(row["tries"] or 0) + 1
            if tries >= MAX_PIN_TRIES:
                self.ch.store.update_onboarding(key, tries=0, updated_at=self.ch._now())
                self.ch.accounts.log("system", "signup_pin_retry", None, None, {"step": "pin"})
                self._send(item, T.say("pin_restart", gender, lang))
                return
            self.ch.store.update_onboarding(key, tries=tries, updated_at=self.ch._now())
            self._send(item, T.say("pin_weak" if status == pins.WEAK else "pin_not_digits", gender, lang))
            return
        self.ch.store.update_onboarding(key, step="pin_again", pin_hash_tmp=pins.hash_pin(pin),
                                        updated_at=self.ch._now())
        self._send(item, T.say("pin_again", gender, lang))

    def _on_pin_again(self, key: str, item: Inbound, row: dict, user: dict | None, text: str, norm: str) -> None:
        lang, gender = row["lang"], row.get("gender") or "m"
        pin = pins.digits_of(text) if item.kind == "text" else None
        if pin is None or not pins.matches(pin, row.get("pin_hash_tmp")):
            tries = int(row["tries"] or 0) + 1
            self.ch.store.update_onboarding(key, step="pin", pin_hash_tmp=None, tries=tries if tries < MAX_PIN_TRIES else 0,
                                            updated_at=self.ch._now())
            self._send(item, T.say("pin_mismatch", gender, lang))
            return
        self._finish(key, item, row, user, row["pin_hash_tmp"])

    def _finish(self, key: str, item: Inbound, row: dict, user: dict | None, pin_hash: str) -> None:
        ch = self.ch
        lang, gender = row["lang"], row.get("gender") or "m"
        e164 = row["phone_e164"]
        name = row.get("name") or (user or {}).get("name") or ""
        # A new account with the identity step on gets a level: what its document check left, or "none". Its
        # daily limit is the level's (kyc.policy), set in code. With the step off nothing here changes.
        # SIGNUP_KYC=required whose step cannot run (no reader, no pepper) still gives the level "none": the
        # operator asked for a document before the full limits, so the full limits never come without one.
        record = None
        if user is None:
            record = ch.accounts.kyc_held(e164)
            if record is None and ch.kyc_mode != "off":
                record = bare_record(ch, "none", "not_asked")
            elif record is None and ch.settings.signup_kyc == "required":
                record = bare_record(ch, "none", "unavailable")
        daily_limit = kyc.limits_for(record["level"])["daily_limit"] if record else ch.settings.default_daily_limit
        result = ch.wallet.open_account(e164, name, gender=gender, language=lang, pin_hash=pin_hash,
                                        wa_name=item.name, signup_credit=ch.settings.signup_credit_iqd,
                                        daily_limit=daily_limit)
        ch.store.end_onboarding(key)
        profile = result["user"]
        uid = profile["id"]
        if record is not None and result.get("created"):
            ch.accounts.kyc_apply(uid, record, drop_phone=e164)
            ch.accounts.touch_inbound(uid)  # they wrote just now: a decision on a review can reach them at once
            if record["level"] == "pending":
                self.tell_manager_review({"name": name, "phone_e164": e164})
        elif record is not None:
            ch.accounts.kyc_drop(e164)
        ch.store.add_sender(key, uid, ch._now())
        ch.store.update_sender(key, user_id=uid, greeted=1)
        ch.forget(key)
        if user is not None and user.get("status") != "invited":  # a PIN reset: only the PIN changed
            self._send(item, T.say("pin_set_done", gender, lang, balance=int(profile.get("balance") or 0)))
            ch.accounts.mark_delivered([n["id"] for n in ch.accounts.pending_notices(uid) if n["kind"] == "pin_reset"])
            return
        lines = [T.say("done", gender, lang, number=phones.display(e164), balance=int(profile.get("balance") or 0))]
        if record is not None and result.get("created") and record["reason"] == "unavailable":
            lines.append(T.say("kyc_small_limits", gender, lang, **kyc.limits_for("none")))
        if result.get("claimed"):
            lines.append(T.say("claimed_intro", gender, lang))
            lines += [T.say("claimed_line", gender, lang, amount=c["amount"], name=c["sender_name"],
                            last4=c["sender_last4"]) for c in result["claimed"]]
        self._send(item, "\n".join(lines))
        ch.store.count(ch._day(), "signups")
        log.info("account opened (user %s)", uid)
        if ch.settings.manager and result.get("created"):
            ch._notify_manager(T.say("manager_signup", name=name, number=phones.display(e164)))
        ch.notifier.after_turn()  # the senders of claimed holds hear about it (inside their window)

    def _repeat(self, item: Inbound, row: dict, user: dict | None) -> None:
        """Say the current step again (after a message that is not an answer)."""
        lang, gender, step = row["lang"], row.get("gender") or "m", row["step"]
        if step == "offer":
            self._offer(item, lang, row["phone_e164"])
        elif step == "name_confirm":
            self._buttons(item, T.say("name_confirm", "m", lang, name=row.get("name") or ""),
                          [("o:name_yes", T.say("btn_yes", "m", lang)),
                           ("o:name_other", T.say("btn_other_name", "m", lang))])
        elif step == "name":
            self._send(item, T.say("ask_name", "m", lang))
        elif step == "gender":
            self._ask_gender(item, lang)
        elif step in DOCUMENT_STEPS:
            self._ask_step(item, row)
        elif step == "pin":
            self._send(item, T.say("ask_pin", gender, lang))
        else:
            self._send(item, T.say("pin_again", gender, lang))

    # ------------------------------------------------------------ helpers

    def _lang_of(self, item: Inbound) -> str:
        return language.detect(item.text or "", language.DEFAULT) if item.kind == "text" else language.DEFAULT

    def _send(self, item: Inbound, text: str) -> None:
        self.ch._send_text(item, text)

    def _buttons(self, item: Inbound, body: str, buttons: list[tuple[str, str]]) -> None:
        self.ch._send_buttons(item, body, buttons)


def review_lines(check: DocumentCheck, gender: str, lang: str) -> list[str]:
    """What a person is told when a human has to look: why, in the words of kyc.texts where it has them."""
    if check.reason == "duplicate_document":
        return [T.say("kyc_duplicate_document", gender, lang)]
    if check.reason == "too_many_attempts":
        # not the last photo's own text: every retry text says «take it again», and this is the end of the tries
        return [kyc_texts.render("kyc_too_many_attempts", gender, lang, attempts=kyc.MAX_ATTEMPTS)]
    if check.reason == "under_age":
        return [kyc_texts.render("kyc_under_age", gender, lang), T.say("kyc_under_age_paused", gender, lang)]
    return [kyc_texts.render(f"kyc_{check.reason}", gender, lang)]


def describe(row: dict[str, Any]) -> str:
    """An onboarding row for the admin page (never the PIN hash)."""
    return f"{phones.display(row.get('phone_e164'))} · {row.get('step')} · {row.get('lang')}"

"""Orchestrator: turns one user message (or one button tap) into the next reply.

Guiding rule: the core understands and talks; deterministic code touches the money.

- The core (closed source, or core.stub.StubCore offline) returns an Intent that points at the
  user's words. Every amount and payee it points at is checked against what the user actually
  wrote; if it is not there, we ask instead of guessing.
- Amounts are parsed by payments.amounts, payees resolved by payments.names, bills and accounts
  read from the wallet, and payments.rules decides what is allowed. Unclear -> one question.
- A payment is shown as a confirmation card (payments.quote) backed by a wallet quote. One open
  card per user; any new text voids it. Typed "إي" never pays: only handle_button with the card's
  quote id and the PIN executes. A PIN typed in the chat after we asked for one is caught here
  and never passed to the core.
- Execute uses the quote id as the idempotency key. A timeout or 5xx means UNKNOWN: we retry with
  the same key, then read the quote back, and never call a payment failed until the wallet says so.
- A bill's amount is set by the biller and a bill is paid in full (the wallet takes nothing else). If the user's
  words state another amount or a part, no card yet: we say so and ask for a yes to the full amount. The card that
  follows the yes keeps one warning line. A month the user names («شهر ستة») picks that month's bill, or we ask.
- Every reply is in the user's language, decided per message by payments.language (Iraqi, Modern
  Standard Arabic for any other Arabic, or English); a confident hint from the core (Intent.language)
  overrides it. Reply.lang says which one, so a channel can add its own words (and voice) in it.
- Phone numbers never reach the core: each Iraqi mobile in a message becomes a placeholder word
  (accounts.phones.mask) before the core reads it, and the placeholder the core points at is mapped back
  here. Money can go to any number: to its account, or (real accounts) held for it until it signs up.
- With real accounts (`accounts`, the WhatsApp channel), the account's state is checked first (a frozen
  account or a blocked PIN pays nothing), relation words are kept on the contact, a new payee with an
  account is saved to the user's contacts after the payment, and «history» / «add_contact» go to
  agent.manage. The core gets the user's vocabulary (saved names, bill labels, billers; never a phone)
  and the last few turns, and knows when a message is a voice-note transcript.

Public interface (used by agent/demo.py, eval/run_eval.py and any UI):
    Session(wallet, core=None, *, clock=None)   core None -> core.loader.load_core() (StubCore by default)
    Session.handle_text(user_id, text) -> Reply
    Session.handle_button(user_id, quote_id, button, pin=None) -> Reply
        (voice=True: the channel's voice approval, no PIN; the wallet pays only on its own grant for that quote)
    Session.handle_voice(user_id, audio, mime="audio/ogg") -> Reply   (Reply.heard = the transcript)
    Session.reconcile(user_id) -> Reply
    Session.handle_option(user_id, option_id) -> Reply   a tapped option, resolved here against the open question
    Session.menu(user_id) -> Reply                       the main menu, as options
    Session.explain(user_id) -> Reply                    what the code checked on the last turn, in the user's language
    Session.talk(user_id, situation, *, text="", facts=None, required=(), fallback) -> str
                                                         a line in a conversing core's words, else `fallback`
    Session.talk_history(user_id) / restore_talk_history(user_id, turns)   that writer's memory, for a channel to keep

A conversing core (core.interface.ConversingCore) writes the words of free chat and of the questions; the code still
decides everything else. It gets the situation, the template as reference with placeholders for the facts, the last
TALK_TURNS turns (digits masked, never a PIN, the code's actions as short notes) and never a value: its placeholders
are filled in here, and a reply with a number of its own, a placeholder it was not given, a required one missing, or
the same words as the last reply gives the template. kind, ask_field, options, the card and every outcome line stay
the code's, so the eval's scoring cannot move. Questions whose exact words matter (TALK_TEMPLATE_ONLY) never go to it.
With a conversing core a chat reply carries the menu only from the second unclear turn in a row; without one
(StubCore) everything is as before.

Options the user can tap. A question with a closed set of answers carries them (Reply.options): which saved contact,
which bill, which month, which of two amounts, which network, yes or no. An option id is opaque («opt_…») and means
something only on this server, for this user, while that question is open: the title a client sends back is never
read. A tap runs the same deterministic code as the typed answer, with no model call, and can never execute, confirm
or carry a PIN: only handle_button with the card's quote id and the PIN moves money. An old or unknown id gets a
polite «this list is old», and the open question again.

Showing the work. Every turn records what the code checked, in order, with the real values and where each came from
(Reply.evidence, a list of Check): the user's amount words and the rule that read them, how the payee words matched a
saved contact, the balance, the daily limit, the wallet's quote, and after the tap the transaction id and its state
read back from the wallet. explain() says them as short lines; «ليش» / "why" asks for them in the chat. No check ever
holds a PIN, a whole phone number or another person's balance. A reply says «done» only from the wallet's own record
of the quote: state SUCCEEDED with a transaction id, and a second read that does not contradict it.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import os
from urllib.parse import quote as urlquote

from accounts import phones
from agent import pins
from agent.wallet_client import WalletClient, WalletError, WalletUnavailable
from core.interface import PAYMENT_ACTIONS, Core, Intent
from kyc import texts as kyc_texts
from payments import amounts, arabizi, language, names, periods, refund, rules, scam
from payments.messages import catalog, format_iqd, format_period, month_name, render
from payments.quote import BUTTONS_BY_LANG, Card, QuoteDetails, render_card, spoken_summary
from payments.text import fold

BAGHDAD = timezone(timedelta(hours=3))
EXECUTE_ATTEMPTS = 2  # the first call plus one retry with the same idempotency key
NO_LIMIT = 10**12
FINAL_STATES = {"SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"}
TXN_STATUS = {"succeeded": "SUCCEEDED", "held": "SUCCEEDED", "reversed": "FAILED", "pending": "EXECUTING",
              "unknown": "UNKNOWN"}
HISTORY_TURNS = 4  # turns of the conversation the core sees (never a PIN)
TALK_TURNS = 20  # turns a conversing core sees when it writes a reply (never a PIN, digits masked)
MENU_AFTER_UNCLEAR = 2  # a conversing core's chat reply carries the menu from this many unclear turns in a row
# Questions whose exact words matter (option lists, the PIN and button steps, a short check of a name or an amount):
# always the template, never rewritten by a conversing core.
TALK_TEMPLATE_ONLY = frozenset({
    "ask_which_payee", "ask_biller", "ask_period", "ask_amount_choice", "ask_operator_mismatch", "ask_pin",
    "press_button", "unknown_payee", "unknown_payee_or_number", "ask_amount_exact", "ask_usd", "ask_confirm_payee",
    "ask_bill_full", "ask_bill_full_part"})
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_ANY_DIGIT = re.compile(r"\d")
_ACTION_NOTE = re.compile(r"^\s*\[[^\]\n]*\]\s*")  # the code's note in front of a remembered reply
# The open layer's own backstop on a written reply (whatever the core checks): outside a done_ situation no reply
# may say that money moved, arrived, was paid, deducted or sent; no reply ever promises money back. Matched on
# folded text, perfective forms and participles, so «تريد تدز؟» passes and «دزيناها», «انخصمت», «صارت مدفوعه» do
# not. A wording that the code's own text (the reference) already uses is not counted.
_AR = "ء-ي"
_MONEY_MOVED_AR = re.compile(
    rf"(?<![{_AR}])[وف]?(?:"
    rf"حول(?:ت|نا)[{_AR}]*|انحول[{_AR}]*|تحولت[{_AR}]*|"
    rf"دفع(?:ت|نا)[{_AR}]*|اندفع[{_AR}]*|تدفعت[{_AR}]*|(?:ال)?مدفوع[{_AR}]*|"
    rf"دزي(?:ت|نا)[{_AR}]*|اندز[{_AR}]*|"
    rf"(?:ان)?خصم(?:ت|نا)[{_AR}]*|انخصم[{_AR}]*|"
    rf"ا?رسل(?:ت|نا)[{_AR}]*|انرسل[{_AR}]*|"
    rf"شحن(?:ت|نا)[{_AR}]*|انشحن[{_AR}]*|"
    rf"وصل(?:ت|ك|ج|ه|ها|و|وا|تك|تج|ته|تها|تله|تلها)?|اوصلت[{_AR}]*|"
    rf"تمت?|اكتمل[{_AR}]*|نجح[{_AR}]*|خلصت(?:ها|ه|لك|لج|ك|ج)[{_AR}]*|خلصنا(?:ها|ه)"
    rf")(?![{_AR}])")
_MONEY_MOVED_EN = re.compile(
    r"\b(?:sent|paid|transferred|went through|gone through|on (?:its|the|their) way|deducted|debited|credited|"
    r"charged|completed|processed|successful(?:ly)?|done|arrived|delivered|received|topped up)\b")
_REFUND_AR = re.compile(r"استرجاع|استرداد|[تني]سترد|تعويض")
_REFUND_MONEY_AR = re.compile(r"فلوس|مبلغ|مصاري|رصيد")
_REFUND_EN = re.compile(r"\b(?:refund\w*|reimburs\w*|money back|get it back|reversed|reversal)\b")


def _claims_money_moved(text: str, reference: str = "", *, done: bool = False) -> bool:
    """Whether a written reply says money moved (unless `done`: a done_ situation) or promises money back."""
    said, given = fold(_PLACEHOLDER.sub(" ", text or "")), fold(_PLACEHOLDER.sub(" ", reference or ""))

    def new(pattern: re.Pattern) -> bool:
        known = {m.group(0) for m in pattern.finditer(given)}
        return any(m.group(0) not in known for m in pattern.finditer(said))

    if new(_REFUND_AR) or new(_REFUND_EN):
        return True
    if "رجع" in said and _REFUND_MONEY_AR.search(said) and not ("رجع" in given and _REFUND_MONEY_AR.search(given)):
        return True  # «الفلوس راح ترجعلك», «رجعتلك فلوسك»
    return not done and (new(_MONEY_MOVED_AR) or new(_MONEY_MOVED_EN))
PIN_RESET_COOLDOWN_IQD = int(os.environ.get("PIN_RESET_COOLDOWN_IQD") or 100_000)
SERVICE_NUMBER = re.sub(r"\D", "", os.environ.get("SERVICE_NUMBER") or "9647714899059")
VOICE_NAME_MATCH = 0.75  # a heard name this close to exactly one saved name is that contact (the card still asks)
REFUSAL_SLOTS = {"hold_limit": "limit", "hold_cap": "cap"}
BILLS_WORD = {"iq": "فواتير", "msa": "فواتير", "en": "bill"}

BUTTON_IDS = {b.label: b.id for buttons in BUTTONS_BY_LANG.values() for b in buttons} | {
    b.id: b.id for b in BUTTONS_BY_LANG["iq"]} | {"/pay": "confirm", "/change": "change", "/cancel": "cancel"}


def _norm_set(*words: str) -> set[str]:
    return {names.normalize(w) for w in words}


YES_WORDS = _norm_set("اي", "إي", "ايي", "ايه", "اكيد", "نعم", "صح", "تمام", "اوك", "ok", "yes", "موافق",
                      "ادفع", "ادفعها", "دزها", "حولها", "يلا", "هو", "هي", "هذا", "هاي", "زين",
                      # English and MSA: a typed yes keeps the card in every language (it still never pays)
                      "okay", "yeah", "yep", "yup", "sure", "confirm", "confirmed", "correct", "right", "goahead",
                      "doit", "أؤكد", "أوافق", "موافقة", "حسناً", "حسنا", "بالتأكيد", "أكيد", "طبعاً", "طبعا")
# Politeness around a yes («yes please», «نعم من فضلك») is not a new request.
YES_FILLERS = _norm_set("please", "pls", "plz", "من", "فضلك", "فضلكِ", "لو", "سمحت", "سمحتي", "رجاءً", "رجاء")
YES_PHRASES = {"go ahead": "goahead", "do it": "doit"}
NEGATIONS = _norm_set("مو", "مش", "موش", "لا", "ما", "not", "no", "isn't", "nope")
ORDINALS = [
    _norm_set("1", "الاول", "الأولى", "اول", "اولى", "الواحد", "واحد", "١", "first", "1st", "one"),
    _norm_set("2", "الثاني", "الثانية", "ثاني", "ثانية", "التاني", "التانية", "٢", "second", "2nd", "two"),
    _norm_set("3", "الثالث", "الثالثة", "ثالث", "ثالثة", "التالت", "التالتة", "٣", "third", "3rd", "three"),
    _norm_set("4", "الرابع", "الرابعة", "رابع", "رابعة", "٤", "fourth", "4th", "four"),
]
ORDINAL_FILLERS = _norm_set("the", "option", "number", "no.", "#", "رقم")
# Words that name a kind of bill. Matching also uses each account's biller name and label.
BILL_KIND_WORDS = {
    "electricity": _norm_set("كهرباء", "كهربا", "كهربه", "وطنية", "الوطنية"),
    "generator": _norm_set("مولدة", "مولده", "مولد", "مولدات", "امبير", "امبيرات"),
    "internet": _norm_set("نت", "انترنت", "انترنيت", "واي فاي"),
    "water": _norm_set("ماي", "ماء", "مياه", "اسالة", "مي"),
}
ALL_KIND_WORDS = set().union(*BILL_KIND_WORDS.values())
# «ادفع الفاتورة», «pay the bill»: these name no biller, so the question is which bill.
GENERIC_BILL_WORDS = _norm_set("فاتورة", "فاتورتي", "لفاتورة", "فواتير", "فواتيري", "لفواتير")
# A bill of someone outside the family (neighbour, friend): never the user's own saved account.
# Whole tokens only, so «الشهر الجاري» (this month) is not «جاري» (my neighbour).
OTHER_PERSON_WORDS = _norm_set(
    "جاري", "لجاري", "جارتي", "لجارتي", "جارنا", "لجارنا", "جيراننا", "لجيراننا", "جيرانا", "الجيران",
    "للجيران", "جيرانّا", "صديقي", "لصديقي", "صديقتي", "لصديقتي", "صاحبي", "لصاحبي", "رفيقي", "لرفيقي", "ربعي",
    "لربعي")
OPERATOR_PREFIXES = {"zain": ("078", "079"), "asiacell": ("077",), "korek": ("075",)}
OPERATOR_WORDS = {names.normalize("زين"): "zain", names.normalize("آسيا"): "asiacell",
                  names.normalize("آسياسيل"): "asiacell", names.normalize("كورك"): "korek",
                  "zain": "zain", "asia": "asiacell", "asiacell": "asiacell", "korek": "korek"}
OPERATOR_NAMES = {"zain": "زين", "asiacell": "آسياسيل", "korek": "كورك"}
OPERATOR_NAMES_EN = {"zain": "Zain", "asiacell": "Asiacell", "korek": "Korek"}
# Words the orchestrator puts around options and amounts, per language.
LIST_WORDS = {
    "iq": {"sep": "، ", "or": " لو ", "ends": "الرقم ينتهي بـ {last4}", "dinar": "دينار"},
    "msa": {"sep": "، ", "or": " أو ", "ends": "الرقم المنتهي بـ {last4}", "dinar": "دينار"},
    "en": {"sep": ", ", "or": " or ", "ends": "number ending in {last4}", "dinar": "dinars"},
}
DESCRIBE = {
    "iq": {"transfer": "التحويل لـ {name}", "bill": "فاتورة {biller}", "topup": "شحن الرصيد", "other": "الطلب الثاني"},
    "msa": {"transfer": "التحويل إلى {name}", "bill": "فاتورة {biller}", "topup": "شحن الرصيد",
            "other": "الطلب الثاني"},
    "en": {"transfer": "the transfer to {name}", "bill": "the {biller} bill", "topup": "the top-up",
           "other": "the second request"},
}
PREFIXES = ("ل", "ب", "و", "لل", "ال", "بال", "وال", "ول", "وب")
RELATION_EN = {names.normalize(k): v for k, v in {
    "ابني": "son", "بنتي": "daughter", "اخوي": "brother", "اخويه": "brother", "اختي": "sister", "امي": "mother",
    "يمه": "mother", "ابوي": "father", "بويه": "father", "زوجي": "husband", "رجلي": "husband", "مرتي": "wife",
    "زوجتي": "wife", "خالي": "uncle", "عمي": "uncle", "خالتي": "aunt", "عمتي": "aunt", "جدي": "grandfather",
    "جدتي": "grandmother"}.items()}
EN_MENTION_FILLERS = {"to", "my", "for", "the", "a", "an", "bill", "account", "of", "our", "me"}
# A yes to the full bill may say so: «إي كاملة», «كلها», "the whole amount", «تأكيد». Any other word in it is no yes.
WHOLE_WORDS = _norm_set("كاملة", "كامل", "كلها", "كله", "كاملا", "full", "whole", "all",
                        "اوكي", "اوكيه", "تأكيد", "أكد", "اكد", "approved", "approve")
WHOLE_FILLERS = _norm_set("المبلغ", "الفاتورة", "amount", "bill", "the", "pay", "it", "of")
# A part of a bill, in words: «نصها», «جزء منها», «والباقي بعدين», "half of it", "the rest later".
PART_WORDS = _norm_set("نصها", "نصفها", "ربعها", "ثلثها", "ثلث", "جزء", "جزئيا", "بعضها", "الباقي", "والباقي", "باقي", "البقية",
                       "والبقية", "شوية", "شويه", "قسم", "دفعة", "منها", "half", "part", "partial", "partly", "partially",
                       "rest", "remainder", "some")
# The same for a transfer, where «نص مليون» is an amount, so only the shapes that point back at another sum count.
SHARE_WORDS = _norm_set("نصها", "نصفها", "نصه", "نصفه", "ربعها", "ربعه", "ثلثها", "ثلثه", "الباقي", "والباقي", "باقي", "البقية",
                        "half", "rest", "remainder")
# All a message may hold, beside the amount, for the amount to be the only thing it changes on an open card or says to
# «how much?». Nothing here names, points at or rules out anyone: «غيره» and «بدله» also mean «somebody else», «مو» refuses.
AMOUNT_ONLY_WORDS = _norm_set(
    "لا", "لاء", "اي", "إي", "نعم", "غلطت", "غلطانة", "غلطان", "غلط", "بالغلط", "اخطأت", "اقصد", "قصدي", "عفوا", "اسف", "اسفة",
    "خليها", "خليه", "خلوها", "خلي", "سويها", "سويه", "عدلها", "عدله", "طيب", "يعني", "بس", "فقط", "هسه", "رجاء", "رجاءً", "لطفا",
    "المبلغ", "مبلغ", "بمبلغ", "دينار", "الف", "الاف", "ألف", "آلاف", "تلاف", "no", "nope", "oops", "sorry", "my", "bad", "mistake",
    "i", "m", "mean", "meant", "make", "it", "change", "to", "the", "that", "amount", "actually", "instead", "just", "only",
    "please", "pls", "dinar", "dinars", "iqd", "la", "ya3ni")
# The words of a hedge or a choice stay with the amount: the amount rules ask about those.
AMOUNT_ONLY_WORDS |= {names.normalize(w) for w in (*amounts.APPROX_WORDS, *amounts.OR_WORDS, *amounts.USD_WORDS)}
# What rejects something: only these refuse the word that follows. «لا لزينب» says no to the card and names who is meant.
NOT_WORDS = _norm_set("مو", "مش", "موش", "مب", "ليس", "ومو", "ومش", "وموش", "not", "mo", "mu", "mou", "mish", "msh")
# Talk of the person being wrong or somebody else: «غلطت بالاسم», «لواحد ثاني», «لغيره», "wrong person", «مو هذا».
SOMEONE_ELSE_WORDS = _norm_set("الاسم", "بالاسم", "الشخص", "شخص", "ثاني", "ثانية", "غيره", "غيرها", "لغيره", "لغيرها", "لواحد", "لوحد",
                               "person", "name", "wrong", "someone", "somebody", "else", "other", "another")
THAT_ONE_WORDS = _norm_set("هذا", "هذي", "هاي", "هو", "هي", "him", "her", "this", "that")
# Between «مو» and the thing it refuses: «مو ل هادي», "not to hadi".
NOT_FILLERS = _norm_set("ل", "الى", "على", "عله", "to", "for", "on", "l", "li", "la")
# A number after «رقم الاشتراك», after a month word or before «امبير» is no money.
BILL_NUMBER_NOUNS = {fold(w) for w in ("الاشتراك", "اشتراك", "العداد", "عداد", "الزبون", "المشترك", "الفاتورة", "فاتورة",
                                       "الحساب", "حساب")}
MONTH_WORDS = {fold(w) for w in ("شهر", "الشهر", "لشهر", "للشهر", "بشهر", "وشهر")}
BILL_UNIT_WORDS = {fold(w) for w in ("امبير", "أمبير", "امبيرات", "ميغا", "ميكا", "جيجا", "ampere", "amps", "amp", "mb", "gb")}
SEND_NEGATIONS = _norm_set("مو", "مش", "موش", "مب", "ليس")
# Any of these before «مو <name>» makes it no plain ruling-out: another negation, a condition, a reason.
NOT_PLAIN_BEFORE = NOT_WORDS | _norm_set("لا", "ما", "لو", "اذا", "إذا", "ازا", "ان", "إن", "لان", "لأن", "لانه")
MENTION_FILLERS = _norm_set("مال", "مالت", "بتاع")
ACCOUNT_WORDS = {fold(w) for w in ("رقم", "الرقم", "برقم", "حساب", "الحساب")}
THIS_WORDS = {fold(w) for w in ("هذا", "هاذا")}
MIN_ACCOUNT_DIGITS = 7  # a number this long is no amount of money; a shorter one equal to the account may be one

# ---- options the user can tap ----
# Ids never look like a card button («confirm:<quote id>»), an account button («m:…») or a sign-up button («o:…»):
# they start with «opt_» and hold no colon. A channel routes an id by is_option_id() alone.
OPTION_PREFIX = "opt_"
EXPLAIN_OPTION = OPTION_PREFIX + "why"  # «شلون تأكدت؟»
MENU_ROWS = ("transfer", "bill", "topup", "balance", "history", "contacts", "account", "help")
MENU_PAYMENTS = {"transfer": "transfer", "bill": "pay_bill", "topup": "topup"}
MENU_HELP_OPTION = OPTION_PREFIX + "m_help"
_MENU_OPTION = re.compile(r"^opt_m_([a-z]+)$")
_QUESTION_OPTION = re.compile(r"^opt_q([0-9a-f]{8})_(\d{1,2})$")
_OPTION_ID = re.compile(r"^opt_[a-z0-9_]{1,40}$")
MAX_OPTIONS = 10  # more than one list holds: the question stays the numbered text
RECENT_PAYEES = 3  # «to whom?» offers this many of the people paid last
PLAIN_HISTORY = 5  # rows of «my last transactions» for a wallet with no account service
FOLLOW_UP_KINDS = ("card", "refuse", "done", "failed", "unknown")  # replies that offer «how did you check?»
MENU_AFTER_KINDS = ("done", "refuse")  # and the main menu after it
# A whole message that asks for the menu, or for what was checked: read here, before the core.
MENU_WORDS = _norm_set(
    "menu", "main menu", "the menu", "show menu", "القائمة", "قائمة", "القائمة الرئيسية", "المنيو", "منيو",
    "الخيارات", "خيارات", "options", "help", "help me", "مساعدة", "المساعدة", "ساعدني", "ساعديني",
    "شنو تگدر تسوي", "شنو تكدر تسوي", "شتگدر تسوي", "شتكدر تسوي", "what can you do", "ماذا تستطيع أن تفعل")
WHY_WORDS = _norm_set(
    "ليش", "شلون تأكدت", "شلون تاكدت", "شلون تأكدتي", "شلون عرفت", "التفاصيل", "تفاصيل", "ورني التفاصيل",
    "شنو تأكدت", "شنو فحصت", "why", "details", "the details", "show details", "how did you check", "explain",
    "لماذا", "كيف تحققت", "كيف تأكدت", "ما التفاصيل")
# A word that asks whether a demo account's money is real (asks_if_real).
REAL_OR_PRETEND = _norm_set("حقيقي", "حقيقية", "حقيقيه", "الحقيقية", "الحقيقي", "وهمي", "وهمية", "وهميه", "تجريبي",
                            "تجريبية", "تجريبيه", "real", "fake", "pretend")
EASTERN_DIGITS = frozenset("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹")
_LONG_NUMBER = re.compile(r"[0-9٠-٩۰-۹][0-9٠-٩۰-۹ \-]{7,}[0-9٠-٩۰-۹]")
# Facts about a request that are worked out again on every attempt (the others stay with the request).
FRESH_FACTS = ("asked", "rules", "flags", "quote", "refused")


# ---------------------------------------------------------------- data


@dataclass(frozen=True)
class Option:
    """One answer the user can tap. `id` is opaque and resolved by Session.handle_option on this server; `title`
    is short (a saved name, a month, an amount); `description` says more where a transport has room for it (the
    number ending); `tail` is the few characters that tell two options with one title apart («0001»), which a
    transport with no room for the description must keep."""

    id: str
    title: str
    description: str = ""
    tail: str = ""


@dataclass(frozen=True)
class Text:
    """A slot of a check that is itself a message key (a rule's name, how a name matched): said in the reader's
    language when the check is."""

    key: str


@dataclass
class Check:
    """One thing the code checked during a turn. `what` names the check (the message key «why_<what>»), `value`
    holds the real values in a plain form, `source` says where they came from («user_text», «contacts»,
    «wallet:quote», «wallet:readback», «rules», «core», «code»). `slots` are the same values for the line in the
    user's language. Never a PIN, never a whole phone number."""

    what: str
    value: str
    source: str
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class Reply:
    """What the user sees after one message or tap."""

    lines: list[str] = field(default_factory=list)  # message lines (the card is separate)
    card: Card | None = None
    kind: str = "info"  # card | ask | refuse | done | failed | unknown | cancelled | info
    state: str | None = None  # wallet quote state when a quote is involved
    quote_id: str | None = None  # the card's quote id when a card is shown
    # payee | amount | biller | period | operator | change | full_amount, when kind == "ask"
    ask_field: str | None = None
    offered_amount: int | None = None  # ask_field "full_amount": the amount a yes would put on the card
    heard: str | None = None  # handle_voice only: what the voice note said (the transcript)
    lang: str = language.DEFAULT  # the language of this reply: 'iq' | 'msa' | 'en'
    options: list[Option] = field(default_factory=list)  # choices the user can tap; [] when the answer is free text
    options_title: str | None = None  # the label of the button that opens them as a list
    # "answer": the options answer the question in `lines`. "next": what the user can do next (the menu, «how did
    # you check?»): a channel may show them apart from the text, or leave them out.
    options_role: str = "answer"
    evidence: list[Check] = field(default_factory=list)  # what the code checked this turn, in order
    reference: str | None = None  # kind "done": the short reference of the wallet's transaction
    held: bool = False  # kind "done" for a transfer whose money waits as a hold (not received by anyone yet)
    warm: bool = False  # the writer's warm line closes `lines` (after an outcome): nothing more is asked after it

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def render(self) -> str:
        """Plain-text view for a terminal: message lines, card, buttons."""
        parts = list(self.lines)
        if self.card is not None:
            parts += [""] if parts else []
            parts += [self.card.text, "  ".join(f"[{b.label}]" for b in self.card.buttons)]
        return "\n".join(parts)


@dataclass
class Request:
    """One payment the user asked for, filled in step by step."""

    action: str  # transfer | pay_bill | topup
    text: str = ""  # everything the user wrote about this request (for the verbatim check)
    payee_mention: str | None = None
    contact: dict | None = None
    biller_mention: str | None = None
    account: dict | None = None
    period: str | None = None
    amount_text: str | None = None
    currency: str | None = None
    amount: int | None = None
    operator: str | None = None
    payee_phone: str | None = None  # the canonical number when the user typed one (9647XXXXXXXXX)
    relation_word: str | None = None  # saved as an alias once the user names the person
    period_settled: bool = False  # the user picked the month in answer to our question
    notes: list[str] = field(default_factory=list)  # lines to show before the next reply
    stated: list[str] = field(default_factory=list)  # the lines that state or change the request (no menu answers)
    not_mine: list[str] = field(default_factory=list)  # amounts of the other requests in the same message
    full_yes: int | None = None  # the bill amount the user said yes to, after being told a bill is paid in full
    facts: dict[str, list[Check]] = field(default_factory=dict)  # what was checked about this request, by subject

    def __post_init__(self) -> None:
        if self.text and not self.stated:
            self.stated = [self.text]


@dataclass
class Question:
    request: Request
    field: str  # payee | amount | biller | period | operator | change | full_amount
    options: list[dict] = field(default_factory=list)  # contacts, bill accounts, amounts or months offered
    nonce: str = ""  # part of this question's option ids: an id of another question, or of this one asked again, is old
    choices: list[dict] = field(default_factory=list)  # what each tappable option means, by its index (server side)
    shown: Reply | None = None  # the question as it was asked, to say again after an old list is tapped


@dataclass
class OpenCard:
    quote: dict
    request: Request
    card: Card
    view: Any = None  # what the card was built from, so it can be shown again in another language
    flags: list[str] | None = None
    warning: tuple[str, dict] | None = None  # a bill-amount warning line: message key and slots


@dataclass
class UserState:
    card: OpenCard | None = None
    question: Question | None = None
    queue: list[Request] = field(default_factory=list)  # the rest of a two-in-one message
    aliases: dict[str, str] = field(default_factory=dict)  # relation word -> contact id
    unknown: dict[str, Request] = field(default_factory=dict)  # quote id -> request, outcome unknown
    awaiting_pin: str | None = None  # quote id of the card we just asked a PIN for
    gender: str = "f"  # from the last profile read, for replies when the wallet is down
    lang: str = language.DEFAULT  # the language of the conversation, updated with every message
    voice_lang: str | None = None  # the language of the last voice note heard (None before the first)
    phone_map: dict[str, str] = field(default_factory=dict)  # placeholder -> canonical number
    tokens: dict[str, str] = field(default_factory=dict)  # canonical number -> placeholder
    history: list[dict] = field(default_factory=list)  # the last turns, for the core (no PIN, no phone)
    source: str = "text"  # "voice" while a voice-note transcript is being handled
    preheard: tuple[str, Any] | None = None  # (transcript, Intent) when the core heard and read a voice note
    scam_turns_left: int = 0  # turns during which an earlier scam cue still stops a payment to an unsaved number
    scam_line: str = "scam_warning"  # the line for that stop: fake support, or fake authority
    trail: list[Any] = field(default_factory=list)  # this turn's checks, in order: a Check, or a Request (its facts)
    explained: list[Check] = field(default_factory=list)  # the checks of the last turn that made any, for explain()
    tapped: str | None = None  # the title (ours, never the client's) of the option tapped in this turn
    # For a conversing core (Session.talk): a longer memory of the conversation, digits masked and never a PIN,
    # with short notes of what the code did («[بطاقة تحويل لـ أحمد]»); the user's last message (masked); where
    # it came from; whether the profile says the gender; unclear chat turns in a row.
    talk_history: list[dict] = field(default_factory=list)
    last_text: str = ""
    last_source: str = "text"
    gender_known: bool = False
    demo: bool = False  # the profile is a demo account (pretend money): the conversing core is told
    unclear_turns: int = 0
    chatted: bool = False  # this turn was answered as plain chat


# After a scam cue, how many more turns a payment to an unsaved number is still stopped.
SCAM_CUE_TURNS = 3

# ---------------------------------------------------------------- text helpers


def _tokens(text: str) -> list[str]:
    return names.normalize(text or "").split()


def said(fragment: str, text: str) -> bool:
    """True when `fragment` appears in the user's own words (normalised, Arabizi read as Arabic).

    The first word may carry an attached particle in the text: "أحمد" is found in "لأحمد".
    """
    frag = _tokens(fragment)
    if not frag:
        return False
    for source in {text, arabizi.to_arabic(text)}:
        words = _tokens(source)
        for i in range(len(words) - len(frag) + 1):
            head = words[i]
            if (head == frag[0] or any(head == p + frag[0] for p in PREFIXES)) \
                    and words[i + 1:i + len(frag)] == frag[1:]:
                return True
    return False


def _whole_message(text: str) -> str:
    """A message as one normalised phrase, without punctuation: «ليش؟» -> «ليش», "Why?" -> "why"."""
    return " ".join(_tokens(re.sub(r"[^\w\s]|_", " ", (text or "").lower())))


def wants_menu(text: str) -> bool:
    """The whole message asks for the menu or for help («القائمة», "menu", «مساعدة»)."""
    return _whole_message(text) in MENU_WORDS


def asks_if_real(text: str) -> bool:
    """The message asks whether the money is real («الفلوس حقيقية؟», «هذا وهمي؟», "is this real money?")."""
    return bool(set(_tokens(re.sub(r"[^\w\s]|_", " ", (text or "").lower()))) & REAL_OR_PRETEND)


def wants_explanation(text: str) -> bool:
    """The whole message asks what was checked («ليش», «شلون تأكدت؟», "why", "details")."""
    return _whole_message(text) in WHY_WORDS


def command_language(text: str, lang: str) -> str:
    """The language of a menu or «why» word. One written in Latin letters ("menu", "details", "options") is
    English whatever the language so far, as agent.manage reads an English command; any other is left to
    payments.language («لماذا» is Modern Standard Arabic, «ليش» Iraqi)."""
    if (wants_menu(text) or wants_explanation(text)) and re.search(r"[A-Za-z]", text or "") \
            and not re.search(r"[؀-ۿ]", text or ""):
        return language.ENGLISH
    return language.detect(text or "", lang)


def is_option_id(value: Any) -> bool:
    """A tapped option's id (never a card button's «confirm:<quote id>», which holds a colon)."""
    return isinstance(value, str) and bool(_OPTION_ID.fullmatch(value))


def short_reference(transaction_id: Any) -> str:
    """The last six characters of the wallet's transaction id, for a person to read out: «t_…9f3a1c» -> «9F3A1C»."""
    return re.sub(r"[^0-9A-Za-z]", "", str(transaction_id or ""))[-6:].upper()


def _safe_words(text: Any, limit: int = 60) -> str:
    """The user's own words as a check may show them: a long number is cut to its ending, and the length is capped."""
    words = _LONG_NUMBER.sub(lambda m: "…" + _digits(m.group())[-4:], str(text or "").strip())
    return words if len(words) <= limit else words[:limit - 1] + "…"


def _plain(value: Any) -> str:
    if isinstance(value, Text):
        return value.key.removeprefix("why_")
    if isinstance(value, int) and not isinstance(value, bool):
        return format_iqd(value)
    return str(value)


def _baghdad_time(moment: Any, with_date: bool = False) -> str:
    """An ISO time as the clock in Baghdad shows it («09:05», or «2026-11-02 09:05»); '' when unreadable."""
    try:
        when = _parse_time(str(moment)).astimezone(BAGHDAD)
    except (ValueError, TypeError):
        return ""
    return when.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M")


def _is_yes(text: str) -> bool:
    """A typed yes in any language («إي ادفع», «yes please», «نعم من فضلك»): only yes words and politeness."""
    lowered = re.sub(r"[^\w\s]", " ", (text or "").lower())
    for phrase, word in YES_PHRASES.items():
        lowered = re.sub(rf"\b{phrase}\b", word, lowered)
    for source in (lowered, arabizi.to_arabic(lowered)):  # English as typed, and Arabizi read as Arabic
        words = [w for w in _tokens(source) if w not in YES_FILLERS]
        if words and all(w in YES_WORDS for w in words):
            return True
    return False


def _is_full_yes(text: str) -> bool:
    """A yes to «pay the whole bill?»: «إي», «كاملة», "yes the whole amount"."""
    words = _tokens(re.sub(r"[^\w\s]", " ", (text or "").lower()))
    if set(words) & NEGATIONS:
        return False
    rest = [w for w in words if w not in WHOLE_WORDS | WHOLE_FILLERS]
    return _is_yes(" ".join(rest)) if rest else bool(set(words) & WHOLE_WORDS)


def _only_amount(text: str, amount_text: str | None = None) -> bool:
    """Nothing in the message but an amount and the words around one («لا اخطأت سويها 45 الف», "sorry I meant 45").
    Anything else (a name, «مو», "wrong person", a pay verb, «لواحد ثاني») and the message says more than an amount."""
    for source in (text, arabizi.to_arabic(text)):
        words = set(_tokens(source)) - AMOUNT_ONLY_WORDS
        words -= set(_tokens(amount_text or "")) | set(_tokens(arabizi.to_arabic(amount_text or "")))
        for _, phrase in amounts.amount_phrases(arabizi.to_arabic(source)):
            words -= set(_tokens(phrase))
        if not words:
            return True
    return False


def _refuses_right_after(fragment: str, line: str) -> bool:
    """«مو» or "not" stands right before `fragment` in the line: «مو 60», "not 60", «دز لزينب 50 مو 60», «مو ل هادي»."""
    frags = [f for f in (_tokens(fragment), _tokens(arabizi.to_arabic(fragment))) if f]
    for source in (line, arabizi.to_arabic(line)):
        words = _tokens(source)
        for frag in frags:
            for i in range(1, len(words) - len(frag) + 1):
                if words[i + 1:i + len(frag)] == frag[1:] and (words[i] == frag[0] or any(words[i] == p + frag[0] for p in PREFIXES)):
                    before = i - 1
                    while before > 0 and words[before] in NOT_FILLERS:
                        before -= 1
                    if words[before] in NOT_WORDS:
                        return True
    return False


def _ordinal(text: str) -> int | None:
    """The one option a short answer points at ("الثاني" -> 1, "the second one" -> 1), else None so we ask again.

    A negation ("مو الأول", "الأول لا", "not the first") or two ordinals ("مو الاول، الثاني") never picks one.
    """
    words = [w for w in _tokens(re.sub(r"[^\w\s#]", " ", (text or "").lower())) if w not in ORDINAL_FILLERS]
    if len(words) > 1 and "one" in words:  # «the second one»: «one» is the option, not the first
        words = [w for w in words if w != "one"]
    if not words or len(words) > 3 or any(w in NEGATIONS for w in words):
        return None
    hits = {index for index, forms in enumerate(ORDINALS) if any(w in forms for w in words)}
    return hits.pop() if len(hits) == 1 else None


def _looks_like_pin(text: str) -> bool:
    """A message that is only 4 to 8 digits (Western or Eastern), also «1234.» as speech-to-text writes it."""
    return pins.looks_like_pin(text)


def _worded_pin(text: str) -> bool:
    """«الرمز 5678», «الرقم السري 5678», «my pin is 5678»: a PIN named as one, in any language."""
    found = pins.pin_in(text)
    return found is not None and found[0] == "worded"


def _bare(word: str) -> str:
    """Drop a leading و / ب / ال / لل: "والماي" -> "ماي"."""
    for prefix in ("وال", "بال", "لل", "ال", "و", "ب"):
        if word.startswith(prefix) and len(word) - len(prefix) >= 2:
            return word[len(prefix):]
    return word


def _digits(text: Any) -> str:
    return re.sub(r"\D", "", names.normalize(str(text or "")))


# A dative word standing on its own before the person: «لـ أحمد», «ل أحمد», «إلى أحمد», "to أحمد".
DATIVE_WORDS = _norm_set("ل", "لل", "لـ", "إلى", "الى", "to")


def _dative_off(mention: str) -> str:
    """The mention without a dative word standing before the person («لـ أحمد» -> «أحمد»). A word glued to the name
    («لأحمد», «ليث») is left as it is, and a mention that is only the dative word stays."""
    words = (mention or "").split()
    while len(words) > 1 and (names.normalize(words[0]) in DATIVE_WORDS or words[0].lower() in DATIVE_WORDS):
        words = words[1:]
    return " ".join(words)


_SMALL_TALK = {"مساء الخير", "صباح الخير", "مسا الخير", "هلو", "هلا", "هلا والله", "مرحبا", "السلام عليكم",
               "شلونك", "شلونج", "شخبارك", "كيف حالك", "شكرا", "شكرا جزيلا", "مشكور", "hi", "hello", "hey",
               "good morning", "good evening", "how are you", "thanks", "thank you"}


def _not_a_name_answer(text: str) -> bool:
    """An answer to «who?» that cannot be a name: a question, a greeting or thanks, or a long sentence. A number,
    or a short answer of one to three words, is still read as the name (or the number) asked for."""
    clean = re.sub(r"[!.,،؟?…]+", " ", text or "").strip().lower()
    if not clean or re.search(r"\d", clean):
        return False
    if "?" in text or "؟" in text:
        return True
    return clean in _SMALL_TALK or len(clean.split()) > 3


def _display(mention: str) -> str:
    """A payee mention as we show it back: without the dative ل ("لميثم" -> "ميثم", «لـ أحمد» -> «أحمد»)."""
    words = _dative_off(mention).split()
    if not words:
        return ""
    if arabizi.has_latin(mention):
        while len(words) > 1 and words[0].lower() in {"l", "li", "la", "lil", "to"}:
            words = words[1:]
        return " ".join(words)
    first = words[0]
    if first.startswith("لل"):  # ل + ال: «للشعلة» -> «الشعلة», and a title stays a title: «للست نادية» -> «ست نادية»
        rest = first[2:]
        first = rest if names.normalize(rest) in names.TITLES | names.KUNYA_HEADS else "ال" + rest
    elif first.startswith("ل") and (len(first) > 3 or names.normalize(first[1:]) in names.TITLES | names.KUNYA_HEADS):
        first = first[1:].lstrip("ـ")  # short names keep their ل («ليث», «لمى»), a title does not («لست نادية»)
    return " ".join([first] + words[1:])


def _names_no_biller(mention: str) -> bool:
    """«الفاتورة», «the bill», «فواتيري»: the mention is only the word for a bill, not a biller."""
    words = [w for w in _tokens(mention) if w not in EN_MENTION_FILLERS]
    return all(_bare(w) in GENERIC_BILL_WORDS for w in _tokens(arabizi.to_arabic(" ".join(words))))


def _bill_kinds(mention: str) -> set[str]:
    """The kinds of bill a mention names («الكهرباء او النت»: two of them)."""
    words = [_bare(w) for w in _tokens(arabizi.to_arabic(mention))]
    return {kind for w in words for kind, forms in BILL_KIND_WORDS.items() if w in forms}


def _operator_of(phone: str) -> str | None:
    digits = _digits(phone)
    if digits.startswith("964"):
        digits = "0" + digits[3:]
    return next((op for op, prefixes in OPERATOR_PREFIXES.items() if digits.startswith(prefixes)), None)


def _operator_in(text: str) -> str | None:
    for word in _tokens(arabizi.to_arabic(text)) + _tokens(text):
        if _bare(word) in OPERATOR_WORDS:
            return OPERATOR_WORDS[_bare(word)]
    return None


def _previous_month(period: str) -> str:
    year, month = (int(x) for x in period.split("-"))
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year:04d}-{month:02d}"


def _pick_bill(bills: list[dict], hint: str | None) -> tuple[dict | None, str | None]:
    """The bill the user means: by month hint, else the oldest unpaid one, else the latest.

    hint: 'current' | 'previous' | 'YYYY-MM' | 'MM' (a month of any year: its latest bill) | None.
    Returns (bill or None, the period asked for or None).
    """
    if hint and re.fullmatch(r"\d{2}", hint):
        same = sorted((b for b in bills if b["period"][5:7] == hint), key=lambda b: b["period"])
        return (same[-1], same[-1]["period"]) if same else (None, hint)
    if not bills:
        return None, None
    by_period = sorted(bills, key=lambda b: b["period"])
    latest = by_period[-1]["period"]
    if hint == "current":
        target = latest
    elif hint == "previous":
        target = _previous_month(latest)
    elif hint and re.fullmatch(r"\d{4}-\d{2}", hint):
        target = hint
    else:
        unpaid = [b for b in by_period if b["status"] == "unpaid"]
        return (unpaid[0] if unpaid else by_period[-1]), None
    return next((b for b in by_period if b["period"] == target), None), target


def _month_label(period: str, lang: str | None = None) -> str:
    """'2026-06' -> 'حزيران 2026', '06' -> 'حزيران' ('June 2026' / 'June' in English)."""
    if re.fullmatch(r"\d{2}", period or ""):
        return month_name(int(period), lang)
    return format_period(period, lang)


def biller_name(source: dict | None, lang: str | None = None) -> str:
    """A biller's name from a bill account or a quote target: the English name in English when known."""
    source = source or {}
    if lang == "en" and source.get("biller_name_en"):
        return str(source["biller_name_en"])
    return str(source.get("biller_name_ar") or source.get("label_ar") or "")


def _named_months(text: str) -> set[int]:
    """The months named in the latest line of `text` that names one (Arabizi read as Arabic)."""
    for line in reversed((text or "").splitlines()):
        found = periods.months_named(line) | periods.months_named(arabizi.to_arabic(line))
        if found:
            return found
    return set()


def _target(quote: dict) -> dict:
    target = quote.get("target") or {}
    return target if isinstance(target, dict) else {}


def _details(quote: dict, lang: str | None = None) -> QuoteDetails:
    """What the card shows, taken from the wallet's quote (not from what we think we asked for)."""
    t = _target(quote)
    try:
        created = datetime.fromisoformat(quote["created_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(quote["expires_at"].replace("Z", "+00:00"))
        minutes = max(1, round((expires - created).total_seconds() / 60))
    except (KeyError, ValueError, AttributeError):
        minutes = 5
    common = {"quote_id": quote["id"], "kind": quote["kind"], "amount": quote["amount"],
              "fee": quote.get("fee", 0), "expires_in_min": minutes}
    if quote["kind"] == "transfer":
        return QuoteDetails(**common, payee_id=t.get("contact_id", ""), payee_name=t.get("display_name", ""),
                            phone=t.get("phone", ""))
    if quote["kind"] == "bill":
        return QuoteDetails(**common, payee_id=t.get("account_id", ""), payee_name=t.get("holder_name", ""),
                            biller_name=biller_name(t, lang), period=t.get("period", ""),
                            account_no=t.get("account_no", ""))
    return QuoteDetails(**common, payee_id=_digits(t.get("phone")), phone=t.get("phone", ""),
                        operator=biller_name(t, lang))


def _transfer_key(contact_id: Any, e164: Any, phone: Any = None) -> str:
    """A transfer's payee for payments.rules: «phone:<number>» whenever the number is known (a typed number and
    the contact saved from it later are the same person), else the contact id."""
    number = e164 or phones.canonical(phone) if (e164 or phone) else None
    if number:
        return f"phone:{number}"
    return str(contact_id or "")


def _payee_key(kind: str, target: Any) -> str:
    """Same key as payments.rules uses: the payee's number (or contact id), bill account id, or top-up phone."""
    if not isinstance(target, dict):
        return ""
    if kind == "transfer":
        return _transfer_key(target.get("contact_id"), target.get("phone_e164"), target.get("phone"))
    if kind == "bill":
        return str(target.get("account_id") or "")
    return _digits(target.get("phone"))


def _parse_time(text: str) -> datetime:
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- the session


class Session:
    """Conversation state for every user, over one wallet and one core."""

    def __init__(self, wallet: Any, core: Core | None = None, *, clock: Callable[[], datetime] | None = None,
                 accounts: Any = None):
        self.wallet = wallet if isinstance(wallet, WalletClient) else WalletClient(wallet)
        self.accounts = accounts  # accounts.service.Accounts for real accounts (None: synthetic demo users)
        self.manage = None
        if accounts is not None:
            from agent.manage import Manage

            self.manage = Manage(accounts, clock=clock)
        if core is None:
            from core.loader import load_core

            core = load_core()  # BILLPAY_CORE="module:Class", else the open StubCore
        self.core = core
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.users: dict[str, UserState] = {}

    def state(self, user_id: str) -> UserState:
        return self.users.setdefault(user_id, UserState())

    # ------------------------------------------------------------ public API

    def handle_text(self, user_id: str, text: str, *, source: str = "text", heard: Any = None) -> Reply:
        """One typed (or transcribed) message from the user. It also sets the reply language.

        source "voice": the text is a voice-note transcript; `heard` is the core's own reading of that voice
        note (HearingCore.hear), used instead of a second reading when the transcript holds no phone number."""
        st = self.state(user_id)
        st.lang = command_language(text or "", st.lang)
        if wants_explanation(text):  # read here, never by the core: nothing is voided, asked or changed
            return self.explain(user_id)
        masked = self._mask(user_id, text or "")
        st.source = source
        st.preheard = (masked, heard) if heard is not None and masked == (text or "") else None
        secret = _looks_like_pin(text) or _worded_pin(text)
        st.last_text, st.last_source = ("" if secret else masked), source
        try:
            reply = self._turn(user_id, lambda profile: self._on_text(user_id, masked, profile))
        finally:
            st.source, st.preheard = "text", None
        if not (_looks_like_pin(text) or _worded_pin(text)):
            self._remember_turn(user_id, masked, reply)
        return reply

    def _mask(self, uid: str, text: str) -> str:
        """Each Iraqi mobile number becomes this conversation's placeholder for it (the same number keeps
        its placeholder for the whole conversation)."""
        st = self.state(uid)
        spans = [s for s in phones.find(text) if s.e164]
        if not spans:
            return text
        out, pos = [], 0
        for span in spans:
            token = st.tokens.get(span.e164)
            if token is None:
                token = phones.token(len(st.tokens))
                st.tokens[span.e164], st.phone_map[token] = token, span.e164
            lead = "" if span.start == 0 or text[span.start - 1].isspace() else " "
            out += [text[pos:span.start], lead, token]
            pos = span.end
        out.append(text[pos:])
        return "".join(out)

    def _remember_turn(self, uid: str, masked: str, reply: Reply) -> None:
        st = self.state(uid)
        said_back = " ".join(reply.lines)[:300]
        if reply.card is not None:
            said_back = (said_back + " " + (reply.card.lines[0] if reply.card.lines else "")).strip()
        st.history = (st.history + [{"role": "user", "text": masked[:200]},
                                    {"role": "assistant", "text": said_back}])[-HISTORY_TURNS:]
        self._remember_talk(uid, masked, " ".join(reply.lines), self._action_note(uid, reply))

    def _remember_talk(self, uid: str, masked: str, said: str, note: str = "") -> None:
        """One exchange in the conversing core's memory: digits masked («#»), never a PIN, the code's action note
        in front of its reply (the store-bot way: the writer remembers what the code did)."""
        st = self.state(uid)
        user = _ANY_DIGIT.sub("#", " ".join((masked or "").split()))[:300]
        said = _ANY_DIGIT.sub("#", " ".join(((note + " " if note else "") + (said or "")).split()))[:400]
        turns = ([{"role": "user", "text": user}] if user else []) + ([{"role": "assistant", "text": said}] if said else [])
        st.talk_history = (st.talk_history + turns)[-TALK_TURNS:]

    def _action_note(self, uid: str, reply: Reply) -> str:
        """What the code did on this reply, in a few words and no number: «[بطاقة تحويل لـ أحمد]»."""
        st = self.state(uid)
        en = st.lang == "en"
        if reply.card is not None and st.card is not None:
            req = st.card.request
            if req.action == "transfer":
                who = (req.contact or {}).get("display_name") or ""
                return (f"[card: transfer to {who}]" if en else f"[بطاقة تحويل لـ {who}]").replace(" ]", "]")
            if req.action == "pay_bill":
                biller = biller_name(req.account, st.lang)
                return f"[card: {biller} bill]" if en else f"[بطاقة فاتورة {biller}]"
            return "[card: phone top-up]" if en else "[بطاقة شحن رصيد]"
        if reply.kind == "done" and reply.held:
            return "[money held until the number opens an account]" if en else "[المبلغ محجوز لحد ما يفتح حساب]"
        notes = {"done": ("[payment done]", "[دفعة تمت]"), "failed": ("[nothing was paid]", "[ما انخصم شي]"),
                 "refuse": ("[refused]", "[رفضت الطلب]"), "cancelled": ("[cancelled]", "[ألغيت]"),
                 "unknown": ("[outcome not known yet]", "[النتيجة بعدها مو معروفة]")}
        if reply.kind in notes:
            return notes[reply.kind][0 if en else 1]
        if reply.kind == "ask" and reply.ask_field:
            return f"[asked: {reply.ask_field}]" if en else f"[سألت: {reply.ask_field}]"
        return ""

    def talk_history(self, user_id: str) -> list[dict]:
        """The conversing core's memory of this conversation (digits masked, never a PIN), for a channel to keep."""
        return [dict(turn) for turn in self.state(user_id).talk_history]

    def restore_talk_history(self, user_id: str, turns: list[dict]) -> None:
        """Put back a memory kept by the channel (talk_history), e.g. after a restart. Digits are masked again."""
        clean = []
        for turn in turns or []:
            if isinstance(turn, dict) and turn.get("role") in ("user", "assistant") and str(turn.get("text") or "").strip():
                clean.append({"role": turn["role"], "text": _ANY_DIGIT.sub("#", str(turn["text"]))[:400]})
        self.state(user_id).talk_history = clean[-TALK_TURNS:]

    def start_transfer(self, user_id: str, e164: str) -> Reply:
        """«حوّل له» on a number the user sent alone: a transfer to it, asking the amount."""
        def run(profile: dict) -> Reply:
            st = self.state(user_id)
            self._void_card(user_id)
            st.queue.clear()
            token = st.tokens.get(e164) or phones.token(len(st.tokens))
            st.tokens[e164], st.phone_map[token] = token, e164
            req = Request("transfer", text=token, payee_mention=token)
            ask = self._payee(user_id, req, profile)
            if ask:
                return ask
            return self._advance(user_id, req, profile)
        return self._turn(user_id, run)

    def lang_of(self, user_id: str) -> str:
        """The language this user is being answered in ('iq' | 'msa' | 'en')."""
        return self.state(user_id).lang

    def handle_button(self, user_id: str, quote_id: str, button: str, pin: str | int | None = None, *,
                      voice: bool = False) -> Reply:
        """A tap on a card button. Only this can execute a payment.

        `voice` (the channel's voice approval): «confirm» without a PIN, which the wallet accepts only on the one-time
        grant it recorded for this card's quote. When it refuses, the card stays open and the PIN is asked for (a card
        reply for this quote, with no lines: the channel words it)."""
        self.state(user_id).last_text = ""  # a tap (and maybe a PIN): nothing for a writer to read
        reply = self._turn(user_id, lambda profile: self._on_button(user_id, quote_id, button, pin, profile,
                                                                    voice=voice))
        if reply.kind in ("done", "failed", "cancelled", "unknown", "refuse"):
            # the writer's memory learns what the code did (an outcome note, digits masked, never the PIN)
            self._remember_talk(user_id, "", " ".join(reply.lines), self._action_note(user_id, reply))
        return reply

    # Channels ask these before they route a message or a tap.
    is_option = staticmethod(is_option_id)
    command_language = staticmethod(command_language)
    wants_menu = staticmethod(wants_menu)
    wants_explanation = staticmethod(wants_explanation)

    def handle_option(self, user_id: str, option_id: str) -> Reply:
        """A tapped option (a reply button, a list row, a chip). The id is looked up here, against this user's open
        question or the menu: whatever title the client sends with it is never read. A tap goes through the same
        code as the typed answer, with no model call. It never executes, confirms or carries a PIN: a card button's
        id sent here is just an unknown id, and an option id sent to handle_button is no open card's quote."""
        option_id = str(option_id or "").strip()
        if option_id == EXPLAIN_OPTION:
            return self.explain(user_id)
        self.state(user_id).last_text = ""  # a tap: nothing was typed
        reply = self._turn(user_id, lambda profile: self._on_option(user_id, option_id, profile))
        st = self.state(user_id)
        title, st.tapped = st.tapped, None
        if title:  # the core's view of the conversation: what was tapped, by the title we gave it ourselves
            self._remember_turn(user_id, title, reply)
        return reply

    def menu(self, user_id: str) -> Reply:
        """The main menu as options (send money, pay a bill, top up, balance, last transactions, contacts, my
        account, help). It changes nothing: an open card or question stays as it is."""
        st = self.state(user_id)
        try:  # the words are said to a woman or to a man: from the profile, when the wallet answers
            st.gender = self._gender(self.wallet.user(user_id))
        except (WalletError, WalletUnavailable):
            pass
        reply = self._menu_reply(user_id)
        reply.lang = st.lang
        return reply

    def explain(self, user_id: str) -> Reply:
        """What the code checked on the last turn that checked anything, as short lines in the user's language.
        Nothing is changed: an open card stays open (it comes back with the reply), an open question stays asked."""
        st = self.state(user_id)
        checks = list(st.explained)
        if checks:
            lines = [self._t(user_id, "why_title")] + [f"• {line}" for line in self.why_lines(user_id, checks)]
        else:
            lines = [self._t(user_id, "why_nothing")]
        reply = Reply(lines, kind="info", lang=st.lang)  # no evidence of its own: this turn checked nothing new
        if st.card is not None:
            reply.card, reply.kind, reply.state = self._open_card(user_id), "card", "QUOTED"
            reply.quote_id = st.card.quote["id"]
        return reply

    def why_lines(self, user_id: str, checks: list[Check]) -> list[str]:
        """Each check as one line in this user's language and gender."""
        st = self.state(user_id)
        return [self._why_line(check, st.gender, st.lang) for check in checks]

    @staticmethod
    def _why_line(check: Check, gender: str, lang: str) -> str:
        slots = {name: render(value.key, gender, lang) if isinstance(value, Text) else value
                 for name, value in check.slots.items()}
        try:
            return render(f"why_{check.what}", gender, lang, **slots)
        except (KeyError, IndexError, ValueError):  # a check with no line of its own: its plain values
            return f"{check.what}: {check.value}"

    def source_label(self, user_id: str, source: str) -> str:
        """Where a check's values came from, in the user's words: «wallet:quote» -> «المحفظة»."""
        st = self.state(user_id)
        head = (source or "code").split(":")[0]
        key = f"why_src_{head}"
        return render(key if key in catalog(st.lang) else "why_src_code", st.gender, st.lang)

    def handle_voice(self, user_id: str, audio: bytes, mime: str = "audio/ogg") -> Reply:
        """A voice note. The core turns it into text (if it can); then it is handled like typed text.

        The reply carries the transcript in `heard`, so a UI can show what was understood. A channel
        with its own PIN guard calls transcribe() and voice_failed() itself, and checks the transcript first.
        """
        heard, intent = self.hear(user_id, audio, mime)
        if heard is None:
            return self.voice_failed(user_id, "voice_unavailable")
        if not heard:
            return self.voice_failed(user_id, "voice_unclear")
        reply = self.handle_text(user_id, heard, source="voice", heard=intent)
        reply.heard = heard
        return reply

    def transcribe(self, user_id: str, audio: bytes, mime: str = "audio/ogg") -> str | None:
        """What a voice note says ("" when nothing was heard), or None when the core cannot hear at all."""
        st = self.state(user_id)
        transcribe = getattr(self.core, "transcribe", None)
        if not callable(transcribe):
            return None
        context = {"user_id": user_id, "gender": st.gender, "language": st.lang, "voice_language": st.voice_lang}
        try:
            heard = transcribe(audio, mime, context)
        except Exception:  # the core could not hear it: say so, never guess
            heard = ""
        heard = (heard or "").strip()
        if heard:
            st.voice_lang = language.detect(heard, st.lang)
        return heard

    def hear(self, user_id: str, audio: bytes, mime: str = "audio/ogg") -> tuple[str | None, Any]:
        """(transcript, the core's reading of it or None). A core that can hear and understand in one step
        (core.interface.HearingCore) gets this conversation's context and vocabulary; any other core
        transcribes, and the transcript is read like typed text. (None, None): the core cannot hear."""
        st = self.state(user_id)
        hear = getattr(self.core, "hear", None)
        if callable(hear):
            context = self.context(user_id)
            context["source"] = "voice"
            try:
                heard, intent = hear(audio, mime, context)
            except Exception:  # never guess: say it was not heard
                heard, intent = "", None
            heard = (heard or "").strip()
            if heard:
                st.voice_lang = language.detect(heard, st.lang)
            return heard, intent if isinstance(intent, Intent) else None
        return self.transcribe(user_id, audio, mime), None

    def voice_failed(self, user_id: str, key: str) -> Reply:
        """The reply to a voice note that was not heard ('voice_unclear' | 'voice_unavailable')."""
        return self._voice_failed(user_id, key)

    def _voice_failed(self, uid: str, key: str) -> Reply:
        """Nothing was heard: say so and keep any open card as it is."""
        st = self.state(uid)
        line = self._t(uid, key)
        if st.card is not None:
            return Reply([line], card=self._open_card(uid), kind="card", state="QUOTED", quote_id=st.card.quote["id"],
                         lang=st.lang)
        return Reply([line], kind="failed", lang=st.lang)

    def reconcile(self, user_id: str) -> Reply:
        """Re-check payments whose outcome was unknown and report the ones now known."""
        reply = self._turn(user_id, lambda profile: Reply([], kind="info"))
        if not reply.lines and self.state(user_id).unknown:
            reply.kind = "unknown"
        return reply

    def _turn(self, uid: str, handle: Callable[[dict], Reply]) -> Reply:
        """Read the profile, report settled unknown payments, then run `handle`.

        The lines about earlier unknown payments are kept even when the wallet stops answering
        during the rest of the turn.
        """
        st = self.state(uid)
        st.trail = []
        try:
            profile = self.wallet.user(uid)
        except WalletUnavailable:
            return Reply([self._t(uid, "wallet_down")], kind="failed", lang=st.lang)
        st.gender = self._gender(profile)
        st.gender_known = (profile or {}).get("gender") in ("f", "m")
        st.demo = (profile or {}).get("source") == "demo"
        notes = self._reconcile(uid, profile)
        st.chatted = False
        try:
            reply = handle(profile)
        except WalletUnavailable:
            reply = Reply([self._t(uid, "wallet_down")], kind="failed")
        if not st.chatted:
            st.unclear_turns = 0
        reply.lines = notes + reply.lines
        reply.lang = st.lang
        self._finish(uid, reply)
        return reply

    # ------------------------------------------------------------ showing the work

    def _note(self, uid: str, what: str, source: str, req: Request | None = None, subject: str | None = None,
              more: bool = False, **slots: Any) -> None:
        """Record one check of this turn. With `req` it is a fact about that request, kept with it under `subject`
        (a later check of the same subject replaces it, unless `more`); without, it stands where it was made."""
        check = Check(what, "; ".join(f"{name}={_plain(value)}" for name, value in slots.items()), source, slots)
        st = self.state(uid)
        if req is None:
            st.trail.append(check)
            return
        key = subject or what
        req.facts[key] = req.facts.get(key, []) + [check] if more else [check]
        self._about(uid, req)

    def _about(self, uid: str, req: Request) -> None:
        """This turn is about `req`: its facts stand at this point of the trail (once)."""
        trail = self.state(uid).trail
        if not any(item is req for item in trail):
            trail.append(req)

    @staticmethod
    def _facts(req: Request) -> list[Check]:
        """What is known about a request now. A fact about a payee or an amount the user has since taken back is
        not one any more."""
        gone = {"payee": req.contact is None, "amount": req.amount is None, "bill": req.account is None}
        return [check for subject, checks in req.facts.items() if not gone.get(subject) for check in checks]

    def _finish(self, uid: str, reply: Reply) -> None:
        """Put the turn's checks on the reply, and offer «how did you check?» (and the menu, after a payment or a
        refusal) when the reply has no options of its own."""
        st = self.state(uid)
        evidence: list[Check] = []
        for item in st.trail:
            evidence += self._facts(item) if isinstance(item, Request) else [item]
        reply.evidence = evidence
        if evidence:
            st.explained = list(evidence)
        if reply.options or not evidence or reply.kind not in FOLLOW_UP_KINDS:
            return
        reply.options = [self._why_option(uid)]
        reply.options_role = "next"
        if reply.kind in MENU_AFTER_KINDS:
            reply.options += self._menu_options(uid)
            reply.options_title = self._t(uid, "menu_button")

    def _note_core(self, uid: str) -> None:
        """What the core says it checked on its last reading (core.interface.ExplainingCore), when it says."""
        evidence = getattr(self.core, "evidence", None)
        if not callable(evidence):
            return
        try:
            facts = evidence(uid)
        except Exception:  # a core that cannot explain itself never breaks a turn
            return
        if not isinstance(facts, dict):
            return
        st = self.state(uid)

        def action(name: Any) -> Any:
            key = f"why_act_{name}"
            return Text(key) if key in catalog(st.lang) else _safe_words(name, 30)

        verdict = str(facts.get("verdict") or "")
        if facts.get("decision") and verdict in ("agree", "ask", "direct", "abstain"):
            try:
                confidence = max(0, min(100, round(float(facts.get("confidence") or 0) * 100)))
            except (TypeError, ValueError):
                confidence = 0
            slots: dict[str, Any] = {"decision": action(facts["decision"]), "confidence": confidence}
            if verdict == "ask":
                slots["generative"] = action(facts.get("generative") or "chat")
            self._note(uid, f"core_{verdict}", "core", **slots)
        if facts.get("route"):
            self._note(uid, "core_route", "core", route=_safe_words(str(facts["route"]).replace("\n", " "), 80))
        if facts.get("fallback") is True:
            self._note(uid, "core_fallback", "core")

    # ------------------------------------------------------------ text turns

    def _on_text(self, uid: str, text: str, profile: dict) -> Reply:
        st, g = self.state(uid), self._gender(profile)
        if st.card is not None and (_looks_like_pin(text) or _worded_pin(text)):
            # A PIN typed (or said) in the chat while a card is open, tapped or not: never pass it on to the
            # core, keep the card as it is. (To change the amount there is «غيّر».)
            return Reply([self._t(uid, "pin_in_chat")], card=self._open_card(uid), kind="card", state="QUOTED",
                         quote_id=st.card.quote["id"])
        if _worded_pin(text):  # «الرمز 5678» with no card open: a PIN is never sent to the core
            return Reply([self._t(uid, "pin_in_chat")], kind="refuse")
        if wants_menu(text):  # «القائمة», "menu", «مساعدة»: answered here, with no model call
            return self._leave_for(uid, lambda: self._menu_reply(uid))
        if st.card is not None:
            if _is_yes(text):  # typed yes never pays: point at the button and keep the card (and the PIN step)
                reply = Reply([self._phrase(uid, "press_button")], card=self._open_card(uid), kind="card",
                              state="QUOTED", quote_id=st.card.quote["id"])
                return reply
            st.awaiting_pin = None
            open_card = st.card
            intent = self._understand(uid, text, profile)
            self._void_card(uid)
            if intent.action == "cancel":
                st.queue.clear()
                st.question = None
                return Reply([self._t(uid, "cancelled")], kind="cancelled", state="CANCELLED")
            intent = self._read_against_the_card(open_card.request, text, intent)
            if intent.action == "chat" and intent.payee_mention and not intent.amount_text \
                    and open_card.request.action == "transfer":
                # A new person for the open card: the amount was for somebody else, so it is asked again.
                open_card.request.amount, open_card.request.amount_text = None, None
            if intent.action == "chat" and (intent.amount_text or intent.payee_mention or intent.biller_mention
                                            or intent.operator or intent.currency):
                st.question = Question(open_card.request, "change")  # "خليها خمسين": change the card
            reply = self._dispatch(uid, text, intent, profile)
            reply.lines = [self._t(uid, "card_voided")] + reply.lines
            return reply
        st.awaiting_pin = None
        return self._dispatch(uid, text, self._understand(uid, text, profile), profile)

    @staticmethod
    def _read_against_the_card(old: Request, text: str, intent: Intent) -> Intent:
        """A message on an open card that names only an amount: if it says nothing else («لا خليها 45»), it changes the
        amount, same payee (the old card is already dead). If it says more («مو هادي 45», "send 45 to a friend", «مو 50»),
        nobody and nothing on the old card is kept: it is a new request, and the question comes from there.
        A core may read the same words as plain chat from one run to the next, so such a message is read here too."""
        if intent.action == "chat" and not (intent.amount_text or intent.extra or intent.payee_mention
                                            or intent.biller_mention or intent.operator or intent.period):
            arabic = arabizi.to_arabic(text)
            found = amounts.amount_phrases(arabic)
            if len(found) == 1 and not found[0][0].needs_question and found[0][0].currency != "USD" \
                    and not amounts.hedged_phrases(arabic) and _only_amount(text, found[0][1]):
                return Intent("chat", amount_text=found[0][1], language=intent.language)
            return intent
        if intent.action not in (old.action, "chat") or not intent.amount_text or intent.extra or intent.payee_mention \
                or intent.biller_mention or intent.operator or intent.period:
            return intent
        if _only_amount(text, intent.amount_text):
            return Intent("chat", amount_text=intent.amount_text, currency=intent.currency, language=intent.language)
        # A top-up for another line asks who if the old mention is not in these words; with none it would go to the user's own.
        return Intent(old.action, payee_mention=old.payee_mention if old.action == "topup" else None,
                      amount_text=intent.amount_text, currency=intent.currency, language=intent.language)

    def _understand(self, uid: str, text: str, profile: dict) -> Intent:
        st = self.state(uid)
        q = st.question
        context = self.context(uid, profile)
        if st.preheard is not None and st.preheard[0] == text and isinstance(st.preheard[1], Intent):
            intent, st.preheard = st.preheard[1], None  # the core already read this voice note
        else:
            intent = self.core.understand(text, context)
        self._note_core(uid)
        hint = language.normalize(getattr(intent, "language", None))
        if hint is not None:  # the core is sure which language this message is in
            st.lang = hint
        return intent

    def context(self, uid: str, profile: dict | None = None) -> dict[str, Any]:
        """What the core is told about this conversation (core.interface.Core): never a phone number or a PIN."""
        st = self.state(uid)
        q = st.question
        context: dict[str, Any] = {
            "user_id": uid,
            "gender": self._gender(profile) if profile is not None else st.gender,
            "asking": q.field if q else None,
            "pending_action": q.request.action if q else (st.card.request.action if st.card else None),
            "language": st.lang,
            "voice_language": st.voice_lang,
            "phone_tokens": sorted(st.phone_map),
            "history": list(st.history),
            "source": st.source,
        }
        try:
            context["vocabulary"] = self._vocabulary(uid)
        except (WalletError, WalletUnavailable):
            pass
        return context

    def _vocabulary(self, uid: str) -> dict[str, list[str]]:
        """The user's saved names and aliases, bill labels and biller names (no numbers)."""
        contacts, accounts = self.wallet.contacts(uid), self.wallet.bill_accounts(uid)
        people = []
        for c in contacts:
            people += [c["display_name"]] + ([c["alias"]] if c.get("alias") else [])
        people += list(self.state(uid).aliases)
        labels = [a["label_ar"] for a in accounts if a.get("label_ar")]
        billers = sorted({a["biller_name_ar"] for a in accounts if a.get("biller_name_ar")})
        return {"contacts": list(dict.fromkeys(people))[:60], "bill_labels": list(dict.fromkeys(labels))[:20],
                "billers": billers[:20]}

    def _account_stop(self, uid: str, profile: dict) -> Reply | None:
        """A real account that may not pay right now: frozen, closed, or without a usable PIN."""
        status, pin_state = profile.get("status") or "active", profile.get("pin_state") or "set"
        if status != "active" or pin_state != "set":
            self._note(uid, "account_state", "wallet:profile", status=str(status), pin_state=str(pin_state))
        if status != "active":
            return Reply([self._t(uid, "account_frozen")], kind="refuse")
        if pin_state != "set":
            return Reply([self._t(uid, "pin_blocked" if pin_state == "blocked" else "pin_not_set")], kind="refuse")
        return None

    def _manage_reply(self, uid: str, intent: Intent, text: str) -> Reply:
        st = self.state(uid)
        if self.manage is None:
            return self._with_menu(uid, Reply([self._phrase(uid, "help")], kind="ask"))
        out = self.manage.from_intent(uid, intent.action, text, st.phone_map, st.lang, st.gender)
        return Reply(out.lines, kind=out.kind)

    def _dispatch(self, uid: str, text: str, intent: Intent, profile: dict, picked: dict | None = None) -> Reply:
        """What to do with one reading of the user's message. `picked` is the option the user tapped in answer to
        the open question (then `text` is empty and `intent` is plain chat: nothing was typed, nothing was read)."""
        st, g = self.state(uid), self._gender(profile)
        q = st.question
        # A cue in this message or in one of the last few: the number may come in a later turn than the threat.
        cue = scam.looks_like_scam(text)
        if cue:
            st.scam_line = "authority_warning" if scam.fake_authority(text) else "scam_warning"
        # «بدون تأكيد» alone says nothing about later turns: it stops a number in its own request, and holds no longer.
        st.scam_turns_left = SCAM_CUE_TURNS + 1 if cue and scam.holds_for_later(text) else max(0, st.scam_turns_left - 1)
        if (cue or st.scam_turns_left) and self._has_unsaved_number(uid, text):
            return self._scam_stop(uid)
        if refund.asks_for_money_back(text):  # never a card: it would send more money
            st.question = None
            st.queue.clear()
            return Reply([self._t(uid, "no_reversal")], kind="refuse")
        if intent.action in ("history", "add_contact"):
            st.question = None
            if self.manage is None and intent.action == "history":
                return self._chat_reply(uid, text)
            return self._manage_reply(uid, intent, self._unmask(uid, text))
        if intent.action == "cancel":
            st.question = None
            st.queue.clear()
            return Reply([self._t(uid, "cancelled")], kind="cancelled")
        if intent.action == "balance":
            self._note(uid, "balance_read", "wallet:profile", balance=int(profile["balance"]))
            return Reply([self._t(uid, "balance", balance=profile["balance"])], kind="info")
        if intent.action == "unsupported":
            st.question = None
            st.queue.clear()
            self._note(uid, "out_of_scope", "core")
            warm = self._warm_line(uid, "refusal_warm")
            return Reply([self._t(uid, "unsupported"), *warm], kind="refuse", warm=bool(warm))
        if q is not None and picked is None and q.field == "payee" and intent.action == "chat" \
                and not intent.payee_mention and _not_a_name_answer(text):
            # «تعرف شي عن الطقس؟» or «مساء الخير» after «who?»: a new message, never a name to look up
            st.question = None
            return self._chat_reply(uid, text)
        if q is not None and intent.action in ("chat", q.request.action):
            return self._answer(uid, text, intent, profile, picked)
        if intent.action not in PAYMENT_ACTIONS:
            return self._chat_reply(uid, text)
        st.question = None
        stop = self._account_stop(uid, profile)
        if stop is not None:
            st.queue.clear()
            return stop
        wanted = [i for i in [intent, *intent.extra] if i.action in PAYMENT_ACTIONS]
        requests = [self._request(i, text) for i in wanted]
        for req in requests:  # «ادفع المولدة ودز لأحمد خمسين»: the fifty is not the generator's
            req.not_mine = [i.amount_text for i in wanted if i.amount_text and i.amount_text != req.amount_text]
        st.queue = requests[1:]
        return self._advance(uid, requests[0], profile)

    def _chat_reply(self, uid: str, text: str) -> Reply:
        """A message that asks for nothing to be done now. If it talks about a sum of money being paid (a what-if, a joke,
        money that came in, a past transfer) we say plainly that no payment was made; otherwise the greeting: a how-to
        question, a thanks or «حول» as «about» name no sum."""
        reading = arabizi.to_arabic(text or "")
        words = [fold(w) for w in reading.split()]
        if any(w.startswith(amounts.PAY_STEMS) for w in words) \
                and (amounts.amount_phrases(reading) or any(amounts.is_number_with_article(w) for w in words)):
            return Reply([self._t(uid, "no_payment_made")], kind="ask")
        st = self.state(uid)
        st.chatted = True
        st.unclear_turns += 1
        if self._can_talk():  # a conversing core answers what was said; the menu only when the user seems lost
            pretend = st.demo and asks_if_real(text)  # «الفلوس حقيقية؟» on a demo account
            written = self._talk(uid, "chat", text=st.last_text,
                                 reference=self._t(uid, "demo_is_pretend" if pretend else "help"))
            if written is not None:
                reply = Reply([written], kind="ask")
                return self._with_menu(uid, reply) if st.unclear_turns >= MENU_AFTER_UNCLEAR else reply
            # the writer was asked and gave nothing: a fixed line now, never a second model call, and no second
            # hello (the help text greets)
            if pretend:
                return Reply([self._t(uid, "demo_is_pretend")], kind="ask")
            return self._with_menu(uid, Reply([self._t(uid, "menu_intro")], kind="ask"))
        return self._with_menu(uid, Reply([self.core.phrase("help", {"gender": st.gender, "lang": st.lang})],
                                          kind="ask"))

    # ------------------------------------------------------------ the menu and tapped options

    def _menu_options(self, uid: str) -> list[Option]:
        return [Option(f"{OPTION_PREFIX}m_{row}", self._t(uid, f"menu_{row}"), self._t(uid, f"menu_{row}_hint"))
                for row in MENU_ROWS]

    def _why_option(self, uid: str) -> Option:
        return Option(EXPLAIN_OPTION, self._t(uid, "why_button"), self._t(uid, "why_hint"))

    def _with_menu(self, uid: str, reply: Reply) -> Reply:
        """The reply, with the main menu to tap under it."""
        reply.options, reply.options_role = self._menu_options(uid), "next"
        reply.options_title = self._t(uid, "menu_button")
        return reply

    def _menu_reply(self, uid: str) -> Reply:
        return self._with_menu(uid, Reply([self._t(uid, "menu_intro")], kind="ask"))

    def _leave_for(self, uid: str, make: Callable[[], Reply]) -> Reply:
        """The user turned to something else (the menu, one of its rows): an open card is voided, as any new
        request voids it, and the open question and the rest of a two-in-one are dropped."""
        st = self.state(uid)
        had_card = st.card is not None
        st.awaiting_pin = None
        self._void_card(uid)
        st.question = None
        st.queue.clear()
        reply = make()
        if had_card:
            reply.lines = [self._t(uid, "card_voided")] + reply.lines
        return reply

    def _on_option(self, uid: str, option_id: str, profile: dict) -> Reply:
        """A tap, looked up on this side. A menu row starts its flow in code. An answer to the open question goes
        through _dispatch like the typed answer does, with the option's meaning in place of words. Anything else
        (another question's id, the same question asked again since, a card button's id, noise) is an old list."""
        st = self.state(uid)
        menu = _MENU_OPTION.fullmatch(option_id)
        if menu and menu.group(1) in MENU_ROWS:
            row = menu.group(1)
            st.tapped = self._t(uid, f"menu_{row}")
            self._note(uid, "tapped", "code", title=st.tapped)
            return self._leave_for(uid, lambda: self._menu_action(uid, row, profile))
        asked, q = _QUESTION_OPTION.fullmatch(option_id), st.question
        if asked and q is not None and st.card is None and asked.group(1) == q.nonce \
                and int(asked.group(2)) < len(q.choices):
            index = int(asked.group(2))
            choice = q.choices[index]
            shown = q.shown.options if q.shown is not None else []
            st.tapped = shown[index].title if index < len(shown) else None
            if st.tapped:
                self._note(uid, "tapped", "code", title=st.tapped)
            st.awaiting_pin = None
            if choice.get("cancel"):  # «لا»: what a typed no is, once it is read
                return self._dispatch(uid, "", Intent("cancel"), profile)
            return self._dispatch(uid, "", Intent("chat"), profile, picked=choice)
        return self._stale_option(uid)

    def _stale_option(self, uid: str) -> Reply:
        """An option that means nothing now: say so, and show what is open (the card, or the question again)."""
        st = self.state(uid)
        line = self._t(uid, "option_stale")
        if st.card is not None:
            return Reply([line], card=self._open_card(uid), kind="card", state="QUOTED", quote_id=st.card.quote["id"])
        q = st.question
        if q is not None:
            again = q.shown or Reply([], kind="ask", ask_field=q.field)
            return Reply([line, *again.lines], kind="ask", ask_field=again.ask_field,
                         offered_amount=again.offered_amount, options=list(again.options),
                         options_title=again.options_title)
        reply = self._menu_reply(uid)
        reply.lines = [line] + reply.lines
        return reply

    def _menu_action(self, uid: str, row: str, profile: dict) -> Reply:
        """One row of the menu, started in code: no model reads anything."""
        st = self.state(uid)
        if row in MENU_PAYMENTS:
            return self._dispatch(uid, "", Intent(MENU_PAYMENTS[row]), profile)
        if row == "balance":
            return self._dispatch(uid, "", Intent("balance"), profile)
        if row == "help":
            return Reply([self._t(uid, "menu_help_text")], kind="info")
        if self.manage is not None:
            show = {"history": self.manage.history, "contacts": self.manage.list_contacts,
                    "account": self.manage.my_account}[row]
            out = show(uid, st.lang, st.gender)
            return Reply(list(out.lines), kind=out.kind)
        plain = {"history": self._plain_history, "contacts": self._plain_contacts, "account": self._plain_account}
        return plain[row](uid, profile)

    def _plain_history(self, uid: str, profile: dict) -> Reply:
        """The last few payments, from the wallet's own list (a wallet with no account service)."""
        lang = self.state(uid).lang
        rows = self._newest_first(self.wallet.transactions(uid))
        if not rows:
            return Reply([self._t(uid, "plain_history_empty")], kind="info")
        lines = [self._t(uid, "plain_history_title")]
        notes = {"reversed": "plain_status_reversed", "pending": "plain_status_pending",
                 "unknown": "plain_status_pending"}
        for t in rows[:PLAIN_HISTORY]:
            target = t.get("target") if isinstance(t.get("target"), dict) else {}
            kind = t.get("kind") if t.get("kind") in ("transfer", "bill", "topup") else "transfer"
            last4 = _digits(target.get("phone"))[-4:]
            name = (biller_name(target, lang) if kind == "bill" else target.get("display_name")) or f"…{last4}"
            note = notes.get(str(t.get("status") or "").lower())
            lines.append(self._t(uid, f"plain_history_{kind}", when=_baghdad_time(t.get("created_at"), with_date=True),
                                 amount=int(t.get("amount") or 0) + int(t.get("fee") or 0), name=str(name),
                                 last4=last4, status=self._t(uid, note) if note else ""))
        return Reply(lines, kind="info")

    @staticmethod
    def _newest_first(transactions: list[dict]) -> list[dict]:
        """The wallet's list by time, newest first. Its times are whole seconds and it lists two payments of one
        second in the order they were made, so the list is turned round first: the later of the two stays first."""
        return sorted(reversed(list(transactions or [])), key=lambda t: str(t.get("created_at") or ""), reverse=True)

    def _plain_contacts(self, uid: str, profile: dict) -> Reply:
        contacts = self.wallet.contacts(uid)
        if not contacts:
            return Reply([self._t(uid, "plain_contacts_empty")], kind="info")
        lines = [self._t(uid, "plain_contacts_title")]
        lines += [self._t(uid, "plain_contact_line", name=c["display_name"], last4=_digits(c.get("phone"))[-4:])
                  for c in contacts[:20]]
        return Reply(lines, kind="info")

    def _plain_account(self, uid: str, profile: dict) -> Reply:
        limit = int(profile.get("daily_limit") or 0)
        left = profile.get("daily_remaining")
        left = max(limit - int(profile.get("spent_today") or 0), 0) if left is None else int(left)
        return Reply([self._t(uid, "plain_account", name=str(profile.get("name") or ""),
                              last4=_digits(profile.get("phone"))[-4:], balance=int(profile["balance"]),
                              limit=limit, left=left)], kind="info")

    def _recent_payees(self, uid: str) -> list[dict]:
        """The saved contacts paid last, newest first: offered under «to whom?» (a tap, never a guess)."""
        try:
            contacts, past = self.wallet.contacts(uid), self.wallet.transactions(uid)
        except (WalletError, WalletUnavailable):
            return []
        by_id = {c["contact_id"]: c for c in contacts}
        by_number = {c.get("phone_e164") or phones.canonical(c.get("phone")): c for c in contacts}
        out: list[dict] = []
        for t in self._newest_first(past):
            target = t.get("target") if isinstance(t.get("target"), dict) else {}
            if t.get("kind") != "transfer" or TXN_STATUS.get(str(t.get("status") or "").lower()) != "SUCCEEDED":
                continue
            number = target.get("phone_e164") or phones.canonical(target.get("phone"))
            contact = by_id.get(target.get("contact_id")) or (by_number.get(number) if number else None)
            if contact is not None and all(contact is not seen for seen in out):
                out.append(contact)
            if len(out) == RECENT_PAYEES:
                break
        return out

    def _offer(self, uid: str, q: Question, key: str) -> list[Option]:
        """The answers to a question that can be tapped, and (kept here, in q.choices) what each one means.
        A free-text question has none, except «to whom?», which offers the people paid last."""
        lang = self.state(uid).lang
        words = LIST_WORDS[lang]
        options: list[Option] = []
        choices: list[dict] = []

        def add(title: str, choice: dict, description: str = "", tail: str = "") -> None:
            options.append(Option(f"{OPTION_PREFIX}q{q.nonce}_{len(choices)}", str(title), description, tail))
            choices.append(choice)

        def person(c: dict) -> None:
            last4 = _digits(c.get("phone"))[-4:]
            add(c["display_name"], {"contact": c}, words["ends"].format(last4=last4), last4)

        if q.field == "payee" and key == "ask_confirm_payee" and len(q.options) == 1:
            c = q.options[0]
            ends = words["ends"].format(last4=_digits(c.get("phone"))[-4:])
            add(self._t(uid, "option_yes"), {"contact": c}, f"{c['display_name']} · {ends}")
            add(self._t(uid, "option_no"), {"cancel": True})
        elif q.field == "payee" and key == "ask_which_payee":
            for c in q.options:
                person(c)
        elif q.field == "payee" and key == "ask_payee":
            for c in self._recent_payees(uid):
                person(c)
        elif q.field == "amount" and key == "ask_amount_choice":
            for o in q.options:
                add(f"{format_iqd(o['amount'])} {words['dinar']}", {"amount": o})
        elif q.field == "period":
            for o in q.options:
                add(_month_label(o["period"], lang), {"period": o["period"]})
        elif q.field == "biller":
            for a in q.options:
                number = str(a.get("account_no") or "")
                add(a.get("label_ar") or biller_name(a, lang), {"account": a},
                    " · ".join(part for part in (biller_name(a, lang), number) if part), number[-4:])
        elif q.field == "operator":
            names_of = OPERATOR_NAMES_EN if lang == language.ENGLISH else OPERATOR_NAMES
            for o in q.options:
                add(names_of.get(o["operator"], o["operator"]), {"operator": o["operator"]})
        elif q.field == "full_amount" and q.options:
            add(self._t(uid, "option_yes_full"), {"yes": True}, f"{format_iqd(q.options[0]['amount'])} {words['dinar']}")
            add(self._t(uid, "option_no"), {"cancel": True})
        if len(options) > MAX_OPTIONS:  # too many for any list: the numbered text and a typed answer
            options, choices = [], []
        q.choices = choices
        return options

    @staticmethod
    def _request(intent: Intent, text: str) -> Request:
        return Request(intent.action, text=text, payee_mention=intent.payee_mention,
                       biller_mention=intent.biller_mention, period=intent.period,
                       amount_text=intent.amount_text, currency=intent.currency, operator=intent.operator)

    # ------------------------------------------------------------ answers to our questions

    def _answer(self, uid: str, text: str, intent: Intent, profile: dict, picked: dict | None = None) -> Reply:
        """The user's answer to the open question: typed (`text`, read by the pickers below), or tapped (`picked`,
        what that option means). Both end in the same request and the same _advance."""
        st = self.state(uid)
        q = st.question
        req = q.request
        if picked is None:  # a tap adds no words to the request: it is read by what it stands for, never as text
            req.text = f"{req.text}\n{text}"
        st.question = None
        if q.field == "full_amount":
            return self._answer_full_amount(uid, q, text, intent, profile, picked)
        if picked is None and q.field == "amount":
            if intent.action == "chat" and intent.payee_mention:  # «لا لهادي 45»: the person answered too
                req.payee_mention, req.contact = intent.payee_mention, None
                req.amount, req.amount_text, req.currency = None, intent.amount_text, intent.currency
                return self._advance(uid, req, profile)
            if (req.action == "transfer" or req.payee_mention) and not intent.payee_mention and _ordinal(text) is None \
                    and self._leaves_the_payee(uid, req, text, intent.amount_text):
                # Nothing of this answer stays on the old person: who first, and the amount again after.
                req.payee_mention, req.contact = None, None
                req.amount, req.amount_text, req.currency = None, None, None
                return self._ask(uid, req, "payee", "ask_payee", profile)
        # A menu answer («2», «الثاني», a month) holds no amount of this request.
        if picked is None and (intent.action == req.action or q.field == "change"
                               or (q.field == "biller" and intent.amount_text and _ordinal(text) is None)):
            req.stated.append(text)
        if picked is None and intent.action == req.action:  # the user restated the request: take the new parts
            self._merge(req, intent)
            return self._advance(uid, req, profile)

        if q.field == "payee":
            contact = picked["contact"] if picked is not None else self._pick_contact(uid, text, q.options)
            if contact is not None:
                req.contact = contact
                self._note(uid, "payee_chosen", "contacts", req, "payee", name=str(contact.get("display_name") or ""),
                           last4=_digits(contact.get("phone"))[-4:])
                self._remember_alias(uid, req, profile)
            elif len(q.options) > 1 and _digits(text) and not intent.payee_mention:
                return self._ask_contacts(uid, req, q.options, profile)  # digits that fit none of them
            else:
                req.payee_mention, req.contact = intent.payee_mention or text.strip(), None
                if intent.payee_mention and intent.amount_text and req.amount_text and self._differ(intent.amount_text, req.amount_text):
                    req.amount, req.amount_text, req.currency = None, None, None  # «لزينب 45» after «دز 50 الف»: asked, never chosen
        elif q.field == "amount":
            chosen = picked["amount"] if picked is not None else None
            if chosen is None:
                req.amount, req.currency = None, intent.currency
                req.amount_text = intent.amount_text or text.strip()
                index = _ordinal(text) if q.options else None
                if index is not None and index < len(q.options):  # "الثاني" after «خمسين لو ستين؟»
                    chosen = q.options[index]
            if chosen is not None:
                req.amount_text, req.amount, req.currency = chosen["amount_text"], chosen["amount"], "IQD"
                self._note(uid, "amount_chosen", "user_text", req, "amount", amount=int(chosen["amount"]))
        elif q.field == "period":
            period = picked["period"] if picked is not None else self._pick_period(text, q.options)
            if period is None:
                return self._ask(uid, req, "period", "ask_period", profile, options=q.options)
            req.period, req.period_settled = period, True
        elif q.field == "biller":
            account = picked["account"] if picked is not None else self._pick_account(text, intent, q.options)
            if account is None:
                return self._ask(uid, req, "biller", "ask_biller", profile, options=q.options)
            req.account = account
        elif q.field == "operator":
            operator = picked["operator"] if picked is not None else (intent.operator or _operator_in(text))
            if operator is None:
                return self._ask(uid, req, "operator", "ask_operator", profile)
            req.operator = operator
        elif picked is None:  # "change": a new amount, a new name or a new biller for the same request
            if not self._merge(req, intent):
                if req.action == "pay_bill":
                    req.biller_mention, req.account = text.strip(), None
                else:
                    req.payee_mention, req.contact = text.strip(), None
        return self._advance(uid, req, profile)

    def _answer_full_amount(self, uid: str, q: Question, text: str, intent: Intent, profile: dict,
                            picked: dict | None = None) -> Reply:
        """The answer to «a bill is paid in full: pay all of it?». Only a yes (or the amount due itself) makes a card."""
        st, req = self.state(uid), q.request
        if picked.get("yes") if picked is not None else _is_full_yes(text):
            req.full_yes = q.options[0]["amount"]
            return self._advance(uid, req, profile)
        if intent.action == req.action and intent.biller_mention:  # another bill: a new request, nothing carried over
            return self._advance(uid, self._request(intent, text), profile)
        if intent.amount_text:
            self._merge(req, intent)
            req.stated.append(text)
            return self._advance(uid, req, profile)
        if set(_tokens(text)) & NEGATIONS:
            st.queue.clear()
            return Reply([self._t(uid, "cancelled")], kind="cancelled", state="CANCELLED")
        return self._advance(uid, req, profile)  # no answer: the same question again

    @staticmethod
    def _differ(one: str, other: str) -> bool:
        return amounts.parse(arabizi.to_arabic(one)).value_iqd != amounts.parse(arabizi.to_arabic(other)).value_iqd

    @staticmethod
    def _merge(req: Request, intent: Intent) -> bool:
        """Copy the parts the new intent names into the request. True if anything changed."""
        changed = False
        if intent.payee_mention:
            req.payee_mention, req.contact, changed = intent.payee_mention, None, True
        if intent.biller_mention:
            req.biller_mention, req.account, changed = intent.biller_mention, None, True
        if intent.amount_text or intent.currency == "USD":
            req.amount_text, req.currency, req.amount, changed = intent.amount_text, intent.currency, None, True
            req.full_yes = None
        if intent.operator:
            req.operator, changed = intent.operator, True
        if intent.period:
            req.period, req.account, changed = intent.period, req.account, True
        return changed

    def _leaves_the_payee(self, uid: str, req: Request, text: str, amount_text: str | None) -> bool:
        """An answer to «how much for him?» that turns away from him: «مو هادي 45», «غلطت بالاسم، 45», "sorry wrong person, 45",
        «لعمار 45» with no mention from the core. Talk around an amount («مليونين، بسرعه عيني») and «مو هواية» stay on the amount."""
        if _only_amount(text, amount_text):
            return False
        typed = _tokens(text)
        all_words = set(typed) | set(_tokens(arabizi.to_arabic(text)))
        if all_words & SOMEONE_ELSE_WORDS or (all_words & NOT_WORDS and all_words & THAT_ONE_WORDS):
            return True
        if set(typed) & NOT_WORDS and set(typed) <= NOT_WORDS | AMOUNT_ONLY_WORDS and not amount_text:
            return True  # «مو», «لا مو»: no amount, and something refused
        amount_words = {w for _, phrase in amounts.amount_phrases(arabizi.to_arabic(text)) for w in _tokens(phrase)}
        for i, word in enumerate(typed):  # somebody else named: «لعمار 45», "to omar 45"
            following = typed[i + 1:i + 2]
            if word.startswith("ل") and len(word) > 2 and word not in AMOUNT_ONLY_WORDS | amount_words:
                return True
            if word in ("to", "for") and following and following[0] not in AMOUNT_ONLY_WORDS | amount_words:
                return True
        contact_id = (req.contact or {}).get("contact_id")
        contacts = self.wallet.contacts(uid)
        if contact_id and self._ruled_out(uid, contact_id, text, text, contacts):
            return True
        return any(c.contact_id != contact_id for word in typed if not _digits(word) and word not in amount_words
                   for c in names.confident(self._candidates(uid, word, contacts)))  # «زينب 45», on هادي's question

    def _pick_contact(self, uid: str, text: str, options: list[dict]) -> dict | None:
        """Which contact the answer means: "الثاني", "إي", phone digits, or a name. «مو سعد النجار» picks nobody."""
        if set(_tokens(text)) & NOT_WORDS:
            return None
        if options and _is_yes(text) and len(options) == 1:
            return options[0]
        index = _ordinal(text)
        if index is not None and index < len(options):
            return options[index]
        # Phone digits pick only among the contacts we listed. A reply that is only a number is
        # resolved later through names.resolve, which matches phone endings too.
        for digits in re.findall(r"\d{4,}", names.normalize(text)):
            by_phone = [c for c in options if _digits(c["phone"]).endswith(digits)]
            if len(by_phone) == 1:
                return by_phone[0]
        if options:
            picked = names.pick(self._candidates(uid, text, options))
            if picked:
                return next(c for c in options if c["contact_id"] == picked.contact_id)
        return None

    def _pick_account(self, text: str, intent: Intent, options: list[dict]) -> dict | None:
        if options and _is_yes(text) and len(options) == 1:
            return options[0]
        index = _ordinal(text)
        if index is not None and index < len(options):
            return options[index]
        matches = self._match_accounts(intent.biller_mention or text, options)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _pick_period(text: str, options: list[dict]) -> str | None:
        """Which month the answer means: "الثاني", "تشرين الثاني", "شهر 11", or the one offered."""
        if options and _is_yes(text) and len(options) == 1:
            return options[0]["period"]
        index = _ordinal(text)
        if index is not None and index < len(options):
            return options[index]["period"]
        months = _named_months(text)
        if len(months) != 1:
            return None
        month = months.pop()
        offered = [o["period"] for o in options if int(o["period"][-2:]) == month]
        return offered[0] if offered else f"{month:02d}"

    def _unmask(self, uid: str, text: str) -> str:
        """The user's own words again: placeholders back to the numbers (for deterministic code only)."""
        for token, e164 in sorted(self.state(uid).phone_map.items(), key=lambda kv: -len(kv[0])):
            text = text.replace(token, phones.local(e164))
        return text

    def _remember_alias(self, uid: str, req: Request, profile: dict) -> None:
        """After "دز لأختي" + "زينب": remember that اختي means this contact (on the contact itself, for a
        real account, so it survives a restart)."""
        if req.relation_word and req.contact and req.contact.get("contact_id"):
            self.state(uid).aliases[req.relation_word] = req.contact["contact_id"]
            if self.accounts is not None:
                try:
                    self.accounts.set_alias(uid, req.contact["contact_id"], req.relation_word)
                except Exception:  # the alias is a convenience: the payment goes on without it
                    pass
            req.notes.append(self._t(uid, "alias_saved", name=req.contact["display_name"], word=req.relation_word))
            req.relation_word = None

    # ------------------------------------------------------------ building a payment

    def _advance(self, uid: str, req: Request, profile: dict) -> Reply:
        """Fill in what is missing, check the rules, and show a card (or ask / refuse)."""
        for subject in FRESH_FACTS:  # worked out again below, on fresh wallet numbers
            req.facts.pop(subject, None)
        self._about(uid, req)
        try:
            if req.action == "transfer":
                reply = self._transfer(uid, req, profile)
            elif req.action == "topup":
                reply = self._topup(uid, req, profile)
            else:
                reply = self._bill(uid, req, profile)
        except WalletError as exc:
            reply = self._quote_refused(uid, req, exc, profile)
        if req.notes:
            reply.lines = req.notes + reply.lines
            req.notes = []
        return reply

    def _has_unsaved_number(self, uid: str, text: str) -> bool:
        """The (masked) message holds a phone number that is none of the user's saved contacts."""
        if phones.foreign_numbers(text):
            return True
        numbers = [self.state(uid).phone_map.get(token) for token in phones.tokens_in(text)]
        if not numbers:
            return False
        saved = {c.get("phone_e164") or phones.canonical(c.get("phone")) for c in self.wallet.contacts(uid)}
        return any(number not in saved for number in numbers)

    def _scam_stop(self, uid: str) -> Reply:
        """A message that pushes money to an unsaved number in the name of support: no card, a warning."""
        st = self.state(uid)
        st.question = None
        st.queue.clear()
        self._note(uid, "scam", "rules")
        return Reply([self._t(uid, st.scam_line)], kind="refuse")

    def _is_scam_payee(self, uid: str, req: Request) -> bool:
        """The payee is a bare number, and the words of this request (all its turns) or of the last few turns
        carry a scam cue."""
        if not req.contact or req.contact.get("contact_id"):
            return False
        st = self.state(uid)
        if scam.looks_like_scam(req.text):
            st.scam_line = "authority_warning" if scam.fake_authority(req.text) else "scam_warning"
            return True
        return st.scam_turns_left > 0

    def _transfer(self, uid: str, req: Request, profile: dict) -> Reply:
        ask = self._payee(uid, req, profile)
        if ask:
            return ask
        if self._is_scam_payee(uid, req):
            return self._scam_stop(uid)
        ask = self._amount(uid, req, profile, "ask_amount_for", name=req.contact["display_name"])
        if ask:
            return ask
        cid = req.contact.get("contact_id")
        e164 = req.contact.get("phone_e164") or req.payee_phone
        stop, view, flags = self._check(uid, req, profile, "transfer",
                                        _transfer_key(cid, e164, req.contact.get("phone")))
        if stop:
            return stop
        target = {"contact_id": cid} if cid else {"phone": e164}
        quote = self.wallet.create_quote(uid, "transfer", target, req.amount)
        if _target(quote).get("hold"):  # a number with no account yet: say so on the card, in place of «new»
            flags = [rules.UNREGISTERED_RECIPIENT] + [f for f in flags if f != rules.FIRST_TIME_PAYEE]
        # A saved contact, so no stop: the scammer's number may have been saved earlier. The card says what this sounds like.
        warning = ("flag_scam_caution", {}) if scam.threat_or_verification(req.text) else None
        return self._show_card(uid, req, quote, view, flags, warning=warning)

    def _topup(self, uid: str, req: Request, profile: dict) -> Reply:
        if req.payee_mention:  # someone else's line: "دز لأحمد رصيد زين بخمسة"
            ask = self._payee(uid, req, profile)
            if ask:
                return ask
            if self._is_scam_payee(uid, req):
                return self._scam_stop(uid)
        phone = _digits(req.contact["phone"] if req.contact else profile["phone"])
        line_operator = _operator_of(phone)
        if req.operator is None:
            req.operator = line_operator
        if req.operator is None:
            return self._ask(uid, req, "operator", "ask_operator", profile)
        names_of = OPERATOR_NAMES_EN if self.state(uid).lang == "en" else OPERATOR_NAMES
        if line_operator and req.operator != line_operator:
            wrong = names_of.get(req.operator, req.operator)
            others = [{"operator": op} for op in OPERATOR_PREFIXES if op != req.operator]
            req.operator = None
            return self._ask(uid, req, "operator", "ask_operator_mismatch", profile, options=others, last4=phone[-4:],
                             operator=wrong)
        self._note(uid, "topup_line", "code", req, "topup", last4=phone[-4:],
                   operator=names_of.get(req.operator, req.operator))
        ask = self._amount(uid, req, profile, "ask_topup_amount")
        if ask:
            return ask
        stop, view, flags = self._check(uid, req, profile, "topup", phone)
        if stop:
            return stop
        quote = self.wallet.create_quote(uid, "topup", {"biller_id": req.operator, "phone": phone}, req.amount)
        return self._show_card(uid, req, quote, view, flags)

    def _bill(self, uid: str, req: Request, profile: dict) -> Reply:
        g = self._gender(profile)
        if OTHER_PERSON_WORDS & set(_tokens(req.text)):  # «فاتورة جاري»: never one of the user's own accounts
            return Reply([self._t(uid, "other_person_bill")], kind="refuse")
        ask = self._account(uid, req, profile)
        if ask:
            return ask
        account = req.account
        biller = biller_name(account, self.state(uid).lang)
        bills = self.wallet.bills(account["id"])
        if not req.period_settled:  # a month in the user's own words wins over a guess
            named = _named_months(req.text)
            if len(named) > 1:
                return self._ask_period(uid, req, bills, named, profile)
            if named:
                month = named.pop()
                if not (req.period and re.fullmatch(r"\d{4}-\d{2}", req.period) and int(req.period[5:]) == month):
                    req.period = f"{month:02d}"
        bill, period = _pick_bill(bills, req.period)
        if bill is not None and not req.period:  # no month said: an amount that is exactly another
            typed = self._typed_bill_amount(req)  # unpaid bill's amount means that bill
            if typed is not None and typed != bill["amount_due"]:
                same = [b for b in bills if b["status"] == "unpaid" and b["amount_due"] == typed]
                if len(same) == 1:
                    bill = same[0]
        if bill is None:
            self._note(uid, "bill_none", "wallet:bills", req, "refused", biller=biller)
            if period:
                return Reply([self._t(uid, "no_bill_for_period", biller=biller,
                                     month=_month_label(period, self.state(uid).lang))],
                             kind="refuse")
            return Reply([self._t(uid, "no_unpaid_bill", biller=biller)], kind="refuse")
        if bill["status"] == "paid":
            self._note(uid, "bill_paid", "wallet:bills", req, "refused", biller=biller, period=bill["period"])
            return Reply([self._t(uid, "already_paid", biller=biller, period=bill["period"])], kind="refuse")
        due = bill["amount_due"]
        self._note(uid, "bill", "wallet:bills", req, "bill", biller=biller, account=str(account.get("account_no") or ""),
                   period=bill["period"], due=int(due))
        words = self._bill_words(req, account)
        warning = self._bill_amount_warning(uid, req, bill, biller, words)
        other, typed = self._states_another_amount(req, words, due, warning)
        req.amount = due  # the biller sets the amount
        stop, view, flags = self._check(uid, req, profile, "bill", account["id"])
        if stop:
            return stop
        if other and req.full_yes != due:  # no card for an amount the user did not ask for: tell, and ask
            self._note(uid, "bill_full", "rules", req, "asked", due=int(due))
            return self._ask_full_amount(uid, req, biller, due, typed, profile)
        if other and warning is None:
            warning = ("flag_bill_amount_set", {"due": due})
        quote = self.wallet.create_quote(uid, "bill", {"bill_id": bill["id"]})
        return self._show_card(uid, req, quote, view, flags, warning=warning)

    def _typed_bill_amount(self, req: Request) -> int | None:
        """The exact amount the user typed with a bill request (in IQD), or None when unclear or absent."""
        if not req.amount_text or not said(req.amount_text, req.text) or req.currency == "USD":
            return None
        parsed = amounts.parse(arabizi.to_arabic(req.amount_text))
        whole = self._whole_amount(req, parsed)
        parsed = whole if whole is not None else parsed
        if parsed.value_iqd is None or parsed.needs_question or self._approximate(req) or self._alternatives(req):
            return None
        return parsed.value_iqd

    @staticmethod
    def _bill_words(req: Request, account: dict) -> list[str]:
        """The lines that state this bill request, read as Arabic, without the numbers that are no money: a real month
        («شهر ستة», never «هذا الشهر 20»), a subscription number, the account's own number when named as such, «5 امبير»,
        and the amounts of the other requests in the same message. Whatever is unsure stays: it only brings a question."""
        account_no = _digits(account.get("account_no"))

        def key(word: str) -> str:
            return re.sub(r"[^\w]|_", "", fold(word))

        out = []
        for line in req.stated:
            for other in req.not_mine:  # the last one is the other request's: «ادفع المولدة عشرين ودز لهادي عشرين»
                head, found, tail = line.rpartition(other)
                line = f"{head} {tail}" if found else line
            words, kept, i = arabizi.to_arabic(line).split(), [], 0
            while i < len(words):
                word, after = key(words[i]), i + 1
                if after < len(words) and word in amounts.NOT_AMOUNT_AFTER and key(words[after]) in BILL_NUMBER_NOUNS:
                    after += 1
                named = after > i + 1  # «رقم الاشتراك 350», «رقم الحساب 350»
                number = _digits(words[after]) if after < len(words) else ""
                if word in MONTH_WORDS:
                    this = i > 0 and key(words[i - 1]) in THIS_WORDS
                    scaled = after + 1 < len(words) and fold(words[after + 1]) in amounts.SCALES
                    month = after < len(words) and not this and not scaled \
                        and periods.months_named(f"شهر {words[after]}")
                    i = after + 1 if month else i + 1  # «شهر ستة» goes; the word «الشهر» alone never hides a number
                elif word in ACCOUNT_WORDS and number and not named and number != account_no:
                    i += 1  # «حساب 20» is an amount until we hear otherwise: the word goes, the number stays
                elif word in amounts.NOT_AMOUNT_AFTER and number:
                    i = after + 1  # «رقم الاشتراك 350», «حساب 350», «الساعة 8»
                elif _digits(words[i]) == account_no and len(account_no) >= MIN_ACCOUNT_DIGITS:
                    i += 1
                elif _digits(words[i]) and i + 1 < len(words) and key(words[i + 1]) in BILL_UNIT_WORDS:
                    i += 2  # «5 امبير»
                else:
                    kept.append(words[i])
                    i += 1
            out.append(" ".join(kept))
        return out

    @staticmethod
    def _states_another_amount(req: Request, words: list[str], due: int,
                               warning: tuple[str, dict] | None) -> tuple[bool, int | None]:
        """Do the user's own words state something other than the amount due? Whatever the core pointed at, so a part
        payment the core read as the plain bill is caught too. Returns (yes or no, the typed amount the core pointed at)."""
        if warning:
            return True, warning[1].get("typed")
        for line in reversed(words):  # the latest line with an amount speaks: «30», then «60» when 60 is due
            found = amounts.amount_phrases(line)
            if any(a.value_iqd != due or a.needs_question for a, _ in found):
                return True, None
            if found:
                if any(_refuses_right_after(phrase, stated) for _, phrase in found for stated in req.stated):
                    return True, None  # «مو 45 الف», and 45,000 is due
                break
        for line in req.stated:  # as typed too: an English word reads as something else in Arabic letters
            for tokens in (set(_tokens(line)), set(_tokens(arabizi.to_arabic(line)))):
                if tokens & PART_WORDS or (tokens & NOT_WORDS and tokens & WHOLE_WORDS):  # «مو كلها»
                    return True, None
        return False, None

    def _ask_full_amount(self, uid: str, req: Request, biller: str, due: int, typed: int | None, profile: dict) -> Reply:
        """A bill is paid in full, the wallet takes nothing else: say so and ask for a yes to that amount."""
        slots: dict[str, Any] = {"biller": biller, "due": due}
        if typed is not None:
            slots["typed"] = typed
        reply = self._ask(uid, req, "full_amount", "ask_bill_full" if typed is not None else "ask_bill_full_part", profile,
                          options=[{"amount": due}], **slots)
        reply.offered_amount = due
        self.state(uid).question.shown.offered_amount = due
        return reply

    def _bill_amount_warning(self, uid: str, req: Request, bill: dict, biller: str,
                             words: list[str] | None = None) -> tuple[str, dict] | None:
        """The warning (message key and slots) when the core points at an amount for a bill that is not the amount due.
        The biller's name is filled in when the card is rendered, in its language."""
        due = bill["amount_due"]
        if req.currency == "USD" and not req.amount_text:
            return "flag_bill_amount_set", {"due": due}
        if not req.amount_text or not said(req.amount_text, req.text):
            return None
        if words is not None and not said(req.amount_text, "\n".join(words)):
            return None  # it pointed at a month or at the account number
        parsed = amounts.parse(arabizi.to_arabic(req.amount_text))
        whole = self._whole_amount(req, parsed)
        parsed = whole if whole is not None else parsed
        if parsed.reason == amounts.NO_AMOUNT:
            return None
        if parsed.value_iqd is not None and not parsed.needs_question and not self._approximate(req) \
                and not self._alternatives(req):
            if parsed.value_iqd == due:
                return None
            return "flag_bill_amount_differs", {"due": due, "typed": parsed.value_iqd}
        return "flag_bill_amount_set", {"due": due}

    def _ask_period(self, uid: str, req: Request, bills: list[dict], months: set[int], profile: dict) -> Reply:
        """The user named more than one month (or «تشرين», which is two): ask which one."""
        options = []
        for month in sorted(months):
            same = sorted(b["period"] for b in bills if b["period"][5:7] == f"{month:02d}")
            options.append({"period": same[-1] if same else f"{month:02d}"})
        return self._ask(uid, req, "period", "ask_period", profile, options=options)

    def _payee(self, uid: str, req: Request, profile: dict) -> Reply | None:
        """Resolve the payee to exactly one saved contact, or return the question to ask."""
        if req.contact is not None:
            return None
        mention = req.payee_mention
        if mention and not said(mention, req.text):
            self._note(uid, "unsaid", "user_text", req, "asked", words=_safe_words(_display(mention)))
            mention = None  # the core pointed at words the user never wrote
        if not mention:
            return self._ask(uid, req, "payee", "ask_payee", profile)
        mention = req.payee_mention = _dative_off(mention)  # «لـ أحمد» is أحمد: the name is looked up without it
        token = phones.token_of(mention)
        if token is not None:
            if _refuses_right_after(mention, req.text.splitlines()[-1]):  # «مو على 0771...»
                req.payee_mention = None
                return self._ask(uid, req, "payee", "ask_payee", profile)
            return self._phone_payee(uid, req, token, profile)
        contacts = self.wallet.contacts(uid)
        if names.holds_verb_form(arabizi.to_arabic(mention)) or names.holds_verb_form(mention):
            # «دزلها» is the verb, never a person, and who «ها» is takes more than a word list can tell: we ask.
            req.payee_mention = None
            return self._ask(uid, req, "payee", "ask_payee", profile)
        candidates = self._candidates(uid, mention, contacts)
        picked = names.pick(candidates)
        how = picked.how if picked else ""
        if picked and self._ruled_out(uid, picked.contact_id, mention, req.text, contacts):
            return self._ask(uid, req, "payee", "ask_payee", profile)
        if not picked:
            picked = self._one_left_after_negation(uid, mention, req.text, contacts)
            how = "only_left" if picked else ""
        if not picked and self.state(uid).source == "voice":
            picked = self._heard_name(mention, contacts)
            how = "voice" if picked else ""
            if picked and self._ruled_out(uid, picked.contact_id, mention, req.text, contacts):
                return self._ask(uid, req, "payee", "ask_payee", profile)
        if picked:
            req.contact = next(c for c in contacts if c["contact_id"] == picked.contact_id)
            matched = f"why_how_{how}"
            self._note(uid, "payee", "contacts", req, "payee", words=_safe_words(_display(mention)),
                       name=str(req.contact["display_name"]), last4=_digits(req.contact.get("phone"))[-4:],
                       how=Text(matched if matched in catalog() else "why_how_other"))
            if req.relation_word and not self._relation_word(mention):
                self._remember_alias(uid, req, profile)
            return None
        by_id = {c["contact_id"]: c for c in contacts}
        sure = names.confident(candidates)
        if len(sure) > 1:
            self._note(uid, "payees_many", "contacts", req, "asked", words=_safe_words(_display(mention)),
                       count=len(sure))
            return self._ask_contacts(uid, req, [by_id[c.contact_id] for c in sure], profile)
        relation = self._relation_word(mention)
        parts = [by_id[c.contact_id] for c in candidates if c.how == "name_part"][:3]
        if relation and parts:  # «ابن عمي» with «رضا ابن عمي» saved: offer them, and keep no alias (it is not «عمي»)
            if len(parts) == 1:
                return self._ask(uid, req, "payee", "ask_confirm_payee", profile, options=parts,
                                 name=parts[0]["display_name"])
            return self._ask_contacts(uid, req, parts, profile)
        if relation:
            req.relation_word = relation  # the matched form, for the alias; the question shows the user's word
            english = self.state(uid).lang == language.ENGLISH
            return self._ask(uid, req, "payee", "ask_relation", profile,
                             word=self._relation_shown(uid, mention, relation) if english else relation)
        weak = [by_id[c.contact_id] for c in candidates if c.score >= 0.3][:3]
        if len(weak) == 1:
            return self._ask(uid, req, "payee", "ask_confirm_payee", profile, options=weak,
                             name=weak[0]["display_name"])
        if weak:
            return self._ask_contacts(uid, req, weak, profile)
        key = "unknown_payee_or_number" if self.accounts is not None else "unknown_payee"
        reply = self._ask(uid, req, "payee", key, profile, name=_display(mention))
        return reply

    def _ruled_out(self, uid: str, contact_id: str, mention: str, text: str, contacts: list[dict]) -> bool:
        """The latest line that names the payee also refuses him, in any way a name can be said: «مو لامي», «مو اخوي»,
        «مو لوسام», "not hadi", "mo hadi", «ومو لهادي». A line after it is a new statement: the user may take it back."""
        lines = text.splitlines()
        line = next((l for l in reversed(lines) if said(mention, l)), lines[-1] if lines else "")
        words = _tokens(line)
        for i, word in enumerate(words):
            if word not in NOT_WORDS:
                continue
            after = words[i + 1:i + 4]
            while after and after[0] in NOT_FILLERS:
                after = after[1:]
            if not after or _digits(after[0]):
                continue
            whole = names.confident(self._candidates(uid, " ".join(after[:2]), contacts)) if len(after) > 1 else []
            whole = [c for c in whole if c.score >= names.SCORE_EXACT]
            if contact_id in {c.contact_id for c in whole or names.confident(self._candidates(uid, after[0], contacts))}:
                return True
        return False

    def _one_left_after_negation(self, uid: str, mention: str, text: str, contacts: list[dict]) -> names.Candidate | None:
        """Two namesakes, and one plain form rules one out: a single line, in Arabic, that ENDS on «مو <his full saved name>»
        with no other negation, condition or question in it, and a mention that fits the other man. That man gets the
        card. Every other shape of negation asks: a question costs little, the wrong one of two people does not."""
        words = _tokens(text)
        if "\n" in text.strip() or arabizi.has_latin(text) or "?" in text or "؟" in text:
            return None
        out = []
        for c in contacts:
            saved = _tokens(c["display_name"])
            at = len(words) - len(saved) - 1
            if at > 0 and words[at] in SEND_NEGATIONS and words[at + 1:] == saved:
                out.append((c, words[:at]))
        if len(out) != 1 or set(out[0][1]) & NOT_PLAIN_BEFORE:
            return None
        ruled_out, before = out[0]
        said_words = _tokens(mention)
        sure = names.confident(names.resolve(said_words[0], contacts, self.state(uid).aliases))
        left = [c for c in sure if c.contact_id != ruled_out["contact_id"]]
        if len(left) != 1 or len(sure) != 2:
            return None
        saved = {_bare(w) for w in _tokens(left[0].display_name)}
        if any(_bare(w) not in saved | MENTION_FILLERS for w in said_words[1:]):
            return None
        # The word after the name in the message, if it is no amount, must fit him too: «لسعد الدورة» with a mention of «لسعد».
        first = _tokens(left[0].display_name)[0]
        for i, word in enumerate(before[:-1]):
            if word == first or word in {p + first for p in PREFIXES}:
                after = before[i + 1]
                if _bare(after) not in saved | MENTION_FILLERS and not amounts.amount_phrases(after):
                    return None
        return left[0]

    def _phone_payee(self, uid: str, req: Request, token: str, profile: dict) -> Reply | None:
        """A typed phone number is the payee: the saved contact with that number, else the number itself."""
        e164 = self.state(uid).phone_map.get(token)
        if e164 is None:  # a placeholder the user never sent
            req.payee_mention = None
            return self._ask(uid, req, "payee", "ask_payee", profile)
        req.payee_phone = e164
        if e164 == (profile.get("phone_e164") or ""):
            req.payee_mention, req.payee_phone = None, None
            return self._ask(uid, req, "payee", "transfer_to_self", profile)
        saved = [c for c in self.wallet.contacts(uid)
                 if (c.get("phone_e164") or phones.canonical(c.get("phone"))) == e164]
        req.contact = saved[0] if saved else {"contact_id": None, "display_name": phones.display(e164),
                                              "phone": phones.local(e164), "phone_e164": e164}
        if saved:
            self._note(uid, "payee_saved_number", "contacts", req, "payee", name=str(saved[0]["display_name"]),
                       last4=e164[-4:])
        else:
            self._note(uid, "payee_number", "user_text", req, "payee", last4=e164[-4:])
        return None

    @staticmethod
    def _heard_name(mention: str, contacts: list[dict]) -> names.Candidate | None:
        """A voice note: a name heard slightly wrong that is close to exactly one saved name is that person
        (the card shows who, and only a tap with the PIN pays)."""
        heard = names.normalize(_display(mention))
        # «ام» alone names nobody however close it is to «ام سجاد», and «ابوي» / «ابوك» are relatives, not «ابو علي»
        if heard in names.KIN_HEADS or names.is_relation_word(mention) or any(w in names.RELATIVES for w in heard.split()):
            return None

        def score(c: dict) -> float:
            saved = names.normalize(c["display_name"])
            first = saved.split(" ")[0]
            if first in names.KIN_HEADS:  # «ابو علي» is one name: being near «ابو» says nothing
                return names.similarity(heard, saved)
            return max(names.similarity(heard, saved), names.similarity(heard, first))

        ranked = sorted(contacts, key=score, reverse=True)
        close = [c for c in ranked if score(c) >= VOICE_NAME_MATCH]
        if len(close) != 1 or (len(ranked) > 1 and score(ranked[1]) >= 0.6):
            return None
        return names.Candidate(close[0]["contact_id"], close[0]["display_name"], close[0].get("phone", ""), 1.0, "voice")

    def _candidates(self, uid: str, mention: str, contacts: list[dict]) -> list[names.Candidate]:
        aliases = self.state(uid).aliases
        if not arabizi.has_latin(mention):
            return names.resolve(mention, contacts, aliases)
        best: dict[str, names.Candidate] = {}  # Arabizi: try every Arabic reading of the name, and the name as typed
        typed = " ".join(w for w in mention.split() if w.lower().strip(".,") not in ("l", "li", "la", "lil", "to"))
        # to_arabic keeps «el sha3la» as one word («الشعله»), the way a contact is saved; the variants split it.
        first = arabizi.to_arabic(mention)
        doubtful = arabizi.first_reading_is_a_guess(mention)  # «3ammar» is عمار: its first reading «عمر» is a guess
        sure = list(dict.fromkeys([typed or mention, *arabizi.sure_name_readings(mention), *([] if doubtful else [first])]))
        guesses = [r for r in [first, *arabizi.latin_name_variants(mention)] if r not in sure]

        def as_guess(cand: names.Candidate) -> names.Candidate:
            return names.Candidate(cand.contact_id, cand.display_name, cand.phone, names.FUZZY_MAX, "fuzzy")

        passed_over: dict[str, float] = {}  # a match taken away: how good it was
        for reading in sure + guesses:
            for cand in names.resolve(reading, contacts, aliases):
                if cand.how != "alias" and cand.score > names.FUZZY_MAX:
                    if arabizi.ends_differently(mention, cand.display_name) \
                            or names.title_not_saved(reading, arabizi.saved_as_read(mention, cand.display_name)):
                        if cand.score > passed_over.get(cand.contact_id, 0):
                            passed_over[cand.contact_id] = cand.score
                        cand = as_guess(cand)
                    elif reading in guesses:  # Other vowels than the ones typed («sara» -> سرى). Only a question may come of it.
                        cand = as_guess(cand)
                if cand.contact_id not in best or cand.score > best[cand.contact_id].score:
                    best[cand.contact_id] = cand
        aliased = [c for c in best.values() if c.how == "alias"]
        if aliased:  # As good as an exact name, no better: if a plain reading is exactly somebody else, the two are the question.
            ids = {c.contact_id for c in aliased}
            readings = [*arabizi.sure_name_readings(mention, shape_counts=False), *([] if arabizi.known_name_spelling(mention) else [first])]
            same = [c for reading in readings for c in names.resolve(reading, contacts, aliases) if c.score >= names.SCORE_EXACT
                    and c.contact_id not in ids and not arabizi.ends_differently(mention, c.display_name)]
            if same:
                best = {c.contact_id: as_guess(c) for c in [*aliased, *same]}
        else:
            # Who else this Latin name could be. It can only take a match away: «husen» reads حسن first, and if حسين is
            # saved too, the two are the question. A known spelling («hasan») has one reading, so it has no such rival.
            fits = names.spelled_by(arabizi.name_word_readings(mention), contacts)
            plain = {c.contact_id for c in names.spelled_by(arabizi.sure_word_readings(mention), contacts)}
            matched = [c for c in best.values() if c.score >= names.THRESHOLD]
            sure_ones = plain | {c.contact_id for c in matched}
            rivals = [c for c in fits if c.contact_id not in plain]
            if doubtful and not arabizi.known_name_spelling(mention):  # «3ammar 3ali»: its first reading is still somebody
                rivals += [c for c in names.confident(names.resolve(first, contacts, aliases)) if c.contact_id not in sure_ones]
            # Taking a match from one person must not leave the next one alone with it: «ali saeed», علي سعيد and السيد علي حسن.
            # A lesser match is no rival: «sayed ali» is سيد علي over علي حسن whatever becomes of the second.
            level = max((c.score for c in matched), default=1.0)
            rivals += [best[i] for i, score in passed_over.items() if score >= level and best[i].score < names.THRESHOLD]
            if (matched and rivals) or (not matched and len(fits) > 1):
                best = {c.contact_id: as_guess(c) for c in [*matched, *fits, *rivals]}
        top = sorted(best.values(), key=lambda c: (-c.score, c.display_name, c.contact_id))
        if top and top[0].score >= names.THRESHOLD:  # keep only the best kind of match
            top = [c for c in top if c.score == top[0].score]
        return top

    def _shown(self, uid: str, mention: str) -> str:
        """A mention as a reply shows it: the user's own words in an English reply («to my brother» ->
        «brother», «the electricity» -> «electricity»), else read as Arabic («kahraba» -> «كهرباء»).
        The Arabic reading is only for matching; an English reply never shows Arabic the user never wrote."""
        english = self.state(uid).lang == language.ENGLISH and arabizi.has_latin(mention)
        source = re.sub(r"[^\w\s]", " ", mention if english else arabizi.to_arabic(mention))
        words = source.split()
        if english:
            words = [w for w in words if w.lower() not in EN_MENTION_FILLERS] or words
        return " ".join(words) or mention

    def _relation_shown(self, uid: str, mention: str, relation: str) -> str:
        """The relation word for an English question: as the user typed it, or its English name."""
        if arabizi.has_latin(mention):
            return self._shown(uid, mention)
        return RELATION_EN.get(relation, "that person")

    @staticmethod
    def _relation_word(mention: str) -> str | None:
        readings = arabizi.latin_name_variants(mention) if arabizi.has_latin(mention) else [mention]
        for reading in readings:
            for word in names.normalize(reading).split():
                for form in (word, word[1:] if word.startswith("ل") else word):
                    if form in names.RELATION_WORDS:
                        return form
        return None

    def _account(self, uid: str, req: Request, profile: dict) -> Reply | None:
        """Resolve the bill account, or return the question (or refusal) to send."""
        if req.account is not None:
            return None
        accounts = [a for a in self.wallet.bill_accounts(uid) if a.get("biller_kind") != "topup"]
        if accounts and req.biller_mention and _refuses_right_after(req.biller_mention, req.text.splitlines()[-1]):
            req.biller_mention = None  # «مو المولدة», even when it is the only bill saved
            return self._ask(uid, req, "biller", "ask_biller", profile, options=accounts)
        if req.biller_mention and _names_no_biller(req.biller_mention):
            req.biller_mention = None
        if not req.biller_mention:
            if not accounts:  # nothing saved: never ask «which bill?» with no options
                return Reply([self._t(uid, "no_bill_account", biller=BILLS_WORD.get(self.state(uid).lang, "فواتير"))],
                             kind="refuse")
            if len(accounts) == 1:
                req.account = accounts[0]
                return None
            return self._ask(uid, req, "biller", "ask_biller", profile, options=accounts)
        matches = self._match_accounts(req.biller_mention, accounts)
        if len(matches) == 1 and len(_bill_kinds(req.biller_mention)) < 2:  # two kinds named: never pick for the user
            req.account = matches[0]
            return None
        if not matches:
            biller = self._shown(uid, req.biller_mention)
            if self.state(uid).lang == language.ENGLISH and not arabizi.has_latin(biller):
                words = {_bare(w) for w in _tokens(biller)}  # an Arabic mention in an English reply: its kind
                kinds = [kind for kind, forms in BILL_KIND_WORDS.items() if words & forms]
                biller = kinds[0] if len(kinds) == 1 else "bill"
            return Reply([self._t(uid, "no_bill_account", biller=biller)], kind="refuse")
        return self._ask(uid, req, "biller", "ask_biller", profile, options=matches)

    @staticmethod
    def _match_accounts(mention: str, accounts: list[dict]) -> list[dict]:
        """Accounts that fit a biller mention: by kind word, then by biller name or the user's label."""
        words = [_bare(w) for w in _tokens(arabizi.to_arabic(mention))]
        kinds = _bill_kinds(mention)
        pool = [a for a in accounts if a.get("biller_kind") in kinds] if kinds else list(accounts)

        def overlap(account: dict) -> int:
            about = f"{account.get('biller_name_ar', '')} {account.get('label_ar', '')}"
            known = {_bare(w) for w in _tokens(about)}
            return sum(1 for w in words if w not in ALL_KIND_WORDS and len(w) > 1 and w in known)

        best = max((overlap(a) for a in pool), default=0)
        if best > 0:
            return [a for a in pool if overlap(a) == best]
        return pool if kinds else []

    def _amount(self, uid: str, req: Request, profile: dict, ask_key: str, **slots: Any) -> Reply | None:
        """Parse the amount from the user's own words, or return the question to ask."""
        if req.amount is not None:
            return None
        if req.amount_text and not said(req.amount_text, req.text):
            self._note(uid, "unsaid", "user_text", req, "asked", more=True, words=_safe_words(req.amount_text))
            req.amount_text = None
        if req.currency == "USD":
            return self._ask(uid, req, "amount", "ask_usd", profile)
        if req.amount_text:
            req.amount_text = self._without_saved_name(req, req.amount_text) or None
        if not req.amount_text:
            return self._ask(uid, req, "amount", ask_key, profile, **slots)
        parsed = amounts.parse(arabizi.to_arabic(req.amount_text))
        whole = self._whole_amount(req, parsed)
        if whole is not None:  # the core pointed at part of the amount ("مية" of "مية وخمسين")
            if whole.currency == "USD":
                req.currency = "USD"
                return self._ask(uid, req, "amount", "ask_usd", profile)
            return self._ask(uid, req, "amount", "ask_amount_unclear", profile)
        choice = self._alternatives(req)
        if choice:  # «خمسين او ستين»: never pick one of them
            if all(a.value_iqd is not None and not a.needs_question for a, _ in choice):
                options = [{"amount": a.value_iqd, "amount_text": words} for a, words in choice]
                words = LIST_WORDS[self.state(uid).lang]
                listed = words["or"].join(f"{i}) {format_iqd(o['amount'])} {words['dinar']}"
                                          for i, o in enumerate(options, 1))
                return self._ask(uid, req, "amount", "ask_amount_choice", profile, options=options,
                                 options_text=listed)
            return self._ask(uid, req, "amount", "ask_amount_multiple", profile)
        if not parsed.needs_question and self._approximate(req):
            parsed = amounts.Amount(None, None, True, amounts.APPROXIMATE)
        if parsed.currency == "USD":
            req.currency = "USD"
            return self._ask(uid, req, "amount", "ask_usd", profile)
        if parsed.needs_question:
            key = {amounts.APPROXIMATE: "ask_amount_exact", amounts.MULTIPLE: "ask_amount_multiple",
                   amounts.NO_AMOUNT: ask_key}.get(parsed.reason, "ask_amount_unclear")
            return self._ask(uid, req, "amount", key, profile, **(slots if key == ask_key else {}))
        if amounts.bare_scale_after_unknown(arabizi.to_arabic(self._amount_line(req))):
            return self._ask(uid, req, "amount", "ask_amount_unclear", profile)
        if req.action == "transfer" and self._amount_is_background(req):
            return self._ask(uid, req, "amount", "ask_amount_multiple", profile)
        if req.action == "transfer" and self._amount_is_a_share(req):
            return self._ask(uid, req, "amount", "ask_amount_unclear", profile)
        if _refuses_right_after(req.amount_text, next((l for l in reversed(req.text.splitlines()) if said(req.amount_text, l)), "")):
            return self._ask(uid, req, "amount", "ask_amount_unclear", profile)  # «مو 60»: the one amount the user refused
        req.amount = parsed.value_iqd
        self._note(uid, "amount", "user_text", req, "amount", words=_safe_words(req.amount_text),
                   amount=int(req.amount), rule=Text(self._amount_rule(req.amount_text, req.amount)))
        return None

    @staticmethod
    def _amount_rule(words: str, value: int) -> str:
        """Which reading rule gave `value` for the user's amount words (the message key of its name): a number
        with its «ألف» or «مليون», a number under 1,000 with none (it means thousands), or the number as written.
        Eastern digits are said too."""
        reading = arabizi.to_arabic(words or "")
        eastern = "eastern_" if set(words or "") & EASTERN_DIGITS else ""
        tokens = {fold(w) for w in re.sub(r"[^\w\s]|_", " ", reading).split()}
        scales = {*amounts.SCALES, *amounts.PLURAL_SCALES, *amounts.DUAL_SCALES, *amounts.CONSTRUCT_SCALES}
        if tokens & scales:
            rule = "scale"
        elif amounts.parse(f"{reading} الف").value_iqd == value:
            rule = "bare"
        else:
            rule = "exact"
        return f"why_rule_{eastern}{rule}"

    @staticmethod
    def _without_saved_name(req: Request, text: str) -> str:
        """`text` without the words of the saved payee's name that read as a number: for «حيدر السبع», the
        «السبع» of «دز لحيدر السبع مليون» is his name, so the amount is the million."""
        if not req.contact or not req.contact.get("contact_id"):
            return text
        in_name = {w for w in names.normalize(req.contact.get("display_name") or "").split()[1:]
                   if amounts.is_number_with_article(w)}
        if not in_name:
            return text
        return " ".join(w for w in text.split() if names.normalize(w) not in in_name)

    @classmethod
    def _amount_line(cls, req: Request) -> str:
        """The latest line of the user's words that contains the core's amount_text, read as Arabic, without the words
        that name the payee: they say nothing about the amount («دز لعلي الحلاق مليون» is a million)."""
        line = next((line for line in reversed(req.text.splitlines()) if said(req.amount_text, line)), "")
        return cls._without_payee(req, line)

    @classmethod
    def _without_payee(cls, req: Request, line: str) -> str:
        """Drops the first word of the mention (it may be a nickname or an alias) and any word of the saved name.
        A word of the amount stays: «دز لست نادية ست تلاف» keeps the second «ست»."""
        line = cls._without_saved_name(req, line)
        mention = _tokens(arabizi.to_arabic(req.payee_mention or ""))[:1]
        saved = set(_tokens((req.contact or {}).get("display_name") or ""))
        amount = set(_tokens(arabizi.to_arabic(req.amount_text or "")))
        out = []
        for word in arabizi.to_arabic(line).split():
            bare = names.normalize(word)
            forms = {bare, bare[1:] if bare.startswith("ل") else bare, "ال" + bare[2:] if bare.startswith("لل") else bare}
            if bare not in amount and (forms & saved or forms & set(mention)):
                continue
            out.append(word)
        return " ".join(out)

    def _approximate(self, req: Request) -> bool:
        """True when the user's own message hedges the amount ("حوالي خمسين", "ميتين بحدود"),
        whatever the core passed on."""
        line = arabizi.to_arabic(self._amount_line(req))
        found = amounts.find_amount(line)
        if found and found[0].reason == amounts.APPROXIMATE:
            return True
        fragment = arabizi.to_arabic(req.amount_text or "")
        return any(said(fragment, words) or said(words, fragment) for words in amounts.hedged_phrases(line))

    def _alternatives(self, req: Request) -> list[tuple[amounts.Amount, str]]:
        """The two amounts the user offered as a choice ("خمسين او ستين") when the core's amount is one of
        them; else []."""
        if req.amount is not None:
            return []
        line = arabizi.to_arabic(self._amount_line(req))
        choice = amounts.alternatives(line)
        fragment = arabizi.to_arabic(req.amount_text or "")
        if any(said(fragment, words) or said(words, fragment) for _, words in choice):
            return choice
        return []

    @staticmethod
    def _after_the_verb(line: str) -> str:
        """The line from its first pay verb on, or all of it when it has none."""
        verb = next((m.start() for m in re.finditer(r"\S+", line) if names.is_verb_form(names.normalize(m.group()))), None)
        return line if verb is None else line[verb:]

    @classmethod
    def _amount_is_background(cls, req: Request) -> bool:
        """The amount to send is the first one after the pay verb (the other request's aside). The core pointed at another
        one in that line: the story («الفحص كلف 70 الف، دزلها مية», «دز لزينب مية، الفحص كلف 70 الف»), and we do not choose."""
        fragment = arabizi.to_arabic(req.amount_text or "")
        found = [words for _, words in amounts.amount_phrases(cls._after_the_verb(cls._amount_line(req)))
                 if not any(said(other, words) or said(words, other) for other in req.not_mine)]
        return bool(found) and not (said(fragment, found[0]) or said(found[0], fragment))

    @classmethod
    def _amount_is_a_share(cls, req: Request) -> bool:
        """«زينب تريد 70 الف، دزلها نصها», «دز الباقي»: what is sent is a share of the amount the core pointed at. An amount
        standing between the verb and the word is the amount sent: «دزله 150 الف والباقي بعدين»."""
        line = next((l for l in reversed(req.text.splitlines()) if said(req.amount_text, l)), "")
        for source in (line, arabizi.to_arabic(line)):
            words = source.split()
            verb = next((i for i, w in enumerate(words) if names.is_verb_form(names.normalize(w))), 0)
            for at, word in enumerate(words):
                if set(_tokens(word)) & SHARE_WORDS:
                    sent = words[verb:at] if at > verb else words[verb:]
                    if not amounts.amount_phrases(arabizi.to_arabic(" ".join(sent))):
                        return True
        return False

    def _whole_amount(self, req: Request, parsed: amounts.Amount) -> amounts.Amount | None:
        """The user's whole amount phrase when the core's amount_text is only part of it, else None.

        "دز لهادي مية وخمسين" with amount_text "مية" -> the phrase is worth 150,000, not 100,000.
        """
        fragment = arabizi.to_arabic(req.amount_text)
        phrases = amounts.amount_phrases(arabizi.to_arabic(self._amount_line(req)))
        around = [amount for amount, words in phrases if said(fragment, words) or said(req.amount_text, words)]
        if not around or any((a.value_iqd, a.currency) == (parsed.value_iqd, parsed.currency) for a in around):
            return None
        return around[0]

    def _check(self, uid: str, req: Request, profile: dict, kind: str,
               payee_id: str) -> tuple[Reply | None, rules.WalletView, list[str]]:
        """payments.rules on fresh wallet numbers. Returns (refusal or None, view, soft flags)."""
        g = self._gender(profile)
        now = self.clock()
        history, in_flight_today = [], 0
        day_start = now.astimezone(BAGHDAD).replace(hour=0, minute=0, second=0, microsecond=0)
        for t in self.wallet.transactions(uid):
            created = _parse_time(t["created_at"])
            status = TXN_STATUS.get(t.get("status", ""), str(t.get("status", "")).upper())
            history.append(rules.PastPayment(_payee_key(t["kind"], t.get("target")), t["amount"],
                                             (now - created).total_seconds() / 60, status))
            if status in ("EXECUTING", "UNKNOWN") and created >= day_start:
                in_flight_today += t["amount"]
        view = rules.WalletView(
            balance=profile["balance"],
            daily_limit=profile.get("daily_limit") or NO_LIMIT,
            spent_today=(profile.get("spent_today") or 0) + in_flight_today,
            gender=g,
            history=history,
            cooldown_left=self._cooldown_left(profile, history, now),
            single_max=self._single_max(uid),
        )
        checks = rules.check(rules.PaymentRequest(kind, req.amount, payee_id), view)
        fee = rules.FEES.get(kind, 0)
        self._note_rules(uid, req, kind, view, checks, fee)
        if not checks.hard_stops:
            return None, view, checks.soft_flags
        code = checks.hard_stops[0]
        if code == rules.OVER_SINGLE_MAX:  # the cap of this account's verification level, with its real number
            return Reply([kyc_texts.render("kyc_single_max", g, self.state(uid).lang, single_max=view.single_max)],
                         kind="refuse"), view, []
        if code == rules.INSUFFICIENT_BALANCE and not fee:
            return Reply([self._t(uid, "insufficient_balance_no_fee", balance=view.balance, needed=req.amount or 0)],
                         kind="refuse"), view, []
        slots = {
            rules.INSUFFICIENT_BALANCE: {"balance": view.balance, "needed": (req.amount or 0) + fee},
            rules.OVER_DAILY_LIMIT: {"limit": view.daily_limit, "remaining": view.remaining_today},
            rules.PIN_RESET_COOLDOWN: {"limit": PIN_RESET_COOLDOWN_IQD, "remaining": view.cooldown_left or 0},
        }.get(code, {})
        return Reply([self._t(uid, code, **slots)], kind="refuse"), view, []

    def _note_rules(self, uid: str, req: Request, kind: str, view: rules.WalletView, checks: rules.Checks,
                    fee: int) -> None:
        """Each hard rule with the numbers it was checked on, passed or not."""
        amount, stops = req.amount, checks.hard_stops
        if not isinstance(amount, int) or amount <= 0 or rules.NON_IQD in stops:
            self._note(uid, "amount_invalid", "rules", req, "rules")
            return
        self._note(uid, "balance_short" if rules.INSUFFICIENT_BALANCE in stops else "balance_ok", "wallet:profile",
                   req, "rules", balance=int(view.balance), needed=amount + fee)
        if view.single_max is not None:
            self._note(uid, "single_over" if rules.OVER_SINGLE_MAX in stops else "single_ok", "rules",
                       req, "rules", more=True, limit=int(view.single_max), amount=amount)
        if view.daily_limit < NO_LIMIT:
            self._note(uid, "limit_over" if rules.OVER_DAILY_LIMIT in stops else "limit_ok", "wallet:profile",
                       req, "rules", more=True, limit=int(view.daily_limit), spent=int(view.spent_today), amount=amount)
        if kind != "bill" and view.cooldown_left is not None:
            self._note(uid, "cooldown_over" if rules.PIN_RESET_COOLDOWN in stops else "cooldown_ok", "wallet:profile",
                       req, "rules", more=True, limit=PIN_RESET_COOLDOWN_IQD, remaining=int(view.cooldown_left),
                       amount=amount)

    def _single_max(self, uid: str) -> int | None:
        """The most one payment may be at this account's verification level (kyc.policy, read through the account
        service). None for an account that has no level, and for a wallet with no account service."""
        limits = getattr(self.accounts, "kyc_limits", None)
        found = limits(uid) if callable(limits) else None
        return int(found["single_max"]) if found else None

    @staticmethod
    def _cooldown_left(profile: dict, history: list[rules.PastPayment], now: datetime) -> int | None:
        """The 24 hours after an admin PIN reset: how much more may go out (None: no reset, no cap)."""
        reset = profile.get("pin_reset_at")
        if not reset:
            return None
        since = (now - _parse_time(reset)).total_seconds() / 60
        if since >= 24 * 60:
            return None
        spent = sum(p.amount for p in history if p.minutes_ago <= since and p.status in ("SUCCEEDED", "EXECUTING",
                                                                                          "UNKNOWN"))
        return max(0, PIN_RESET_COOLDOWN_IQD - spent)

    def _show_card(self, uid: str, req: Request, quote: dict, view: rules.WalletView, flags: list[str],
                   warning: tuple[str, dict] | None = None) -> Reply:
        open_card = OpenCard(quote, req, None, view=view, flags=list(flags) if flags is not None else None,
                             warning=warning)
        open_card.card = self._render_open(uid, open_card, self.state(uid).lang)
        self.state(uid).card = open_card
        for index, code in enumerate(flags or []):  # the warnings the card carries, as the rules gave them
            known = f"why_flag_{code}" in catalog()
            self._note(uid, f"flag_{code}" if known else "flag_other", "rules", req, "flags", more=index > 0,
                       **({} if known else {"flag": str(code)}))
        if not flags:
            self._note(uid, "flags_none", "rules", req, "flags")
        amount, fee = int(quote.get("amount") or 0), int(quote.get("fee") or 0)
        self._note(uid, "quote", "wallet:quote", req, "quote", reference=str(quote["id"]), amount=amount, fee=fee,
                   total=int(quote.get("total") or amount + fee), time=_baghdad_time(quote.get("expires_at")))
        return Reply([], card=open_card.card, kind="card", state=quote.get("state", "QUOTED"), quote_id=quote["id"])

    def _render_open(self, uid: str, open_card: OpenCard, lang: str) -> Card:
        """The open card in `lang`: the same quote, amounts and warnings, in the words of that language."""
        card = render_card(_details(open_card.quote, lang), open_card.view, open_card.flags, lang=lang)
        if open_card.warning is not None:  # never a silent mismatch: one line on the card itself
            key, slots = open_card.warning
            if key == "flag_scam_caution":
                line, code = render(key, self.state(uid).gender, lang), "scam_caution"
            else:
                line = render(key, self.state(uid).gender, lang, biller=biller_name(open_card.request.account, lang),
                              **slots)
                code = "bill_amount_differs"
            card.flags.insert(0, line)
            card.flag_codes.insert(0, code)
            card.spoken = spoken_summary(_details(open_card.quote, lang), self.state(uid).gender, lang,
                                         has_flags=True, flag=line)
        return card

    def _open_card(self, uid: str) -> Card:
        """The open card, shown again: in the conversation's language now, if the user switched since
        (same quote id and buttons), so the text, the card and its voice summary are one language."""
        st = self.state(uid)
        if getattr(st.card.card, "lang", st.lang) != st.lang:
            st.card.card = self._render_open(uid, st.card, st.lang)
        return st.card.card

    def _quote_refused(self, uid: str, req: Request, exc: WalletError, profile: dict) -> Reply:
        """The wallet would not quote. Say so honestly; nothing was charged."""
        g = self._gender(profile)
        self._note(uid, "refused_wallet", "wallet:quote", req, "refused", reason=str(exc.code))
        if exc.code == "already_paid":
            account = req.account or {}
            period = exc.detail.get("period") or ""
            return Reply([self._t(uid, "already_paid", biller=biller_name(account, self.state(uid).lang), period=period)],
                         kind="refuse")
        if exc.code in ("unknown_contact",):
            req.contact = None
            return self._ask(uid, req, "payee", "unknown_payee", profile, name=_display(req.payee_mention or ""))
        if exc.code == "non_positive_amount":
            return Reply([self._t(uid, "non_positive_amount")], kind="refuse")
        if exc.code in ("transfer_to_self", "invalid_phone"):
            req.contact, req.payee_mention, req.payee_phone = None, None, None
            return self._ask(uid, req, "payee", exc.code, profile)
        if exc.code in ("recipient_closed", "hold_limit", "hold_cap", "pin_blocked", "pin_not_set"):
            slot = REFUSAL_SLOTS.get(exc.code)
            slots = {slot: int(exc.detail.get(slot) or 0)} if slot else {}
            return Reply([self._t(uid, exc.code, **slots)], kind="refuse")
        if exc.code in ("account_frozen", "account_inactive"):
            return Reply([self._t(uid, "account_frozen")], kind="refuse")
        return Reply([self._t(uid, "failed")], kind="failed")

    # ------------------------------------------------------------ questions

    def _ask(self, uid: str, req: Request, field_name: str, key: str, profile: dict,
             options: list[dict] | None = None, **slots: Any) -> Reply:
        options = options or []
        lang = self.state(uid).lang
        sep = LIST_WORDS[lang]["sep"]
        if key == "ask_biller":
            slots["options"] = sep.join(
                f"{i}) {a.get('label_ar') or biller_name(a, lang)} ({biller_name(a, lang)})"
                for i, a in enumerate(options, 1))
        elif key == "ask_period":
            slots["options"] = sep.join(f"{i}) {_month_label(o['period'], lang)}" for i, o in enumerate(options, 1))
        if field_name == "operator" and not options:
            options = [{"operator": operator} for operator in OPERATOR_PREFIXES]
        question = Question(req, field_name, options, nonce=secrets.token_hex(4))
        choices = self._offer(uid, question, key)
        self.state(uid).question = question
        self._about(uid, req)
        if key in ("unknown_payee", "unknown_payee_or_number"):  # a fixed failure text, not a free question
            text = self._t(uid, key, **slots)
        else:
            text = self._phrase(uid, key, **slots)
        title = self._t(uid, "options_button") if choices else None
        question.shown = Reply([text], kind="ask", ask_field=field_name, options=list(choices), options_title=title)
        return Reply([text], kind="ask", ask_field=field_name, options=choices, options_title=title)

    def _ask_contacts(self, uid: str, req: Request, options: list[dict], profile: dict) -> Reply:
        words = LIST_WORDS[self.state(uid).lang]
        listed = words["sep"].join(
            f"{i}) {c['display_name']} ({words['ends'].format(last4=_digits(c['phone'])[-4:])})"
            for i, c in enumerate(options, 1))
        return self._ask(uid, req, "payee", "ask_which_payee", profile, options=options, options_text=listed)

    def _phrase(self, uid: str, key: str, **slots: Any) -> str:
        """A question in the user's language: written by a conversing core when there is one (the facts as
        placeholders, the template as its reference), else through the core's phrasing (slots 'gender', 'lang')."""
        st = self.state(uid)
        if "options_text" in slots:
            slots["options"] = slots.pop("options_text")
        if key not in TALK_TEMPLATE_ONLY and self._can_talk():
            try:
                reference = render(key, st.gender, st.lang, **{name: "{" + name + "}" for name in slots})
            except (KeyError, IndexError, ValueError):
                reference = ""
            if reference:
                required = tuple(name for name in slots if "{" + name + "}" in reference)
                written = self._talk(uid, key, text=st.last_text, facts=slots, required=required, reference=reference)
                if written is not None:
                    return written
                # the writer was asked and gave nothing: the template now, never a second model call after it
                return self._t(uid, key, **slots)
        return self.core.phrase(key, {"gender": st.gender, "lang": st.lang, **slots})

    # ------------------------------------------------------------ a conversing core (core.interface.ConversingCore)

    def _can_talk(self) -> bool:
        return callable(getattr(self.core, "talk", None))

    def talk(self, user_id: str, situation: str, *, text: str = "", facts: dict[str, Any] | None = None,
             required: tuple[str, ...] | list[str] = (), fallback: str, voice: bool | None = None) -> str:
        """A reply written by a conversing core for `situation` ("hello", "thanks", "unsupported_media",
        "small_talk", "intro_followup", "chat", or a question key), or `fallback` (the template, already filled in)
        when the core cannot talk, gives nothing, fails or writes something the checks refuse.

        `text` is the user's message (masked here: phone numbers as placeholders, digits as «#», a PIN never
        passed on). `facts` are the values the reply may name ({placeholder: value}); the core sees only their
        names and kinds and writes placeholders, which are filled in here. `required` must all be in the reply.
        `voice` None: the reply is spoken when the user's last message was a voice note.
        The exchange joins this conversation's memory. Nothing else changes: no card, no question, no state."""
        st = self.state(user_id)
        if self._can_talk():
            try:  # the gender, when the profile says it
                profile = self.wallet.user(user_id)
                st.gender, st.gender_known = self._gender(profile), (profile or {}).get("gender") in ("f", "m")
                st.demo = (profile or {}).get("source") == "demo"
            except Exception:  # the wallet is down: the last known gender
                pass
        raw = text or ""
        secret = bool(raw) and (_looks_like_pin(raw) or _worded_pin(raw))
        masked = "" if secret else self._mask(user_id, raw)
        facts = dict(facts or {})
        written = None
        if self._can_talk():
            reference = self._masked_reference(fallback, facts)
            written = self._talk(user_id, situation, text=masked, facts=facts, required=tuple(required),
                                 reference=reference, voice=voice)
        reply = written if written is not None else fallback
        self._remember_talk(user_id, masked, reply)
        return reply

    @staticmethod
    def _masked_reference(fallback: str, facts: dict[str, Any]) -> str:
        """The template with a placeholder where each fact's value stands (the longest value first)."""
        reference = str(fallback or "")
        shown = []
        for name, value in facts.items():
            if value is None or isinstance(value, bool):
                continue
            forms = {str(value)}
            if isinstance(value, int):
                forms.add(format_iqd(value))
            if name == "period" and isinstance(value, str):
                forms.add(format_period(value))
            shown += [(form, name) for form in forms if form.strip()]
        for form, name in sorted(shown, key=lambda item: -len(item[0])):  # whole words: «علي» is not in «عليك»
            edge = r"\d" if form.replace(",", "").isdigit() else r"\w"
            reference = re.sub(rf"(?<!{edge}){re.escape(form)}(?!{edge})", "{" + name + "}", reference)
        return reference

    def _talk(self, uid: str, situation: str, *, text: str = "", facts: dict[str, Any] | None = None,
              required: tuple[str, ...] = (), reference: str = "", voice: bool | None = None) -> str | None:
        """The conversing core's reply with its placeholders filled in from `facts`, or None. The core never sees a
        value: a reference that still holds a digit (a fact not handed over as one) is not sent at all."""
        talk = getattr(self.core, "talk", None)
        if not callable(talk) or situation in TALK_TEMPLATE_ONLY:
            return None
        st = self.state(uid)
        facts = {str(k): v for k, v in (facts or {}).items()
                 if v is not None and _PLACEHOLDER.fullmatch("{" + str(k) + "}")}
        if situation.endswith("_warm"):
            facts, required = {}, ()
        if _ANY_DIGIT.search(_PLACEHOLDER.sub(" ", reference or "")):
            return None
        required = tuple(name for name in required if name in facts)
        kinds = {name: ("money" if isinstance(value, int) and not isinstance(value, bool)
                        else "digits" if str(value).isdigit() else "text") for name, value in facts.items()}
        moment = self.clock()
        moment = moment.astimezone(BAGHDAD) if moment.tzinfo else moment.replace(tzinfo=timezone.utc).astimezone(BAGHDAD)
        spoken = voice if voice is not None else (st.source == "voice" or st.last_source == "voice")
        context: dict[str, Any] = {
            "user_id": uid,
            "language": st.lang,
            "gender": st.gender,
            "gender_known": st.gender_known,
            "text": _ANY_DIGIT.sub("#", text or ""),
            "reference": reference or "",
            "required": list(required),
            "question": bool(re.search(r"[?؟]", reference or "")),
            "history": [dict(turn) for turn in st.talk_history],
            "first_turn": not any(turn.get("role") == "assistant" for turn in st.talk_history),
            "time_of_day": "morning" if 5 <= moment.hour < 12 else "evening",  # as the channel's own hello
            "voice": bool(spoken),
            "demo": st.demo,
        }
        try:
            context["vocabulary"] = {"contacts": self._vocabulary(uid)["contacts"]}
        except Exception:  # the names are only for the core's own checks
            pass
        try:
            out = talk(situation, kinds, context)
        except Exception:  # a core that cannot write never breaks a turn: the template goes out
            return None
        if isinstance(out, str) and _claims_money_moved(out, reference, done=situation.startswith("done_")):
            return None  # only the code's own outcome lines say money moved, and nobody promises it back
        filled = self._fill(out, facts, required, st.lang)
        if filled is None:
            return None
        previous = next((t["text"] for t in reversed(st.talk_history) if t.get("role") == "assistant"), "")
        previous = _ACTION_NOTE.sub("", previous)
        if previous and " ".join(_ANY_DIGIT.sub("#", filled).split()) == " ".join(previous.split()):
            return None  # never the same reply twice in a row
        return filled

    @staticmethod
    def _fill(written: Any, facts: dict[str, Any], required: tuple[str, ...], lang: str) -> str | None:
        """The core's text with each placeholder replaced by the code's own value, or None when the text names a
        fact it was not given, leaves out a required one, or holds a number of its own."""
        if not isinstance(written, str) or not written.strip():
            return None
        text = written.strip()
        used = set(_PLACEHOLDER.findall(text))
        if used - set(facts) or set(required) - used:
            return None
        bare = _PLACEHOLDER.sub(" ", text)
        if "{" in bare or "}" in bare or _ANY_DIGIT.search(bare):
            return None

        def value(m: re.Match) -> str:
            name, v = m.group(1), facts[m.group(1)]
            if isinstance(v, int) and not isinstance(v, bool):
                return format_iqd(v)
            if name == "period":
                return format_period(str(v), lang)
            return str(v)

        text = _PLACEHOLDER.sub(value, text)
        return text if lang == "en" else text.replace("چ", "ج")

    def _warm_line(self, uid: str, situation: str) -> list[str]:
        """One short line from a conversing core after an outcome or a refusal ('done_warm' | 'refusal_warm'),
        with no fact at all in it; [] without one."""
        if not self._can_talk():
            return []
        written = self._talk(uid, situation)
        return [written] if written else []

    def _t(self, uid: str, key: str, **slots: Any) -> str:
        """A fixed message in this user's language and gender."""
        st = self.state(uid)
        return render(key, st.gender, st.lang, **slots)

    # ------------------------------------------------------------ buttons and execution

    def _on_button(self, uid: str, qid: str, button: str, pin: str | int | None, profile: dict,
                   voice: bool = False) -> Reply:
        st, g = self.state(uid), self._gender(profile)
        action = BUTTON_IDS.get((button or "").strip())
        if st.card is None or st.card.quote["id"] != qid:
            return Reply([self._t(uid, self._stale_key(uid, qid))], kind="refuse", quote_id=None)
        if action == "cancel":
            self._void_card(uid)
            st.queue.clear()
            return Reply([self._t(uid, "cancelled")], kind="cancelled", state="CANCELLED")
        if action == "change":
            request = st.card.request
            self._void_card(uid)
            st.question = Question(request, "change")
            if request.action == "pay_bill":  # the biller sets a bill's amount; say so
                biller = biller_name(request.account, st.lang)
                text = self._phrase(uid, "ask_change_bill", biller=biller)
            else:
                text = self._phrase(uid, "ask_change")
            st.question.shown = Reply([text], kind="ask", ask_field="change")  # said again after an old list is tapped
            return Reply([text], kind="ask", ask_field="change")
        if action != "confirm":
            return Reply([self._phrase(uid, "press_button")], card=self._open_card(uid), kind="card", state="QUOTED",
                         quote_id=qid)
        if voice and (pin is None or str(pin).strip() == ""):
            return self._execute(uid, None, profile, factor="voice")
        if pin is None or str(pin).strip() == "":
            st.awaiting_pin = qid  # a PIN typed in the chat next is caught, never sent to the core
            return Reply([self._phrase(uid, "ask_pin")], card=self._open_card(uid), kind="card", state="QUOTED",
                         quote_id=qid)
        return self._execute(uid, str(pin).strip(), profile)

    def _stale_key(self, uid: str, qid: str) -> str:
        """A tap on a card that is no longer open. If it was already paid, say so: never «nothing was taken»."""
        try:
            quote = self.wallet.quote(qid) if qid else None
        except Exception:  # unknown quote or wallet down: the plain stale-card line is still true
            return "stale_card"
        if isinstance(quote, dict) and quote.get("user_id") == uid and quote.get("state") == "SUCCEEDED":
            return "card_already_paid"
        return "stale_card"

    def _execute(self, uid: str, pin: str | None, profile: dict, factor: str = "pin") -> Reply:
        st, g = self.state(uid), self._gender(profile)
        open_card = st.card
        qid, req = open_card.quote["id"], open_card.request
        self._about(uid, req)  # what the card was built on, before what the tap did
        try:
            quote, settled = self._execute_once(qid, pin, factor)
        except WalletError as exc:
            if exc.code == "voice_not_approved":  # nothing moved: the same card, now with the PIN
                self._note(uid, "voice_refused", "wallet:execute")
                st.awaiting_pin = qid
                return Reply([], card=open_card.card, kind="card", state="QUOTED", quote_id=qid)
            if exc.code == "wrong_pin":  # nothing moved; the same card can be tapped again
                self._note(uid, "pin_wrong", "wallet:execute")
                st.awaiting_pin = qid
                return Reply([self._t(uid, "wrong_pin")], card=open_card.card, kind="card", state="QUOTED",
                             quote_id=qid)
            if exc.code in ("pin_locked", "pin_blocked"):  # too many wrong PINs: nothing moved, close this card
                self._void_card(uid)
                st.queue.clear()
                return Reply([self._t(uid, exc.code)], kind="failed")
            if exc.code in ("account_frozen", "account_inactive", "pin_not_set"):
                self._void_card(uid)
                st.queue.clear()
                return Reply([self._t(uid, "pin_not_set" if exc.code == "pin_not_set" else "account_frozen")],
                             kind="refuse")
            st.card = None
            if exc.code == "unknown_quote":
                return Reply([self._t(uid, "stale_card")], kind="refuse")
            return Reply([self._t(uid, "failed")], kind="failed")
        st.card = None
        read_back = settled
        if quote is not None and quote.get("state") == "SUCCEEDED":  # «done» comes from the wallet's record only
            quote, read_back = self._confirm_paid(uid, qid, quote, settled)
        if quote is None:  # still no answer: tell the truth and keep checking
            st.unknown[qid] = req
            st.queue.clear()
            self._note(uid, "unknown", "wallet:execute")
            return Reply([self._t(uid, "unknown_outcome")], kind="unknown", state="UNKNOWN")
        self._note_outcome(uid, quote, read_back, factor)
        reply = self._outcome(uid, quote, profile, settled)
        # The outcome line stays the code's own words. A hold gets no warm line: nobody has the money yet, and a
        # "payment succeeded" line could say it arrived.
        if not st.queue and reply.kind in ("done", "failed") and not reply.held:
            warm = self._warm_line(uid, "done_warm" if reply.kind == "done" else "refusal_warm")
            reply.lines += warm
            reply.warm = bool(warm)
        if st.queue:
            try:
                reply = self._next_in_queue(uid, reply, profile)
            except (WalletUnavailable, WalletError):
                # The payment above is final: report it, never replace it with a wallet error.
                st.queue.clear()
                reply.lines.append(self._t(uid, "next_request_failed"))
        return reply

    def _execute_once(self, qid: str, pin: str | None, factor: str = "pin") -> tuple[dict | None, bool]:
        """Execute with one same-key retry, then read the quote back.

        Returns (final quote or None if still unknown, whether we had to settle it ourselves).
        """
        for _ in range(EXECUTE_ATTEMPTS):
            try:
                if factor == "pin":
                    return self.wallet.execute(qid, pin), False
                return self.wallet.execute(qid, None, factor=factor), False
            except WalletUnavailable:
                continue  # UNKNOWN: retry with the SAME key; the wallet never pays a quote twice
        return self._settle(qid), True

    @staticmethod
    def _is_paid(quote: Any, qid: str, uid: str) -> bool:
        """The wallet's record of this very quote says it was paid: this quote, this user (when the record names
        one), state SUCCEEDED, and a transaction to show for it."""
        if not isinstance(quote, dict) or quote.get("id") != qid or quote.get("state") != "SUCCEEDED":
            return False
        if quote.get("user_id") not in (None, uid):
            return False
        result = quote.get("result")
        return isinstance(result, dict) and bool(result.get("transaction_id"))

    def _confirm_paid(self, uid: str, qid: str, answer: dict, settled: bool) -> tuple[dict | None, bool]:
        """An answer that says SUCCEEDED, checked before anyone hears «done». Returns (the record to report, whether
        it was read back from the wallet), or (None, False) when the outcome is not known after all.

        - It must be the record of this quote, with a transaction id; if not, it proves nothing.
        - The quote is then read back. A record that says paid is the one reported. A record that says it ended any
          other way is reported instead (never «done»). A record still in flight means unknown.
        - If the wallet cannot be read just now, its own answer to the payment stands: that answer is its record."""
        proven = self._is_paid(answer, qid, uid)
        if settled:  # the answer already is a read of the record
            return (answer, True) if proven else (None, False)
        try:
            record = self.wallet.quote(qid)
        except (WalletError, WalletUnavailable):
            return (answer, False) if proven else (None, False)
        if self._is_paid(record, qid, uid):
            return record, True
        if isinstance(record, dict) and record.get("id") == qid and record.get("state") in FINAL_STATES - {"SUCCEEDED"}:
            return record, True
        return None, False

    def _note_outcome(self, uid: str, quote: dict, read_back: bool, factor: str = "pin") -> None:
        """What the wallet's record says after a tap: the transaction and its state, and when."""
        result = quote.get("result") or {}
        source = "wallet:readback" if read_back else "wallet:execute"
        if quote.get("state") == "SUCCEEDED":
            if factor == "voice":
                self._note(uid, "voice_ok", "wallet:execute")
            else:
                self._note(uid, "pin_ok", "wallet:execute")
            self._note(uid, "paid" if read_back else "paid_unread", source,
                       reference=short_reference(result.get("transaction_id")),
                       time=_baghdad_time(result.get("completed_at") or quote.get("created_at"), with_date=True),
                       transaction=str(result.get("transaction_id") or ""), state=str(quote.get("state")))
        else:
            self._note(uid, "not_paid", source, reason=str(result.get("reason") or quote.get("state") or "").lower(),
                       state=str(quote.get("state") or ""))

    def _settle(self, qid: str) -> dict | None:
        """Read an UNKNOWN quote back. If it never ran, close it so it never can, and say so."""
        try:
            quote = self.wallet.quote(qid)
            if quote["state"] in FINAL_STATES:
                return quote
            if quote["state"] == "QUOTED":
                return self.wallet.cancel(qid)
        except WalletError as exc:
            if exc.code == "not_cancellable" and isinstance(exc.detail.get("quote"), dict):
                quote = exc.detail["quote"]
                return quote if quote.get("state") in FINAL_STATES else None
        except WalletUnavailable:
            pass
        return None

    def _outcome(self, uid: str, quote: dict, profile: dict, settled: bool) -> Reply:
        """The result of an execute, in fixed words that say whether money moved."""
        g = self._gender(profile)
        state, result, t = quote["state"], quote.get("result") or {}, _target(quote)
        qid = quote["id"]
        if state == "SUCCEEDED":
            balance = result.get("balance_after", profile["balance"])
            reference = short_reference(result.get("transaction_id"))  # the receipt names the wallet's transaction
            more = [self._t(uid, "done_reference", reference=reference)] if reference else []
            if quote["kind"] == "transfer" and result.get("held"):
                link = f"https://wa.me/{SERVICE_NUMBER}?text={urlquote('افتح حساب')}"
                more.append(self._t(uid, "hold_share", link=link))
                line = self._t(uid, "done_hold", amount=quote["amount"], last4=_digits(t.get("phone"))[-4:],
                              balance=balance)
            elif quote["kind"] == "transfer":
                line = self._t(uid, "done_transfer", amount=quote["amount"], name=t.get("display_name", ""),
                              balance=balance)
                saved = self._auto_save(uid, t)
                if saved:
                    more.append(saved)
            elif quote["kind"] == "bill":
                line = self._t(uid, "done_bill", biller=biller_name(t, self.state(uid).lang), period=t.get("period", ""),
                              amount=quote["amount"], balance=balance)
            else:
                line = self._t(uid, "done_topup", amount=quote["amount"], last4=_digits(t.get("phone"))[-4:],
                              balance=balance)
            return Reply([line, *more], kind="done", state=state, reference=reference or None,
                         held=bool(quote["kind"] == "transfer" and result.get("held")))
        reason = result.get("reason")
        biller = biller_name(t, self.state(uid).lang)
        if state == "CANCELLED":
            line = self._t(uid, "unknown_resolved_failed" if settled else "cancelled")
        elif state == "EXPIRED" or reason == "expired":
            line = self._t(uid, "quote_expired")
        elif reason == "insufficient_balance":
            fresh = self._fresh_profile(uid, profile)
            key = reason if quote.get("fee") else "insufficient_balance_no_fee"
            line = self._t(uid, key, balance=fresh["balance"], needed=quote.get("total", quote["amount"]))
        elif reason == "over_daily_limit":
            fresh = self._fresh_profile(uid, profile)
            line = self._t(uid, reason, limit=fresh.get("daily_limit") or 0, remaining=fresh.get("daily_remaining") or 0)
        elif reason == "pin_reset_cooldown":
            line = self._t(uid, reason, limit=PIN_RESET_COOLDOWN_IQD, remaining=0)
        elif reason == "already_paid":
            line = self._t(uid, "already_paid", biller=biller, period=t.get("period", ""))
        elif reason == "biller_down":
            line = self._t(uid, "biller_down", biller=biller)
        elif reason == "biller_declined":
            line = self._t(uid, "biller_declined_refunded", biller=biller)
        elif reason in ("recipient_closed", "hold_limit", "hold_cap", "bill_changed"):
            slot = REFUSAL_SLOTS.get(reason)
            line = self._t(uid, reason, **({slot: int(result.get(slot) or 0)} if slot else {}))
        else:
            line = self._t(uid, "failed")
        return Reply([line], kind="failed", state=state)

    def _auto_save(self, uid: str, target: dict) -> str | None:
        """After paying a number that has an account: save it to the user's contacts (once)."""
        if self.accounts is None or target.get("contact_id") or not target.get("payee_user_id"):
            return None
        e164 = target.get("phone_e164") or phones.canonical(target.get("phone"))
        name = target.get("registered_name") or target.get("display_name") or ""
        if not e164 or not name:
            return None
        try:
            status, _ = self.accounts.add_contact(uid, name, e164, source="auto_transfer")
        except Exception:
            return None
        return self._t(uid, "auto_saved", name=name, last4=self._unique_tail(uid, e164)) if status == "added" else None

    def _unique_tail(self, uid: str, e164: str) -> str:
        """The shortest end of a number (4 digits or more) that no other saved contact shares, for «سمّي 0002 …»."""
        try:
            numbers = [c.get("phone_e164") or phones.canonical(c.get("phone")) or "" for c in self.accounts.contacts(uid)]
        except Exception:
            return e164[-4:]
        for n in range(4, 10):
            if sum(1 for x in numbers if x.endswith(e164[-n:])) <= 1:
                return e164[-n:]
        return "0" + e164[3:]

    def _fresh_profile(self, uid: str, profile: dict) -> dict:
        """Re-read the profile for exact numbers; the turn's copy if the wallet does not answer."""
        try:
            return self.wallet.user(uid)
        except WalletUnavailable:
            return profile

    def _next_in_queue(self, uid: str, reply: Reply, profile: dict) -> Reply:
        """Two-in-one: after the first payment, offer the next request as its own card."""
        st = self.state(uid)
        nxt = st.queue.pop(0)
        follow = self._advance(uid, nxt, self.wallet.user(uid))
        if follow.card is not None and reply.state == "SUCCEEDED":
            intro = self._phrase(uid, "offer_next", next=self._describe(nxt, st.lang))
        else:
            intro = self._t(uid, "next_request")
        lines = reply.lines + [intro] + follow.lines
        kind = follow.kind if follow.kind in ("card", "ask") else reply.kind
        return Reply(lines, card=follow.card, kind=kind, state=follow.state or reply.state,
                     quote_id=follow.quote_id, ask_field=follow.ask_field, offered_amount=follow.offered_amount,
                     options=follow.options, options_title=follow.options_title, options_role=follow.options_role,
                     reference=reply.reference)

    @staticmethod
    def _describe(req: Request, lang: str = language.DEFAULT) -> str:
        words = DESCRIBE.get(lang, DESCRIBE[language.DEFAULT])
        if req.action == "transfer" and req.contact:
            return words["transfer"].format(name=req.contact["display_name"])
        if req.action == "pay_bill" and req.account:
            return words["bill"].format(biller=biller_name(req.account, lang))
        return words["topup"] if req.action == "topup" else words["other"]

    # ------------------------------------------------------------ housekeeping

    def _void_card(self, uid: str) -> None:
        """Forget the open card and cancel its quote, so it can never be executed later."""
        st = self.state(uid)
        open_card, st.card = st.card, None
        if open_card is None:
            return
        try:
            self.wallet.cancel(open_card.quote["id"])
        except (WalletError, WalletUnavailable):
            pass  # already final or unreachable; we no longer hold its id, so we never execute it

    def _reconcile(self, uid: str, profile: dict) -> list[str]:
        """Lines about earlier UNKNOWN payments whose outcome is now known."""
        st = self.state(uid)
        lines = []
        for qid in list(st.unknown):
            quote = self._settle(qid)
            if quote is None or (quote.get("state") == "SUCCEEDED" and not self._is_paid(quote, qid, uid)):
                continue  # still not known: «done» needs the record of this quote, with its transaction
            self._about(uid, st.unknown.pop(qid))
            self._note_outcome(uid, quote, True)
            lines += self._outcome(uid, quote, profile, settled=True).lines
        return lines

    @staticmethod
    def _gender(profile: dict | None) -> str:
        # Unknown (a demo account, an old profile): the masculine default, as the channel's own runtime uses, so one
        # chat never flips between forms. A known gender is always used.
        return "f" if (profile or {}).get("gender") == "f" else "m"

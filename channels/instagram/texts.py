"""The Instagram channel's own words: Baghdadi Arabic (the reference), Modern Standard Arabic and English.

Only what linking and the platform's limits need; everything about payments and accounts comes from the same places
as on WhatsApp. Iraqi text writes ج, never the Persian letter.

Each entry is one text or {"f": ..., "m": ...}. Before an account is linked we do not know who we talk to, and the
masculine is used, as accounts/texts.py does for someone unknown.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from payments.messages import format_iqd
from payments.text import fold

# What the person writes in the WhatsApp chat to get a link code, and to cut the link. The wa.me link carries the
# first one, already typed.
LINK_PHRASE = {"iq": "اربط انستغرام", "msa": "ربط إنستغرام", "en": "link instagram"}
UNLINK_PHRASE = {"iq": "افصل انستغرام", "msa": "فصل إنستغرام", "en": "unlink instagram"}

IQ: dict[str, str | dict[str, str]] = {
    # ---- in the Instagram chat ----
    "not_linked": {
        "f": "هلا بيج! حتى تستخدمين محفظتج من انستغرام، اربطي هذا الحساب بحسابج على واتساب أول:\n"
             "1. افتحي محادثتنا على واتساب: {wa_link}\n"
             "2. اكتبي هناك «{phrase}» ويوصلج رمز من 6 أرقام.\n"
             "3. ارجعي لهنا واكتبي الرمز.\n"
             "الرمز يخلص وقته بعد {minutes} دقايق. ما عندج حساب بعد؟ سجلي من واتساب أول.",
        "m": "هلا بيك! حتى تستخدم محفظتك من انستغرام، اربط هذا الحساب بحسابك على واتساب أول:\n"
             "1. افتح محادثتنا على واتساب: {wa_link}\n"
             "2. اكتب هناك «{phrase}» ويوصلك رمز من 6 أرقام.\n"
             "3. ارجع لهنا واكتب الرمز.\n"
             "الرمز يخلص وقته بعد {minutes} دقايق. ما عندك حساب بعد؟ سجل من واتساب أول.",
    },
    "link_ok": {
        "f": "تم الربط ✅ هسه تگدرين تستخدمين محفظتج من هنا. اكتبيلي طلبج، مثلاً «شكد رصيدي».",
        "m": "تم الربط ✅ هسه تگدر تستخدم محفظتك من هنا. اكتبلي طلبك، مثلاً «شكد رصيدي».",
    },
    "wrong_code": {
        "f": "الرمز مو صحيح أو خلص وقته، وما انربط شي. تأكدي منه واكتبيه مرة ثانية. المحاولات الباقية: {left}.",
        "m": "الرمز مو صحيح أو خلص وقته، وما انربط شي. تأكد منه واكتبه مرة ثانية. المحاولات الباقية: {left}.",
    },
    "locked": {
        "f": "انكتبت رموز غلط هواية، فالربط موقوف {minutes} دقيقة. بعدها اطلبي رمز جديد من واتساب.",
        "m": "انكتبت رموز غلط هواية، فالربط موقوف {minutes} دقيقة. بعدها اطلب رمز جديد من واتساب.",
    },
    "voice_not_here": {
        "f": "الرد الصوتي ما يشتغل على انستغرام، فأجاوبج كتابة. إذا تريدين رد صوتي راسليني على واتساب.",
        "m": "الرد الصوتي ما يشتغل على انستغرام، فأجاوبك كتابة. إذا تريد رد صوتي راسلني على واتساب.",
    },
    # ---- in the WhatsApp chat ----
    "link_code": {
        "f": "رمز ربط انستغرام: {code}\n"
             "اكتبيه بمحادثتنا على انستغرام خلال {minutes} دقايق. يشتغل مرة وحدة بس.\n"
             "إذا مو انتي اللي طلبتيه، لا تنطينه لأي أحد وتجاهلي هاي الرسالة.",
        "m": "رمز ربط انستغرام: {code}\n"
             "اكتبه بمحادثتنا على انستغرام خلال {minutes} دقايق. يشتغل مرة وحدة بس.\n"
             "إذا مو انت اللي طلبته، لا تنطيه لأي أحد وتجاهل هاي الرسالة.",
    },
    "too_many_codes": {
        "f": "طلبتي رموز هواية بهالساعة. جربي بعد {minutes} دقيقة.",
        "m": "طلبت رموز هواية بهالساعة. جرب بعد {minutes} دقيقة.",
    },
    "linked_notice": {
        "f": "انربط حساب انستغرام بمحفظتج هسه. إذا مو انتي، اكتبي «{phrase}» هنا وينفصل فوراً.",
        "m": "انربط حساب انستغرام بمحفظتك هسه. إذا مو انت، اكتب «{phrase}» هنا وينفصل فوراً.",
    },
    "unlinked": {
        "f": "انفصل حساب انستغرام عن محفظتج. حتى ترجعين تربطينه اكتبي «{phrase}».",
        "m": "انفصل حساب انستغرام عن محفظتك. حتى ترجع تربطه اكتب «{phrase}».",
    },
    "not_linked_here": {
        "f": "ماكو حساب انستغرام مربوط بمحفظتج.",
        "m": "ماكو حساب انستغرام مربوط بمحفظتك.",
    },
    # ---- in the Instagram chat, once linked ----
    "already_linked": {
        "f": "هذا الحساب مربوط بمحفظتج من قبل. اكتبيلي طلبج، مثلاً «شكد رصيدي».",
        "m": "هذا الحساب مربوط بمحفظتك من قبل. اكتبلي طلبك، مثلاً «شكد رصيدي».",
    },
    "whatsapp_only": {
        "f": "هاي الخطوة تصير على واتساب بس، لأن حسابج مربوط برقمج هناك: {wa_link}",
        "m": "هاي الخطوة تصير على واتساب بس، لأن حسابك مربوط برقمك هناك: {wa_link}",
    },
    # ---- to the admin (WHATSAPP_MANAGER) ----
    "manager_link_flood": "انستغرام: انكتب {count} رمز ربط غلط أو أكثر بساعة وحدة. اللي غلطوا ينتظرون {minutes} دقيقة؛ "
                          "اللي عنده رمز صحيح يكدر يربط عادي.",
}

MSA: dict[str, str | dict[str, str]] = {
    "not_linked": {
        "f": "أهلاً بكِ! لاستخدام محفظتكِ من إنستغرام، اربطي هذا الحساب بحسابكِ على واتساب أولاً:\n"
             "1. افتحي محادثتنا على واتساب: {wa_link}\n"
             "2. اكتبي هناك «{phrase}» وسيصلكِ رمز من 6 أرقام.\n"
             "3. عودي إلى هنا واكتبي الرمز.\n"
             "تنتهي صلاحية الرمز بعد {minutes} دقائق. ليس لديكِ حساب بعد؟ سجّلي من واتساب أولاً.",
        "m": "أهلاً بك! لاستخدام محفظتك من إنستغرام، اربط هذا الحساب بحسابك على واتساب أولاً:\n"
             "1. افتح محادثتنا على واتساب: {wa_link}\n"
             "2. اكتب هناك «{phrase}» وسيصلك رمز من 6 أرقام.\n"
             "3. عد إلى هنا واكتب الرمز.\n"
             "تنتهي صلاحية الرمز بعد {minutes} دقائق. ليس لديك حساب بعد؟ سجّل من واتساب أولاً.",
    },
    "link_ok": {
        "f": "تم الربط ✅ يمكنكِ الآن استخدام محفظتكِ من هنا. اكتبي لي طلبكِ، مثلاً «كم رصيدي».",
        "m": "تم الربط ✅ يمكنك الآن استخدام محفظتك من هنا. اكتب لي طلبك، مثلاً «كم رصيدي».",
    },
    "wrong_code": {
        "f": "الرمز غير صحيح أو انتهت صلاحيته، ولم يُربط شيء. تأكدي منه واكتبيه مرة أخرى. المحاولات المتبقية: {left}.",
        "m": "الرمز غير صحيح أو انتهت صلاحيته، ولم يُربط شيء. تأكد منه واكتبه مرة أخرى. المحاولات المتبقية: {left}.",
    },
    "locked": {
        "f": "كُتبت رموز خاطئة كثيرة، فالربط متوقف {minutes} دقيقة. بعدها اطلبي رمزاً جديداً من واتساب.",
        "m": "كُتبت رموز خاطئة كثيرة، فالربط متوقف {minutes} دقيقة. بعدها اطلب رمزاً جديداً من واتساب.",
    },
    "voice_not_here": {
        "f": "الرد الصوتي غير متاح على إنستغرام، فسأجيبكِ كتابةً. إن أردتِ رداً صوتياً فراسليني على واتساب.",
        "m": "الرد الصوتي غير متاح على إنستغرام، فسأجيبك كتابةً. إن أردت رداً صوتياً فراسلني على واتساب.",
    },
    "link_code": {
        "f": "رمز ربط إنستغرام: {code}\n"
             "اكتبيه في محادثتنا على إنستغرام خلال {minutes} دقائق. يعمل مرة واحدة فقط.\n"
             "إن لم تطلبيه أنتِ فلا تعطيه لأحد وتجاهلي هذه الرسالة.",
        "m": "رمز ربط إنستغرام: {code}\n"
             "اكتبه في محادثتنا على إنستغرام خلال {minutes} دقائق. يعمل مرة واحدة فقط.\n"
             "إن لم تطلبه أنت فلا تعطه لأحد وتجاهل هذه الرسالة.",
    },
    "too_many_codes": {
        "f": "طلبتِ رموزاً كثيرة في هذه الساعة. جرّبي بعد {minutes} دقيقة.",
        "m": "طلبت رموزاً كثيرة في هذه الساعة. جرّب بعد {minutes} دقيقة.",
    },
    "linked_notice": {
        "f": "رُبط حساب إنستغرام بمحفظتكِ الآن. إن لم تكوني أنتِ فاكتبي «{phrase}» هنا وسيُفصل فوراً.",
        "m": "رُبط حساب إنستغرام بمحفظتك الآن. إن لم تكن أنت فاكتب «{phrase}» هنا وسيُفصل فوراً.",
    },
    "unlinked": {
        "f": "فُصل حساب إنستغرام عن محفظتكِ. لربطه من جديد اكتبي «{phrase}».",
        "m": "فُصل حساب إنستغرام عن محفظتك. لربطه من جديد اكتب «{phrase}».",
    },
    "not_linked_here": {
        "f": "لا يوجد حساب إنستغرام مربوط بمحفظتكِ.",
        "m": "لا يوجد حساب إنستغرام مربوط بمحفظتك.",
    },
    "already_linked": {
        "f": "هذا الحساب مربوط بمحفظتكِ من قبل. اكتبي لي طلبكِ، مثلاً «كم رصيدي».",
        "m": "هذا الحساب مربوط بمحفظتك من قبل. اكتب لي طلبك، مثلاً «كم رصيدي».",
    },
    "whatsapp_only": {
        "f": "هذه الخطوة على واتساب فقط، لأن حسابكِ مرتبط برقمكِ هناك: {wa_link}",
        "m": "هذه الخطوة على واتساب فقط، لأن حسابك مرتبط برقمك هناك: {wa_link}",
    },
    "manager_link_flood": "إنستغرام: كُتب {count} رمز ربط خاطئ أو أكثر خلال ساعة واحدة. من أخطأ ينتظر {minutes} "
                          "دقيقة؛ ومن لديه رمز صحيح يستطيع الربط كالمعتاد.",
}

EN: dict[str, str | dict[str, str]] = {
    "not_linked": "Hello! To use your wallet from Instagram, first link this account to your account on WhatsApp:\n"
                  "1. Open our WhatsApp chat: {wa_link}\n"
                  "2. Write “{phrase}” there and you will get a 6-digit code.\n"
                  "3. Come back here and type the code.\n"
                  "The code expires after {minutes} minutes. No account yet? Sign up on WhatsApp first.",
    "link_ok": "Linked ✅ You can now use your wallet from here. Type your request, for example “what's my balance”.",
    "wrong_code": "That code is wrong or has expired, and nothing was linked. Check it and type it again. "
                  "Tries left: {left}.",
    "locked": "Too many wrong codes were typed, so linking is paused for {minutes} minutes. After that, ask for a "
              "new code on WhatsApp.",
    "voice_not_here": "Voice replies don't work on Instagram, so I'll answer in writing. For a voice reply, "
                      "message me on WhatsApp.",
    "link_code": "Instagram link code: {code}\n"
                 "Type it in our Instagram chat within {minutes} minutes. It works once only.\n"
                 "If you didn't ask for it, don't give it to anyone and ignore this message.",
    "too_many_codes": "You asked for too many codes this hour. Try again in {minutes} minutes.",
    "linked_notice": "An Instagram account was just linked to your wallet. If that wasn't you, write “{phrase}” "
                     "here and it is cut at once.",
    "unlinked": "The Instagram account was unlinked from your wallet. To link it again, write “{phrase}”.",
    "not_linked_here": "No Instagram account is linked to your wallet.",
    "already_linked": "This account is already linked to your wallet. Type your request, for example "
                      "“what's my balance”.",
    "whatsapp_only": "This step is on WhatsApp only, because your account is tied to your number there: {wa_link}",
    "manager_link_flood": "Instagram: {count} or more wrong link codes in one hour. Senders who typed a wrong code "
                          "wait {minutes} minutes; anyone with a right code still links.",
}

BY_LANG: dict[str, dict] = {"iq": IQ, "msa": MSA, "en": EN}


def say(key: str, gender: str | None = "m", lang: str | None = None, **slots) -> str:
    """Fill a text in `lang` ('iq' default | 'msa' | 'en'); integers get thousands separators."""
    template = BY_LANG.get(lang or "iq", IQ).get(key, IQ[key])
    if isinstance(template, dict):
        template = template["f" if gender == "f" else "m"]
    values = {k: (format_iqd(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in slots.items()}
    return template.format(**values)


def link_phrase(lang: str | None = None) -> str:
    return LINK_PHRASE.get(lang or "iq", LINK_PHRASE["iq"])


def unlink_phrase(lang: str | None = None) -> str:
    return UNLINK_PHRASE.get(lang or "iq", UNLINK_PHRASE["iq"])


def wa_link(service_number: str, lang: str | None = None) -> str:
    """A wa.me link to the service's WhatsApp number that opens the chat with the link request already typed."""
    digits = re.sub(r"\D", "", service_number or "")
    return f"https://wa.me/{digits}?text={quote(link_phrase(lang))}"


def minutes(seconds: float) -> int:
    """Seconds as whole minutes for a text, rounded up, at least 1."""
    return max(1, int(-(-max(0.0, float(seconds)) // 60)))


_INSTAGRAM = r"(?:انستغرام|انستكرام|انستقرام|انستجرام|الانستغرام|الانستا|انستا|instagram|insta|ig)"
_UNLINK = re.compile(rf"^(?:افصل|افصلي|فصل|الغ ربط|الغي ربط|الغاء ربط|unlink|disconnect)\s+(?:حساب\s+)?{_INSTAGRAM}$")
_LINK = re.compile(rf"^(?:اربط|اربطي|ربط|link|connect)\s+(?:حساب\s+)?{_INSTAGRAM}$")


def _plain(text: object) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", fold(text)).split()) if isinstance(text, str) and len(text) <= 60 else ""


def wants_link(text: object) -> bool:
    """A WhatsApp message that asks for an Instagram link code, in any of the three languages (the whole message)."""
    return bool(_LINK.match(_plain(text)))


def wants_unlink(text: object) -> bool:
    return bool(_UNLINK.match(_plain(text)))


def request_language(text: object, lang: str) -> str:
    """The language to answer a link or unlink request in: English when it was written in Latin letters («link
    instagram» is too short for the language guesser, which reads it as Arabizi), else the conversation's."""
    if isinstance(text, str) and text.strip() and not re.search("[؀-ۿ]", text):
        return "en"
    return lang


def all_texts(lang: str | None = None) -> list[str]:
    """Every string of one language, or of all three (the tests check the Persian letter and the length)."""
    out: list[str] = []
    for table in [BY_LANG[lang]] if lang else BY_LANG.values():
        for value in table.values():
            out += list(value.values()) if isinstance(value, dict) else [value]
    return out

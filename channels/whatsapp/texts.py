"""The WhatsApp channel's own words, in Baghdadi Arabic (MSA and English: texts_i18n.py). ج, never the Persian letter.

Everything about payments (cards, questions, failures, outcomes) comes from the orchestrator and
payments/messages.py, and everything about accounts (sign-up, contacts, history, notices) from
accounts/texts.py; these are only the channel's extras: the PIN step after the tap, receipts, and the
replies for message types the agent cannot read.

Each entry is one text or {"f": ..., "m": ...}, picked by the account holder's gender, the same way
as payments.messages.render.
"""

from __future__ import annotations

from channels.whatsapp.texts_i18n import HELLO, TEXTS_EN, TEXTS_MSA
from payments.messages import format_iqd

TEXTS: dict[str, str | dict[str, str]] = {
    # Like the orchestrator's lines, the channel's imperatives follow the account holder's gender.
    "paid_once": {
        "f": "هاي البطاقة اندفعت قبل، مرة وحدة بس. ما راح ينخصم منج مرة ثانية. هذا وصلها:",
        "m": "هاي البطاقة اندفعت قبل، مرة وحدة بس. ما راح ينخصم منك مرة ثانية. هذا وصلها:",
    },
    "thanks": {
        "f": "العفو! إذا تحتاجين شي ثاني اكتبيلي، مثلاً «شكد رصيدي».",
        "m": "العفو! إذا تحتاج شي ثاني اكتبلي، مثلاً «شكد رصيدي».",
    },
    "heard": {"f": "سمعتج تگولين: «{text}»", "m": "سمعتك تگول: «{text}»"},
    "voice_needs_full": {
        "f": "الرسائل الصوتية تشتغل ويا النسخة الكاملة من المساعد. هسه اكتبيلي طلبج.",
        "m": "الرسائل الصوتية تشتغل ويا النسخة الكاملة من المساعد. هسه اكتبلي طلبك.",
    },
    "unsupported_image": {
        "f": "وصلتني صورة، بس أنا أفهم الكتابة{voice_or} بس. اكتبيلي طلبج، مثلاً «ادفع المولدة».",
        "m": "وصلتني صورة، بس أنا أفهم الكتابة{voice_or} بس. اكتبلي طلبك، مثلاً «ادفع المولدة».",
    },
    "unsupported_sticker": {
        "f": "ستيكر حلو! بس أنا أفهم الكتابة{voice_or} بس. اكتبيلي طلبج، مثلاً «ادفع المولدة».",
        "m": "ستيكر حلو! بس أنا أفهم الكتابة{voice_or} بس. اكتبلي طلبك، مثلاً «ادفع المولدة».",
    },
    "unsupported_location": {
        "f": "وصلني الموقع، بس الدفع ما يحتاج موقع. اكتبيلي طلبج، مثلاً «ادفع المولدة».",
        "m": "وصلني الموقع، بس الدفع ما يحتاج موقع. اكتبلي طلبك، مثلاً «ادفع المولدة».",
    },
    "unsupported_other": {
        "f": "هالنوع من الرسائل ما أگدر أفتحه. اكتبيلي طلبج{voice_send}، مثلاً «ادفع المولدة».",
        "m": "هالنوع من الرسائل ما أگدر أفتحه. اكتبلي طلبك{voice_send}، مثلاً «ادفع المولدة».",
    },
    # Other wordings of the same line (Channel._variant): a second «شكراً» or sticker never gets the same bubble.
    "thanks_2": {"f": "تدللين! أي وقت تحتاجين شي اكتبيلي.", "m": "تدلل! أي وقت تحتاج شي اكتبلي."},
    "thanks_3": {"f": "ولا يهمج، آني هنا إذا احتجتي شي.", "m": "ولا يهمك، آني هنا إذا احتجت شي."},
    "unsupported_image_2": {"f": "الصور ما أگدر أقراها، بس الكتابة{voice_or}. اكتبيلي شنو تحتاجين.",
                            "m": "الصور ما أگدر أقراها، بس الكتابة{voice_or}. اكتبلي شنو تحتاج."},
    "unsupported_sticker_2": {"f": "حلو! بس أنا أقرا الكتابة{voice_or} بس، اكتبيلي شنو تحتاجين.",
                              "m": "حلو! بس أنا أقرا الكتابة{voice_or} بس، اكتبلي شنو تحتاج."},
    "unsupported_location_2": {"f": "تسلمين، بس الدفع ما يحتاج موقع. اكتبيلي شنو تحتاجين.",
                               "m": "تسلم، بس الدفع ما يحتاج موقع. اكتبلي شنو تحتاج."},
    "unsupported_other_2": {"f": "ما گدرت أفتح هالرسالة. اكتبيلي طلبج{voice_send}.",
                            "m": "ما گدرت أفتح هالرسالة. اكتبلي طلبك{voice_send}."},
    "voice_or": " والرسائل الصوتية",
    "voice_send": {"f": " أو دزيه رسالة صوتية", "m": " أو دزه رسالة صوتية"},
    "rate_limited": {
        "f": "وصلتي حد الرسائل بهالساعة ({limit} رسالة). ارتاحي شوية وارجعيلي بعد ساعة. ما انخصم منج شي.",
        "m": "وصلت حد الرسائل بهالساعة ({limit} رسالة). ارتاح شوية وارجعلي بعد ساعة. ما انخصم منك شي.",
    },
    "error": {
        "f": "صار خلل عدنا بهالرسالة. إذا ما وصلج وصل، الدفعة ما تمت. جربي بعد شوية.",
        "m": "صار خلل عدنا بهالرسالة. إذا ما وصلك وصل، الدفعة ما تمت. جرب بعد شوية.",
    },
    # ---- the card (an interactive message) ----
    "card_header_transfer": "تأكيد تحويل",
    "card_header_bill": "تأكيد دفع فاتورة",
    "card_header_topup": "تأكيد شحن رصيد",
    # ---- the receipt, from the wallet's own record ----
    "receipt_transfer": "تحويل إلى: {payee}",
    "receipt_bill": "فاتورة: {payee}، عن شهر {period}",
    "receipt_topup": "شحن رصيد للرقم المنتهي بـ {last4}",
    "receipt_amount": "المبلغ: {amount} دينار",
    "receipt_fee": "العمولة: {fee} دينار",
    "receipt_no_fee": "بدون عمولة",
    "receipt_total": "المجموع: {total} دينار",
    "receipt_balance": "الرصيد بعد الدفع: {balance} دينار",
    "receipt_reference": "رقم العملية: {reference}",
    "receipt_time": "الوقت: {time} بتوقيت بغداد",
    # ---- the manager (optional) ----

    "ask_pin": {
        "f": "تمام. هسه اكتبي رمزج السري برسالة هنا حتى تندفع. ما نطلبه إلا بعد ما تدوسين «إي، ادفع». نسيتيه؟ اكتبي «نسيت الرمز».",
        "m": "تمام. هسه اكتب رمزك السري برسالة هنا حتى تندفع. ما نطلبه إلا بعد ما تدوس «إي، ادفع». نسيته؟ اكتب «نسيت الرمز».",
    },
    "pin_still_open": {
        "f": "البطاقة بعدها مفتوحة: اكتبي رمزج السري هسه برسالة.",
        "m": "البطاقة بعدها مفتوحة: اكتب رمزك السري هسه برسالة.",
    },
    "pin_wait": {
        "f": "على راحتج، البطاقة بعدها مفتوحة. من تكونين جاهزة اكتبي رمزج السري برسالة.",
        "m": "على راحتك، البطاقة بعدها مفتوحة. من تكون جاهز اكتب رمزك السري برسالة.",
    },
    "wrong_pin": {
        "f": "الرمز السري غلط، ما انخصم منج شي. اكتبيه مرة ثانية. بعد 5 مرات غلط ورا بعض ينقفل ربع ساعة.",
        "m": "الرمز السري غلط، ما انخصم منك شي. اكتبه مرة ثانية. بعد 5 مرات غلط ورا بعض ينقفل ربع ساعة.",
    },
    "tap_first": {
        "f": "دوسي «إي، ادفع» بالبطاقة أول، وبعدين اكتبي رمزج السري. ما انخصم منج شي. تريدين تغيرين المبلغ؟ دوسي «غيّر».",
        "m": "دوس «إي، ادفع» بالبطاقة أول، وبعدين اكتب رمزك السري. ما انخصم منك شي. تريد تغير المبلغ؟ دوس «غيّر».",
    },
    "pin_no_card": {
        "f": "الرمز ما نحتاجه هسه، ماكو بطاقة دفع مفتوحة. اطلبي الدفعة أول، وبعد «إي، ادفع» اكتبي رمزج. ولا تكتبين رمزج إلا بهالخطوة.",
        "m": "الرمز ما نحتاجه هسه، ماكو بطاقة دفع مفتوحة. اطلب الدفعة أول، وبعد «إي، ادفع» اكتب رمزك. ولا تكتب رمزك إلا بهالخطوة.",
    },
    "resting": {
        "f": "الخدمة مرتاحة لباجر، خلصت حصة اليوم. ارجعيلي باجر. ما انخصم منج شي.",
        "m": "الخدمة مرتاحة لباجر، خلصت حصة اليوم. ارجعلي باجر. ما انخصم منك شي.",
    },
    "message_lost": {
        "f": "انقطعت الخدمة لحظة وضاعت رسالتج الأخيرة قبل ما أقراها. اكتبيها مرة ثانية من فضلج.",
        "m": "انقطعت الخدمة لحظة وضاعت رسالتك الأخيرة قبل ما أقراها. اكتبها مرة ثانية من فضلك.",
    },
    "card_footer": "تنتهي {time} بتوقيت بغداد",
    "receipt_title": "وصل",
    # ---- options to tap ----
    "menu_body": {"f": "اختاري من القائمة، أو اكتبيلي طلبج بكلامج.", "m": "اختار من القائمة، أو اكتبلي طلبك بكلامك."},
    "next_body": {"f": "تحتاجين شي ثاني؟ اختاري من القائمة.", "m": "تحتاج شي ثاني؟ اختار من القائمة."},
    "choose_body": {"f": "اختاري جوابج من هنا:", "m": "اختار جوابك من هنا:"},
    # under a card: its three buttons are all a message may hold, so «شلون تأكدت؟» comes as one more, right below
    "card_why_body": {"f": "تريدين تعرفين شنو فحصت قبل البطاقة؟", "m": "تريد تعرف شنو فحصت قبل البطاقة؟"},
    "card_why_title": "هذا اللي تأكدت منه قبل هاي البطاقة:",
    "card_why_closed": {"f": "هاي البطاقة مو مفتوحة بعد، فما أشرح فحصها. إذا تريدين تعرفين شنو فحصت بآخر طلب اكتبي «ليش».",
                        "m": "هاي البطاقة مو مفتوحة بعد، فما أشرح فحصها. إذا تريد تعرف شنو فحصت بآخر طلب اكتب «ليش»."},
    # ---- after a PIN payment: WhatsApp gives no way to delete a message for someone, so the person is asked ----
    "delete_pin": {"f": "امسحي رسالة الرمز من المحادثة بـ«حذف لدى الجميع».",
                   "m": "امسح رسالة الرمز من المحادثة بـ«حذف لدى الجميع»."},
    # ---- a voice note while the PIN is asked for: never heard, never paid ----
    "pin_by_voice": {
        "f": "لا تگولين الرمز بصوت، اكتبيه برسالة. البطاقة بعدها مفتوحة وما انخصم منج شي.",
        "m": "لا تگول الرمز بصوت، اكتبه برسالة. البطاقة بعدها مفتوحة وما انخصم منك شي.",
    },
    # ---- voice approval (VOICE_APPROVAL): a voice note that matched the owner's kept voice ----
    "voice_matched": {"f": "صوتج تطابق ✅ دوسي «إي، ادفع» وتندفع بلا رمز.",
                      "m": "صوتك تطابق ✅ دوس «إي، ادفع» وتندفع بلا رمز."},
    "voice_fell_back": {"f": "ما گدرت أوافق بصوتج هالمرة، فنحتاج الرمز.",
                        "m": "ما گدرت أوافق بصوتك هالمرة، فنحتاج الرمز."},
    "voice_enrolled": {"f": "حفظنا صوتج: بعد هسه رسالتج الصوتية تگدر توافق على دفعة لحد {cap} دينار بلا رمز. "
                            "حتى تمسحيه اكتبي «امسح صوتي».",
                       "m": "حفظنا صوتك: بعد هسه رسالتك الصوتية تگدر توافق على دفعة لحد {cap} دينار بلا رمز. "
                            "حتى تمسحه اكتب «امسح صوتي»."},
    "voice_deleted": {"f": "مسحنا صوتج ✅ بعد هسه كل دفعة تحتاج رمزج السري.",
                      "m": "مسحنا صوتك ✅ بعد هسه كل دفعة تحتاج رمزك السري."},
    "voice_none": {"f": "ما عندنا صوت محفوظ إلج، وكل دفعة تحتاج رمزج السري.",
                   "m": "ما عندنا صوت محفوظ إلك، وكل دفعة تحتاج رمزك السري."},
    "manager_new_sender": "رقم جديد راسل وكيل الدفع: {who}",
    "manager_budget": "الخدمة وقفت لباجر: مصروف اليوم وصل {spent}$ من {budget}$.",
}


BY_LANG: dict[str, dict] = {"iq": TEXTS, "msa": TEXTS_MSA, "en": TEXTS_EN}
MANAGER_KEYS = ("manager_new_sender", "manager_budget")  # Karar's lines: Iraqi only


def say(key: str, gender: str = "f", lang: str | None = None, **slots) -> str:
    """Fill a channel text in `lang` ('iq' default | 'msa' | 'en'). Integers get thousands separators."""
    template = BY_LANG.get(lang or "iq", TEXTS).get(key, TEXTS[key])
    if isinstance(template, dict):
        template = template["m" if gender == "m" else "f"]
    values = {k: (format_iqd(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in slots.items()}
    return template.format(**values)


def hello(hour: int, lang: str | None = None) -> str:
    """«صباح الخير» from 5 until noon (Baghdad), «مساء الخير» otherwise; Good morning / Good evening in English."""
    morning, evening = HELLO.get(lang or "iq", HELLO["iq"])
    return morning if 5 <= hour < 12 else evening  # just after midnight is still the evening


SALAM_REPLY = {"iq": "وعليكم السلام، ", "msa": "وعليكم السلام، "}


def answer_salam(text: str, greeting: str, lang: str | None = None) -> str:
    """`greeting`, with «وعليكم السلام، » in front when the user said السلام عليكم (Arabic replies only). We never
    open with السلام عليكم ourselves; we always return it."""
    reply = SALAM_REPLY.get(lang or "iq")
    folded = (text or "").replace("أ", "ا").replace("إ", "ا")
    if reply and "السلام" in folded and "عليكم السلام" not in folded and "عليكم السلام" not in greeting:
        return reply + greeting
    return greeting


def all_texts(lang: str | None = None) -> list[str]:
    """Every string of one language, or of all three (the tests check the Persian letter and length)."""
    out: list[str] = []
    for table in [BY_LANG[lang]] if lang else BY_LANG.values():
        for value in table.values():
            out += list(value.values()) if isinstance(value, dict) else [value]
    return out

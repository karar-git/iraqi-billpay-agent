"""The orchestrator's messages for real accounts (transfers to any number, holds, account states), in
Baghdadi Iraqi Arabic, Modern Standard Arabic and English. Merged into payments.messages' catalogs, so
render() finds them like every other key. ج, never the Persian letter.
"""

from __future__ import annotations

ACCOUNT_MESSAGES: dict[str, dict[str, str | dict[str, str]]] = {
    "iq": {
        "transfer_to_self": {"f": "هذا رقمج إنتِ 🙂 ما انخصم منج شي. لمنو تريدين تدزين؟",
                             "m": "هذا رقمك إنت 🙂 ما انخصم منك شي. لمنو تريد تدز؟"},
        "recipient_closed": {"f": "هذا الرقم ما يگدر يستلم تحويلات هسه. ما انخصم منج شي.",
                             "m": "هذا الرقم ما يگدر يستلم تحويلات هسه. ما انخصم منك شي."},
        "invalid_phone": {"f": "هذا مو رقم موبايل عراقي. ما انخصم منج شي. اكتبيه مثل 07701234567.",
                          "m": "هذا مو رقم موبايل عراقي. ما انخصم منك شي. اكتبه مثل 07701234567."},
        "hold_limit": {"f": "عندج هسه {limit} تحويلات محجوزة لأرقام ما عندها حساب. ما انخصم منج شي. "
                            "انتظري لحد ما يفتحون حساب أو ترجع الفلوس.",
                       "m": "عندك هسه {limit} تحويلات محجوزة لأرقام ما عندها حساب. ما انخصم منك شي. "
                            "انتظر لحد ما يفتحون حساب أو ترجع الفلوس."},
        "hold_cap": {"f": "هذا الرقم محجوزله هواية فلوس لحد ما يفتح حساب (الحد {cap} دينار). ما انخصم منج شي.",
                     "m": "هذا الرقم محجوزله هواية فلوس لحد ما يفتح حساب (الحد {cap} دينار). ما انخصم منك شي."},
        "bill_changed": {"f": "مبلغ هاي الفاتورة تغيّر بعد ما سوينا البطاقة. ما انخصم منج شي. "
                              "اطلبيها مرة ثانية حتى تشوفين المبلغ الجديد.",
                         "m": "مبلغ هاي الفاتورة تغيّر بعد ما سوينا البطاقة. ما انخصم منك شي. "
                              "اطلبها مرة ثانية حتى تشوف المبلغ الجديد."},
        "account_frozen": {"f": "حسابج موقوف مؤقتاً، تگدرين تشوفين رصيدج بس ما تگدرين تدفعين. "
                                "تواصلي ويانا حتى نفتحه. ما انخصم منج شي.",
                           "m": "حسابك موقوف مؤقتاً، تگدر تشوف رصيدك بس ما تگدر تدفع. "
                                "تواصل ويانا حتى نفتحه. ما انخصم منك شي."},
        "pin_blocked": {"f": "الرمز السري مقفول بعد هواية محاولات غلط. طلبنا من الإدارة تتواصل وياج. ما انخصم منج شي.",
                        "m": "الرمز السري مقفول بعد هواية محاولات غلط. طلبنا من الإدارة تتواصل وياك. ما انخصم منك شي."},
        "pin_not_set": {"f": "لازم تختارين رمز سري أول حتى تگدرين تدفعين. ما انخصم منج شي.",
                        "m": "لازم تختار رمز سري أول حتى تگدر تدفع. ما انخصم منك شي."},
        "pin_reset_cooldown": {"f": "بعد تصفير الرمز، أول 24 ساعة تگدرين تدزين لحد {limit} دينار بس "
                                    "(باقي إلج {remaining}). ما انخصم منج شي.",
                               "m": "بعد تصفير الرمز، أول 24 ساعة تگدر تدز لحد {limit} دينار بس "
                                    "(باقي إلك {remaining}). ما انخصم منك شي."},
        "flag_unregistered_recipient": "هذا الرقم ما عنده حساب بعد: المبلغ ينحجز إله 7 أيام. إذا فتح حساب يوصله، "
                                       "وإذا لا يرجعلك كامل ويا العمولة.",
        "done_hold": {"f": "تم. انحجز {amount} دينار للرقم المنتهي بـ {last4} لحد ما يفتح حساب (7 أيام). "
                           "رصيدج هسه {balance} دينار.",
                      "m": "تم. انحجز {amount} دينار للرقم المنتهي بـ {last4} لحد ما يفتح حساب (7 أيام). "
                           "رصيدك هسه {balance} دينار."},
        "hold_share": {"f": "گوليله يراسل هذا الرقم حتى يفتح حساب ويستلمها: {link}",
                       "m": "گله يراسل هذا الرقم حتى يفتح حساب ويستلمها: {link}"},
        "auto_saved": {"f": "حفظت الرقم بأسمائج باسم «{name}». تريدين تغيّرينه؟ اكتبي «سمّي {last4} اسم جديد».",
                       "m": "حفظت الرقم بأسمائك باسم «{name}». تريد تغيّره؟ اكتب «سمّي {last4} اسم جديد»."},
        "unknown_payee_or_number": {"f": "ما لگيت {name} بالأسماء المحفوظة عندج. ما انخصم منج شي. "
                                         "اكتبي الاسم مثل ما هو محفوظ، أو اكتبي رقم موبايله.",
                                    "m": "ما لگيت {name} بالأسماء المحفوظة عندك. ما انخصم منك شي. "
                                         "اكتب الاسم مثل ما هو محفوظ، أو اكتب رقم موبايله."},
        "pilot_bill": "ملاحظة: الخدمة بمرحلة تجريبية، الدفعة تبقى داخل الخدمة ولسه ما توصل للجهة.",
    },
    "msa": {
        "transfer_to_self": {"f": "هذا رقمكِ أنتِ 🙂 لم يُخصم منك شيء. إلى من تريدين التحويل؟",
                             "m": "هذا رقمك أنت 🙂 لم يُخصم منك شيء. إلى من تريد التحويل؟"},
        "recipient_closed": "هذا الرقم لا يستطيع استلام التحويلات الآن. لم يُخصم منك شيء.",
        "invalid_phone": {"f": "هذا ليس رقم هاتف محمول عراقياً. لم يُخصم منك شيء. اكتبيه مثل 07701234567.",
                          "m": "هذا ليس رقم هاتف محمول عراقياً. لم يُخصم منك شيء. اكتبه مثل 07701234567."},
        "hold_limit": "لديك الآن {limit} تحويلات محجوزة لأرقام بلا حساب. لم يُخصم منك شيء. "
                      "انتظر حتى يفتحوا حسابات أو تعود الأموال.",
        "hold_cap": "هذا الرقم محجوز له مبلغ كبير قبل أن يفتح حساباً (الحد {cap} دينار). لم يُخصم منك شيء.",
        "bill_changed": "تغيّر مبلغ هذه الفاتورة بعد إعداد البطاقة. لم يُخصم منك شيء. اطلبها مرة أخرى لترى المبلغ الجديد.",
        "account_frozen": "حسابك موقوف مؤقتاً: يمكنك رؤية رصيدك لكن لا يمكنك الدفع. تواصل معنا لإعادة فتحه. "
                          "لم يُخصم منك شيء.",
        "pin_blocked": "الرمز السري مقفل بعد محاولات خاطئة كثيرة. طلبنا من الإدارة التواصل معك. لم يُخصم منك شيء.",
        "pin_not_set": {"f": "يجب أن تختاري رمزاً سرياً أولاً لتتمكني من الدفع. لم يُخصم منك شيء.",
                        "m": "يجب أن تختار رمزاً سرياً أولاً لتتمكن من الدفع. لم يُخصم منك شيء."},
        "pin_reset_cooldown": "بعد تصفير الرمز، يمكنك خلال أول 24 ساعة تحويل {limit} دينار فقط (المتبقي {remaining}). "
                              "لم يُخصم منك شيء.",
        "flag_unregistered_recipient": "لا يوجد حساب على هذا الرقم بعد: يُحجز المبلغ له 7 أيام. إن فتح حساباً وصله، "
                                       "وإلا عاد إليك كاملاً مع العمولة.",
        "done_hold": "تم. حُجز {amount} دينار للرقم المنتهي بـ {last4} حتى يفتح حساباً (7 أيام). "
                     "رصيدك الآن {balance} دينار.",
        "hold_share": {"f": "اطلبي منه مراسلة هذا الرقم ليفتح حساباً ويستلمها: {link}",
                       "m": "اطلب منه مراسلة هذا الرقم ليفتح حساباً ويستلمها: {link}"},
        "auto_saved": "حفظت الرقم في جهات اتصالك باسم «{name}». لتغييره اكتب «سمّ {last4} اسم جديد».",
        "unknown_payee_or_number": {"f": "لم أجد {name} في جهات الاتصال المحفوظة. لم يُخصم منك شيء. "
                                         "اكتبي الاسم كما هو محفوظ، أو اكتبي رقم هاتفه.",
                                    "m": "لم أجد {name} في جهات الاتصال المحفوظة. لم يُخصم منك شيء. "
                                         "اكتب الاسم كما هو محفوظ، أو اكتب رقم هاتفه."},
        "pilot_bill": "ملاحظة: الخدمة في مرحلة تجريبية؛ تبقى الدفعة داخل الخدمة ولا تصل إلى الجهة بعد.",
    },
    "en": {
        "transfer_to_self": "That's your own number 🙂 Nothing was taken. Who do you want to send to?",
        "recipient_closed": "That number can't receive transfers right now. Nothing was taken.",
        "invalid_phone": "That isn't an Iraqi mobile number. Nothing was taken. Write it like 07701234567.",
        "hold_limit": "You already have {limit} transfers held for numbers with no account. Nothing was taken. "
                      "Wait until they sign up or the money comes back.",
        "hold_cap": "A lot is already held for that number until it opens an account (limit {cap} dinars). "
                    "Nothing was taken.",
        "bill_changed": "This bill's amount changed after the card was made. Nothing was taken. "
                        "Ask for it again to see the new amount.",
        "account_frozen": "Your account is paused: you can see your balance but can't pay. Contact us to reopen "
                          "it. Nothing was taken.",
        "pin_blocked": "Your PIN is blocked after too many wrong tries. We've asked the admin to contact you. "
                       "Nothing was taken.",
        "pin_not_set": "You need to choose a PIN before you can pay. Nothing was taken.",
        "pin_reset_cooldown": "For 24 hours after a PIN reset you can send up to {limit} dinars "
                              "({remaining} left). Nothing was taken.",
        "flag_unregistered_recipient": "That number has no account yet: the money is held for it for 7 days. "
                                       "If they sign up it reaches them; if not, it all comes back to you, fee included.",
        "done_hold": "Done. {amount} dinars are held for the number ending in {last4} until it opens an account "
                     "(7 days). Your balance is now {balance} dinars.",
        "hold_share": "Tell them to message this number to open an account and receive it: {link}",
        "auto_saved": "I saved the number in your contacts as “{name}”. To rename it, write “name {last4} new name”.",
        "unknown_payee_or_number": "I couldn't find {name} in your saved contacts. Nothing was taken. "
                                   "Write the name as it's saved, or write their mobile number.",
        "pilot_bill": "Note: the service is in a pilot. The payment stays inside the service and doesn't reach "
                      "the biller yet.",
    },
}

"""Words of real accounts: sign-up, the account commands (contacts, bill accounts, history, PIN) and the
notices a user gets (money received, holds, admin actions). Baghdadi Iraqi Arabic, Modern Standard Arabic
and English, the same keys in all three. Each entry is one text or {"f": ..., "m": ...} (Arabic follows the
reader's gender; before we know it, the masculine, as Iraqi Arabic does for someone unknown).
ج, never the Persian letter.
"""

from __future__ import annotations

from kyc.texts import ACCEPTED_EN, ACCEPTED_IQ, ACCEPTED_MSA
from payments.messages import format_iqd

T = str | dict[str, str]

IQ: dict[str, T] = {
    # ---- sign-up ----
    "offer": "{hello}! أني وكيل الدفع الذكي: أدفع الفواتير وأحوّل فلوس لأي رقم بجملة وحدة، كتابة أو صوت.\n"
             "هذا الرقم ما عليه حساب بعد. نفتح حساب؟ ياخذ دقيقة.",
    "pilot": "للعلم: الفلوس هنا رصيد داخل الخدمة بس، ودفع الفواتير لسه ما يوصل للجهات.",
    "name_confirm": "أسجلك باسم «{name}»؟ هذا الاسم يشوفه اللي تحوّلهم.",
    "ask_name": "شنو اسمك الكامل؟ اكتبه مثل ما تريده يطلع للناس.",
    "bad_name": "الاسم لازم يكون حروف بس (من 2 لـ 40 حرف)، بلا أرقام. اكتبه مرة ثانية.",
    "ask_gender": "حتى أحجي وياك صح: أخاطبك…",
    "ask_pin": {"f": "هسه اختاري رمز سري من 4 إلى 6 أرقام واكتبيه برسالة. بيه تأكدين كل دفعة. لا تنطينه لأحد، "
                     "وإحنا ما نطلبه إلا بعد ما تدوسين «إي، ادفع».",
                "m": "هسه اختار رمز سري من 4 إلى 6 أرقام واكتبه برسالة. بيه تأكد كل دفعة. لا تنطيه لأحد، "
                     "وإحنا ما نطلبه إلا بعد ما تدوس «إي، ادفع»."},
    "pin_weak": {"f": "هذا الرمز سهل ينحزر (مثل 1234 أو 0000 أو آخر أرقام رقمج). اختاري غيره.",
                 "m": "هذا الرمز سهل ينحزر (مثل 1234 أو 0000 أو آخر أرقام رقمك). اختار غيره."},
    "pin_not_digits": {"f": "الرمز لازم يكون من 4 إلى 6 أرقام بس. اكتبيه برسالة.",
                       "m": "الرمز لازم يكون من 4 إلى 6 أرقام بس. اكتبه برسالة."},
    "pin_again": {"f": "اكتبيه مرة ثانية للتأكيد.", "m": "اكتبه مرة ثانية للتأكيد."},
    "pin_mismatch": {"f": "الرمزين مو نفس الشي. نرجع: اكتبي رمز جديد.", "m": "الرمزين مو نفس الشي. نرجع: اكتب رمز جديد."},
    "pin_restart": {"f": "خلي نبدي الرمز من جديد. اختاري رمز من 4 إلى 6 أرقام.",
                    "m": "خلي نبدي الرمز من جديد. اختار رمز من 4 إلى 6 أرقام."},
    "done": {"f": "تم فتح حسابج ✅ رقم حسابج هو رقم واتسابج {number}. رصيدج {balance} دينار.\n"
                  "جربي: «ضيف حساب المولدة 350» أو «دز عشرة آلاف لـ 07xx…». الأوامر: «مساعدة».\n"
                  "حتى تحطين فلوس بحسابج اكتبي «اشحن محفظتي».",
             "m": "تم فتح حسابك ✅ رقم حسابك هو رقم واتسابك {number}. رصيدك {balance} دينار.\n"
                  "جرب: «ضيف حساب المولدة 350» أو «دز عشرة آلاف لـ 07xx…». الأوامر: «مساعدة».\n"
                  "حتى تحط فلوس بحسابك اكتب «اشحن محفظتي»."},
    "claimed_intro": {"f": "وصلتج تحويلات كانت محجوزة لرقمج وانضافت لرصيدج:",
                      "m": "وصلتك تحويلات كانت محجوزة لرقمك وانضافت لرصيدك:"},
    "claimed_line": "• {amount} دينار من {name} (…{last4})",
    "not_now": {"f": "تدللين. من تريدين، اكتبي «افتح حساب».", "m": "تدلل. من تريد، اكتب «افتح حساب»."},
    "finish_first": {"f": "خلي نكمل فتح الحساب أول.", "m": "خلي نكمل فتح الحساب أول."},
    "signup_cancelled": {"f": "لغيت فتح الحساب، ما انحفظ شي. من تريدين، اكتبي «افتح حساب».",
                         "m": "لغيت فتح الحساب، ما انحفظ شي. من تريد، اكتب «افتح حساب»."},
    "signup_needs_number": "حتى نفتح حساب نحتاج رقم موبايل: الحساب هو رقم واتسابك. راسلنا من حساب واتساب عليه رقم.",
    "signup_invite_only": "فتح الحسابات هسه بالدعوة بس. بلغنا الإدارة بطلبك وراح يتواصلون وياك.",
    "signup_closed": "فتح حسابات جديدة متوقف هسه. جرب بعدين.",
    "account_closed": "هذا الحساب مسدود. تواصل ويانا إذا تحتاج شي.",
    "voice_in_signup": {"f": "بهالخطوة اكتبيها كتابة رجاءً (الرمز السري ما ينگال بالصوت أبداً).",
                        "m": "بهالخطوة اكتبها كتابة رجاءً (الرمز السري ما ينگال بالصوت أبداً)."},
    "invited_hello": {"f": "{hello} {first}! فتحنالج حساب على هذا الرقم ورصيدج {balance} دينار. بس تحتاجين تختارين "
                           "رمز سري حتى تگدرين تدفعين.",
                      "m": "{hello} {first}! فتحنالك حساب على هذا الرقم ورصيدك {balance} دينار. بس تحتاج تختار "
                           "رمز سري حتى تگدر تدفع."},
    "pin_reset_hello": {"f": "{hello} {first}! صفّرنا رمزج السري. اختاري رمز جديد من 4 إلى 6 أرقام واكتبيه برسالة.",
                        "m": "{hello} {first}! صفّرنا رمزك السري. اختار رمز جديد من 4 إلى 6 أرقام واكتبه برسالة."},
    "pin_set_done": {"f": "تم ✅ انحفظ رمزج الجديد. رصيدج {balance} دينار.",
                     "m": "تم ✅ انحفظ رمزك الجديد. رصيدك {balance} دينار."},
    "btn_open": "افتح حساب", "btn_later": "مو هسه", "btn_yes": "إي", "btn_other_name": "اسم ثاني",
    "btn_male": "رجّال", "btn_female": "امرأة", "btn_save": "احفظه", "btn_send": "حوّل له", "btn_no": "لا",
    "btn_rename": "إي، غيّره", "btn_keep": "خليه", "btn_request": "إي، اطلب", "btn_delete": "إي، امسحه",
    "btn_confirm_save": "احفظ",
    # ---- returning users ----
    "welcome_back": {"f": "{hello} {first}! رصيدج {balance} دينار. شنو تحتاجين؟",
                     "m": "{hello} {first}! رصيدك {balance} دينار. شنو تحتاج؟"},
    "help": {"f": "أگدر أدفع فواتيرج وأحوّل فلوس لأي رقم عراقي وأشحن رصيد، بجملة وحدة كتابة أو صوت:\n"
                  "• «دز 25 الف لـ 0770 123 4567» أو «دز لأحمد خمسين»\n"
                  "• «ضيف أحمد 07701234567» · «أسمائي»\n"
                  "• «ضيف حساب المولدة 350» · «ادفع المولدة» · «فواتيري»\n"
                  "• «اشحن رصيدي بعشرة» · «رصيدي» · «آخر العمليات» · «حسابي»\n"
                  "• «غيّر الرمز» · «نسيت الرمز»\n"
                  "قبل أي دفعة تطلعلج بطاقة: دوسي «إي، ادفع» وبعدين اكتبي رمزج السري.",
             "m": "أگدر أدفع فواتيرك وأحوّل فلوس لأي رقم عراقي وأشحن رصيد، بجملة وحدة كتابة أو صوت:\n"
                  "• «دز 25 الف لـ 0770 123 4567» أو «دز لأحمد خمسين»\n"
                  "• «ضيف أحمد 07701234567» · «أسمائي»\n"
                  "• «ضيف حساب المولدة 350» · «ادفع المولدة» · «فواتيري»\n"
                  "• «اشحن رصيدي بعشرة» · «رصيدي» · «آخر العمليات» · «حسابي»\n"
                  "• «غيّر الرمز» · «نسيت الرمز»\n"
                  "قبل أي دفعة تطلعلك بطاقة: دوس «إي، ادفع» وبعدين اكتب رمزك السري."},
    "pin_is_secret": {"f": "الرمز السري محفوظ مشفّر، ما نگدر نشوفه ولا نرجعه. نسيتيه؟ اكتبي «نسيت الرمز».",
                      "m": "الرمز السري محفوظ مشفّر، ما نگدر نشوفه ولا نرجعه. نسيته؟ اكتب «نسيت الرمز»."},
    # ---- contacts ----
    "contact_saved": "حفظت {name} ({number}).",
    "contact_has_account": "عنده حساب بالخدمة: التحويل إله يوصل فوراً.",
    "contact_same": "{name} محفوظ من قبل بهالرقم.",
    "contact_other": "هذا الرقم محفوظ من قبل باسم «{old}». أغيّره لـ «{new}»؟",
    "contact_self": "هذا رقمك إنت 🙂",
    "contact_limit": "وصلت حد الأسماء المحفوظة ({limit}). امسح اسم قديم أول.",
    "contact_bad_number": {"f": "هذا مو رقم موبايل عراقي. اكتبيه مثل 07701234567.",
                           "m": "هذا مو رقم موبايل عراقي. اكتبه مثل 07701234567."},
    "contact_ask_name": {"f": "شنو الاسم اللي تحفظين بيه {number}؟", "m": "شنو الاسم اللي تحفظ بيه {number}؟"},
    "contact_renamed": "تم، صار اسمه «{name}».",
    "contact_deleted": "مسحت {name} من أسمائك.",
    "contact_confirm_delete": {"f": "أمسح {name} (…{last4}) من أسمائج؟", "m": "أمسح {name} (…{last4}) من أسمائك؟"},
    "contact_not_found": {"f": "ما لگيت هالاسم بأسمائج. اكتبي «أسمائي» حتى تشوفين القائمة.",
                          "m": "ما لگيت هالاسم بأسمائك. اكتب «أسمائي» حتى تشوف القائمة."},
    "contact_which": {"f": "أي واحد تقصدين؟ {options}", "m": "أي واحد تقصد؟ {options}"},
    "contacts_title": {"f": "أسمائج المحفوظة ({count}):", "m": "أسمائك المحفوظة ({count}):"},
    "contacts_empty": {"f": "ما عندج أسماء محفوظة بعد. ضيفي واحد: «ضيف أحمد 07701234567».",
                       "m": "ما عندك أسماء محفوظة بعد. ضيف واحد: «ضيف أحمد 07701234567»."},
    "contacts_more": {"f": "واكو {more} بعد. اكتبي «الباقي».", "m": "واكو {more} بعد. اكتب «الباقي»."},
    "number_only": {"f": "هذا الرقم ({number}) تريدين تحفظينه لو تحوّليله فلوس؟",
                    "m": "هذا الرقم ({number}) تريد تحفظه لو تحوّله فلوس؟"},
    "send_to_number": {"f": "شكد تريدين تدزين لـ {number}؟", "m": "شكد تريد تدز لـ {number}؟"},
    # ---- bill accounts ----
    "bill_kind": {"f": "يا حساب تريدين تضيفين؟ {options}", "m": "يا حساب تريد تضيف؟ {options}"},
    "bill_which_biller": {"f": "يا شركة؟ {options}", "m": "يا شركة؟ {options}"},
    "bill_ask_number": {"f": "شنو رقم حساب {biller}؟", "m": "شنو رقم حساب {biller}؟"},
    "bill_bad_number": {"f": "رقم الحساب مو صحيح لـ {biller}. اكتبيه مثل ما مكتوب بالوصل.",
                        "m": "رقم الحساب مو صحيح لـ {biller}. اكتبه مثل ما مكتوب بالوصل."},
    "bill_confirm": "أحفظ حساب {biller} رقم {account}؟",
    "bill_saved": {"f": "انضاف ✅ عليه فاتورة {month}: {amount} دينار. تريدين تدفعيها؟ اكتبي «ادفع {label}».",
                   "m": "انضاف ✅ عليه فاتورة {month}: {amount} دينار. تريد تدفعها؟ اكتب «ادفع {label}»."},
    "bill_exists": {"f": "هذا الحساب محفوظ عندج من قبل.", "m": "هذا الحساب محفوظ عندك من قبل."},
    "bill_limit": {"f": "وصلتي حد الحسابات ({limit}). امسحي حساب قديم أول.",
                   "m": "وصلت حد الحسابات ({limit}). امسح حساب قديم أول."},
    "bills_title": {"f": "حساباتج:", "m": "حساباتك:"},
    "bills_line": "• {label} ({biller}) رقم {account}{due}",
    "bills_due": " · عليه {amount} دينار ({month})",
    "bills_paid": " · ماكو فاتورة غير مدفوعة",
    "bills_empty": {"f": "ما عندج حسابات فواتير محفوظة. ضيفي واحد: «ضيف حساب المولدة 350».",
                    "m": "ما عندك حسابات فواتير محفوظة. ضيف واحد: «ضيف حساب المولدة 350»."},
    "bill_deleted": "مسحت حساب {label}.",
    "kinds": {"electricity": "كهرباء", "generator": "مولدة", "internet": "نت", "water": "ماء"},
    # ---- history / my account ----
    "history_title": "آخر العمليات:",
    "history_empty": "ماكو عمليات بعد.",
    "h_transfer_out": "{when} · دزيت لـ {name} −{amount}",
    "h_transfer_held": "{when} · محجوز لـ {name} −{amount}",
    "h_transfer_in": {"f": "{when} · وصلج من {name} +{amount}", "m": "{when} · وصلك من {name} +{amount}"},
    "h_bill": "{when} · فاتورة {biller} −{amount}",
    "h_topup": "{when} · شحن رصيد …{last4} −{amount}",
    "h_admin_credit": "{when} · إيداع من الإدارة +{amount}",
    "h_admin_debit": "{when} · سحب من الإدارة −{amount}",
    "h_signup_credit": "{when} · هدية فتح الحساب +{amount}",
    "h_hold_refund": "{when} · رجعة تحويل محجوز +{amount}",
    "h_reversed": " (رجعت)",
    "history_today": {"f": "اليوم صرفتي {spent} دينار من حدج اليومي {limit} (الحد يحسب المبالغ بلا العمولة).",
                      "m": "اليوم صرفت {spent} دينار من حدك اليومي {limit} (الحد يحسب المبالغ بلا العمولة)."},
    "my_account": {"f": "حسابج: {name}\nالرقم: {number}\nالحالة: {status}\nالرصيد: {balance} دينار\n"
                        "الحد اليومي: {limit} (باقي اليوم {left})",
                   "m": "حسابك: {name}\nالرقم: {number}\nالحالة: {status}\nالرصيد: {balance} دينار\n"
                        "الحد اليومي: {limit} (باقي اليوم {left})"},
    "my_holds": "محجوز لأرقام بلا حساب: {count} ({amount} دينار)",
    "status_active": "شغّال", "status_frozen": "موقوف مؤقتاً", "status_invited": "مدعو", "status_closed": "مسدود",
    # ---- PIN ----
    "change_current": {"f": "اكتبي رمزج الحالي.", "m": "اكتب رمزك الحالي."},
    "change_new": {"f": "تمام. هسه اكتبي الرمز الجديد (4 إلى 6 أرقام).", "m": "تمام. هسه اكتب الرمز الجديد (4 إلى 6 أرقام)."},
    "change_done": "تغيّر الرمز ✅",
    "change_wrong": {"f": "الرمز الحالي غلط. ما تغيّر شي. اكتبيه مرة ثانية أو «الغاء».",
                     "m": "الرمز الحالي غلط. ما تغيّر شي. اكتبه مرة ثانية أو «الغاء»."},
    "change_locked": "الرمز انقفل ربع ساعة بعد 5 محاولات غلط. ما تغيّر شي.",
    "forgot": {"f": "الرمز محفوظ مشفّر وما نگدر نرجعه. نطلب من الإدارة تصفّره؟ راح يتواصلون وياج قبل.",
               "m": "الرمز محفوظ مشفّر وما نگدر نرجعه. نطلب من الإدارة تصفّره؟ راح يتواصلون وياك قبل."},
    "forgot_sent": {"f": "انطلب ✅ الإدارة راح تتواصل وياج. لا تنطين رمزج لأحد.",
                    "m": "انطلب ✅ الإدارة راح تتواصل وياك. لا تنطي رمزك لأحد."},
    "fund_how": {"f": "الفلوس تنزل بحسابج هسه عن طريق الإدارة، أو أحد يدزلج على رقمج من حسابه هنا. "
                      "نطلب من الإدارة تتواصل وياج حتى تنزّل رصيد بحسابج؟",
                 "m": "الفلوس تنزل بحسابك هسه عن طريق الإدارة، أو أحد يدزلك على رقمك من حسابه هنا. "
                      "نطلب من الإدارة تتواصل وياك حتى تنزّل رصيد بحسابك؟"},
    "fund_sent": {"f": "انطلب ✅ الإدارة راح تتواصل وياج، وتوصلج رسالة أول ما ينزل الرصيد بحسابج.",
                  "m": "انطلب ✅ الإدارة راح تتواصل وياك، وتوصلك رسالة أول ما ينزل الرصيد بحسابك."},
    "rename_me_ask": {"f": "شنو الاسم الجديد؟", "m": "شنو الاسم الجديد؟"},
    "rename_me_done": {"f": "تم، صار اسمج «{name}».", "m": "تم، صار اسمك «{name}»."},
    "manage_cancelled": "تمام، ما غيّرت شي.",
    # ---- notices ----
    "n_transfer_in": {"f": "وصلج {amount} دينار من {name} (…{last4}).", "m": "وصلك {amount} دينار من {name} (…{last4})."},
    "n_hold_claimed": {"f": "صاحب الرقم …{last4} ({name}) فتح حساب واستلم {amount} دينار اللي دزّيتيها.",
                       "m": "صاحب الرقم …{last4} ({name}) فتح حساب واستلم {amount} دينار اللي دزّيتها."},
    "n_hold_refunded": "رجعتلك {amount} دينار: الرقم …{last4} ما فتح حساب خلال 7 أيام.",
    "n_hold_cancelled": "رجعتلك {amount} دينار من التحويل المحجوز للرقم …{last4}.",
    "n_admin_credit": "انضافلك {amount} دينار. السبب: {reason}",
    "n_admin_debit": {"f": "انسحب من رصيدج {amount} دينار. السبب: {reason}",
                      "m": "انسحب من رصيدك {amount} دينار. السبب: {reason}"},
    "n_pin_reset": {"f": "صفّرنا رمزج السري بطلب منج.", "m": "صفّرنا رمزك السري بطلب منك."},
    "n_frozen": {"f": "حسابج انوقف مؤقتاً. تگدرين تشوفين رصيدج بس ما تگدرين تدفعين. تواصلي ويانا.",
                 "m": "حسابك انوقف مؤقتاً. تگدر تشوف رصيدك بس ما تگدر تدفع. تواصل ويانا."},
    "n_unfrozen": "رجع حسابك شغّال ✅",
    "n_balance": {"f": "رصيدج هسه {balance} دينار.", "m": "رصيدك هسه {balance} دينار."},
    # ---- manager (Karar) ----
    "manager_signup": "حساب جديد: {name} ({number})",
    "manager_pin_request": "طلب تصفير رمز: {name} ({number})",
    "manager_pin_blocked": "رمز انقفل نهائياً (3 قفلات): {name} ({number})",
    "manager_invite_request": "رقم يريد يفتح حساب (الفتح بالدعوة): {number}",
}

MSA: dict[str, T] = {
    "offer": "{hello}! أنا وكيل الدفع الذكي: أدفع الفواتير وأحوّل المال إلى أي رقم بجملة واحدة، كتابةً أو صوتاً.\n"
             "لا يوجد حساب على هذا الرقم بعد. هل نفتح حساباً؟ يستغرق دقيقة.",
    "pilot": "للعلم: المال هنا رصيد داخل الخدمة فقط، ودفع الفواتير لا يصل إلى الجهات بعد.",
    "name_confirm": "هل نسجّلك باسم «{name}»؟ يظهر هذا الاسم لمن تحوّل إليهم.",
    "ask_name": "ما اسمك الكامل؟ اكتبه كما تريد أن يظهر للآخرين.",
    "bad_name": "يجب أن يتكوّن الاسم من حروف فقط (من 2 إلى 40 حرفاً) بلا أرقام. اكتبه مرة أخرى.",
    "ask_gender": "لأخاطبك بالصيغة الصحيحة:",
    "ask_pin": {"f": "الآن اختاري رمزاً سرياً من 4 إلى 6 أرقام واكتبيه في رسالة. ستؤكدين به كل دفعة. لا تعطيه لأحد، "
                     "ولن نطلبه إلا بعد أن تضغطي «نعم، ادفع».",
                "m": "الآن اختر رمزاً سرياً من 4 إلى 6 أرقام واكتبه في رسالة. ستؤكد به كل دفعة. لا تعطه لأحد، "
                     "ولن نطلبه إلا بعد أن تضغط «نعم، ادفع»."},
    "pin_weak": {"f": "هذا الرمز سهل التخمين (مثل 1234 أو 0000 أو آخر أرقام هاتفك). اختاري رمزاً آخر.",
                 "m": "هذا الرمز سهل التخمين (مثل 1234 أو 0000 أو آخر أرقام هاتفك). اختر رمزاً آخر."},
    "pin_not_digits": {"f": "يجب أن يتكوّن الرمز من 4 إلى 6 أرقام فقط. اكتبيه في رسالة.",
                       "m": "يجب أن يتكوّن الرمز من 4 إلى 6 أرقام فقط. اكتبه في رسالة."},
    "pin_again": {"f": "اكتبيه مرة أخرى للتأكيد.", "m": "اكتبه مرة أخرى للتأكيد."},
    "pin_mismatch": {"f": "الرمزان غير متطابقين. لنبدأ من جديد: اكتبي رمزاً جديداً.",
                     "m": "الرمزان غير متطابقين. لنبدأ من جديد: اكتب رمزاً جديداً."},
    "pin_restart": {"f": "لنبدأ الرمز من جديد. اختاري رمزاً من 4 إلى 6 أرقام.", "m": "لنبدأ الرمز من جديد. اختر رمزاً من 4 إلى 6 أرقام."},
    "done": {"f": "تم فتح حسابك ✅ رقم حسابك هو رقم واتسابك {number}. رصيدك {balance} دينار.\n"
                  "جرّبي «أضف حساب المولدة 350» أو «حوّل عشرة آلاف إلى 07xx…». للمساعدة: «مساعدة».\n"
                  "لإضافة مال إلى حسابك اكتبي «اشحن محفظتي».",
             "m": "تم فتح حسابك ✅ رقم حسابك هو رقم واتسابك {number}. رصيدك {balance} دينار.\n"
                  "جرّب «أضف حساب المولدة 350» أو «حوّل عشرة آلاف إلى 07xx…». للمساعدة: «مساعدة».\n"
                  "لإضافة مال إلى حسابك اكتب «اشحن محفظتي»."},
    "claimed_intro": "وصلتك تحويلات كانت محجوزة لرقمك وأضيفت إلى رصيدك:",
    "claimed_line": "• {amount} دينار من {name} (…{last4})",
    "not_now": {"f": "حسناً. متى أردتِ، اكتبي «افتح حساب».", "m": "حسناً. متى أردت، اكتب «افتح حساب»."},
    "finish_first": "لنكمل فتح الحساب أولاً.",
    "signup_cancelled": "ألغيت فتح الحساب ولم يُحفظ شيء. متى أردت، اكتب «افتح حساب».",
    "signup_needs_number": "لفتح حساب نحتاج إلى رقم هاتف: الحساب هو رقم واتسابك. راسلنا من حساب واتساب له رقم.",
    "signup_invite_only": "فتح الحسابات الآن بالدعوة فقط. أبلغنا الإدارة بطلبك وسيتواصلون معك.",
    "signup_closed": "فتح حسابات جديدة متوقف الآن. حاول لاحقاً.",
    "account_closed": "هذا الحساب مغلق. تواصل معنا إن احتجت إلى شيء.",
    "voice_in_signup": "في هذه الخطوة اكتب كتابةً من فضلك (الرمز السري لا يُقال بالصوت أبداً).",
    "invited_hello": {"f": "{hello} {first}! فتحنا لكِ حساباً على هذا الرقم ورصيدك {balance} دينار. تحتاجين فقط إلى "
                           "اختيار رمز سري لتتمكني من الدفع.",
                      "m": "{hello} {first}! فتحنا لك حساباً على هذا الرقم ورصيدك {balance} دينار. تحتاج فقط إلى "
                           "اختيار رمز سري لتتمكن من الدفع."},
    "pin_reset_hello": {"f": "{hello} {first}! صفّرنا رمزك السري. اختاري رمزاً جديداً من 4 إلى 6 أرقام واكتبيه في رسالة.",
                        "m": "{hello} {first}! صفّرنا رمزك السري. اختر رمزاً جديداً من 4 إلى 6 أرقام واكتبه في رسالة."},
    "pin_set_done": "تم ✅ حُفظ رمزك الجديد. رصيدك {balance} دينار.",
    "btn_open": "افتح حساباً", "btn_later": "ليس الآن", "btn_yes": "نعم", "btn_other_name": "اسم آخر",
    "btn_male": "ذكر", "btn_female": "أنثى", "btn_save": "احفظه", "btn_send": "حوّل إليه", "btn_no": "لا",
    "btn_rename": "نعم، غيّره", "btn_keep": "اتركه", "btn_request": "نعم، اطلب", "btn_delete": "نعم، احذفه",
    "btn_confirm_save": "احفظ",
    "welcome_back": {"f": "{hello} {first}! رصيدك {balance} دينار. كيف أساعدك؟", "m": "{hello} {first}! رصيدك {balance} دينار. كيف أساعدك؟"},
    "help": "أستطيع دفع فواتيرك وتحويل المال إلى أي رقم عراقي وشحن الرصيد، بجملة واحدة كتابةً أو صوتاً:\n"
            "• «حوّل 25 ألفاً إلى 0770 123 4567» أو «حوّل إلى أحمد خمسين ألفاً»\n"
            "• «أضف أحمد 07701234567» · «جهات الاتصال»\n"
            "• «أضف حساب المولدة 350» · «ادفع فاتورة المولدة» · «فواتيري»\n"
            "• «اشحن رصيدي بعشرة آلاف» · «كم رصيدي» · «آخر العمليات» · «حسابي»\n"
            "• «غيّر الرمز» · «نسيت الرمز»\n"
            "قبل أي دفعة تظهر بطاقة: اضغط «نعم، ادفع» ثم اكتب رمزك السري.",
    "pin_is_secret": "الرمز السري محفوظ مشفّراً، لا نستطيع رؤيته ولا استعادته. هل نسيته؟ اكتب «نسيت الرمز».",
    "contact_saved": "حفظت {name} ({number}).",
    "contact_has_account": "لديه حساب في الخدمة: يصله التحويل فوراً.",
    "contact_same": "{name} محفوظ من قبل بهذا الرقم.",
    "contact_other": "هذا الرقم محفوظ من قبل باسم «{old}». هل أغيّره إلى «{new}»؟",
    "contact_self": "هذا رقمك أنت 🙂",
    "contact_limit": "وصلت إلى حد جهات الاتصال ({limit}). احذف اسماً قديماً أولاً.",
    "contact_bad_number": "هذا ليس رقم هاتف محمول عراقياً. اكتبه مثل 07701234567.",
    "contact_ask_name": "ما الاسم الذي تحفظ به {number}؟",
    "contact_renamed": "تم، أصبح اسمه «{name}».",
    "contact_deleted": "حذفت {name} من جهات الاتصال.",
    "contact_confirm_delete": "هل أحذف {name} (…{last4}) من جهات الاتصال؟",
    "contact_not_found": "لم أجد هذا الاسم في جهات الاتصال. اكتب «جهات الاتصال» لترى القائمة.",
    "contact_which": {"f": "أيّهم تقصدين؟ {options}", "m": "أيّهم تقصد؟ {options}"},
    "contacts_title": "جهات الاتصال المحفوظة ({count}):",
    "contacts_empty": "لا توجد جهات اتصال محفوظة بعد. أضف واحدة: «أضف أحمد 07701234567».",
    "contacts_more": "وهناك {more} أخرى. اكتب «الباقي».",
    "number_only": "هل تريد حفظ هذا الرقم ({number}) أم تحويل المال إليه؟",
    "send_to_number": "كم تريد أن تحوّل إلى {number}؟",
    "bill_kind": "أي حساب تريد أن تضيف؟ {options}",
    "bill_which_biller": "أي شركة؟ {options}",
    "bill_ask_number": "ما رقم حساب {biller}؟",
    "bill_bad_number": "رقم الحساب غير صحيح لـ {biller}. اكتبه كما هو في الإيصال.",
    "bill_confirm": "هل أحفظ حساب {biller} رقم {account}؟",
    "bill_saved": "أضيف ✅ عليه فاتورة {month}: {amount} دينار. هل تريد دفعها؟ اكتب «ادفع فاتورة {label}».",
    "bill_exists": "هذا الحساب محفوظ لديك من قبل.",
    "bill_limit": "وصلت إلى حد الحسابات ({limit}). احذف حساباً قديماً أولاً.",
    "bills_title": "حساباتك:",
    "bills_line": "• {label} ({biller}) رقم {account}{due}",
    "bills_due": " · عليه {amount} دينار ({month})",
    "bills_paid": " · لا توجد فاتورة غير مدفوعة",
    "bills_empty": "لا توجد حسابات فواتير محفوظة. أضف واحداً: «أضف حساب المولدة 350».",
    "bill_deleted": "حذفت حساب {label}.",
    "kinds": {"electricity": "كهرباء", "generator": "مولدة", "internet": "إنترنت", "water": "ماء"},
    "history_title": "آخر العمليات:",
    "history_empty": "لا توجد عمليات بعد.",
    "h_transfer_out": "{when} · تحويل إلى {name} −{amount}",
    "h_transfer_held": "{when} · محجوز لـ {name} −{amount}",
    "h_transfer_in": "{when} · وارد من {name} +{amount}",
    "h_bill": "{when} · فاتورة {biller} −{amount}",
    "h_topup": "{when} · شحن رصيد …{last4} −{amount}",
    "h_admin_credit": "{when} · إيداع من الإدارة +{amount}",
    "h_admin_debit": "{when} · سحب من الإدارة −{amount}",
    "h_signup_credit": "{when} · هدية فتح الحساب +{amount}",
    "h_hold_refund": "{when} · استرداد تحويل محجوز +{amount}",
    "h_reversed": " (مسترد)",
    "history_today": "صرفت اليوم {spent} دينار من حدك اليومي {limit} (يحسب الحد المبالغ دون العمولة).",
    "my_account": "حسابك: {name}\nالرقم: {number}\nالحالة: {status}\nالرصيد: {balance} دينار\n"
                  "الحد اليومي: {limit} (المتبقي اليوم {left})",
    "my_holds": "محجوز لأرقام بلا حساب: {count} ({amount} دينار)",
    "status_active": "فعّال", "status_frozen": "موقوف مؤقتاً", "status_invited": "مدعو", "status_closed": "مغلق",
    "change_current": "اكتب رمزك الحالي.",
    "change_new": "حسناً. اكتب الآن الرمز الجديد (4 إلى 6 أرقام).",
    "change_done": "تم تغيير الرمز ✅",
    "change_wrong": "الرمز الحالي غير صحيح ولم يتغيّر شيء. اكتبه مرة أخرى أو «إلغاء».",
    "change_locked": "قُفل الرمز ربع ساعة بعد 5 محاولات خاطئة. لم يتغيّر شيء.",
    "forgot": "الرمز محفوظ مشفّراً ولا نستطيع استعادته. هل نطلب من الإدارة تصفيره؟ سيتواصلون معك أولاً.",
    "forgot_sent": "تم الطلب ✅ ستتواصل معك الإدارة. لا تعطِ رمزك لأحد.",
    "fund_how": {"f": "يُضاف المال إلى حسابك الآن عن طريق الإدارة، أو بتحويل من شخص لديه حساب هنا إلى رقمك. "
                      "هل نطلب من الإدارة التواصل معك لإضافة رصيد؟",
                 "m": "يُضاف المال إلى حسابك الآن عن طريق الإدارة، أو بتحويل من شخص لديه حساب هنا إلى رقمك. "
                      "هل نطلب من الإدارة التواصل معك لإضافة رصيد؟"},
    "fund_sent": "تم الطلب ✅ ستتواصل معك الإدارة، وتصلك رسالة حين يُضاف الرصيد إلى حسابك.",
    "rename_me_ask": "ما الاسم الجديد؟",
    "rename_me_done": "تم، أصبح اسمك «{name}».",
    "manage_cancelled": "حسناً، لم أغيّر شيئاً.",
    "n_transfer_in": "وصلك {amount} دينار من {name} (…{last4}).",
    "n_hold_claimed": "صاحب الرقم …{last4} ({name}) فتح حساباً واستلم {amount} دينار التي حوّلتها.",
    "n_hold_refunded": "أعدنا إليك {amount} دينار: الرقم …{last4} لم يفتح حساباً خلال 7 أيام.",
    "n_hold_cancelled": "أعدنا إليك {amount} دينار من التحويل المحجوز للرقم …{last4}.",
    "n_admin_credit": "أضيف إلى رصيدك {amount} دينار. السبب: {reason}",
    "n_admin_debit": "سُحب من رصيدك {amount} دينار. السبب: {reason}",
    "n_pin_reset": "صفّرنا رمزك السري بناءً على طلبك.",
    "n_frozen": "أوقف حسابك مؤقتاً: يمكنك رؤية رصيدك لكن لا يمكنك الدفع. تواصل معنا.",
    "n_unfrozen": "عاد حسابك فعّالاً ✅",
    "n_balance": "رصيدك الآن {balance} دينار.",
}

EN: dict[str, T] = {
    "offer": "{hello}! I'm the smart payment agent: I pay bills and send money to any number from one sentence, "
             "typed or spoken.\nThis number has no account yet. Shall we open one? It takes a minute.",
    "pilot": "Note: money here is a balance inside this service, and bill payments don't reach the billers yet.",
    "name_confirm": "Shall I register you as “{name}”? People you pay will see this name.",
    "ask_name": "What's your full name, as you'd like others to see it?",
    "bad_name": "A name is letters only (2 to 40), no digits. Please write it again.",
    "ask_gender": "So I address you correctly in Arabic replies:",
    "ask_pin": "Now choose a secret PIN of 4 to 6 digits and send it as a message. You'll confirm every payment "
               "with it. Never share it. We only ask for it after you tap “Yes, pay”.",
    "pin_weak": "That PIN is easy to guess (like 1234, 0000 or the end of your number). Please choose another.",
    "pin_not_digits": "A PIN is 4 to 6 digits only. Send it as a message.",
    "pin_again": "Type it once more to confirm.",
    "pin_mismatch": "The two PINs don't match. Let's start again: type a new PIN.",
    "pin_restart": "Let's start the PIN again. Choose 4 to 6 digits.",
    "done": "Your account is open ✅ Your account number is your WhatsApp number {number}. Balance: {balance} dinars.\n"
            "Try “add generator account 350” or “send 10000 to 07xx…”. Type “help” for more.\n"
            "To put money in your account, write “fund my wallet”.",
    "claimed_intro": "Transfers held for your number have arrived in your balance:",
    "claimed_line": "• {amount} dinars from {name} (…{last4})",
    "not_now": "Sure. Whenever you like, type “open account”.",
    "finish_first": "Let's finish opening your account first.",
    "signup_cancelled": "Sign-up cancelled, nothing was saved. Whenever you like, type “open account”.",
    "signup_needs_number": "An account needs a phone number: your account is your WhatsApp number. Please message "
                           "us from a WhatsApp account with a number.",
    "signup_invite_only": "New accounts are by invitation for now. We've told the admin and they'll contact you.",
    "signup_closed": "New sign-ups are paused right now. Please try later.",
    "account_closed": "This account is closed. Contact us if you need anything.",
    "voice_in_signup": "Please type this step (a PIN is never said out loud).",
    "invited_hello": "{hello} {first}! We've opened an account for you on this number; your balance is {balance} "
                     "dinars. You only need to choose a PIN to pay.",
    "pin_reset_hello": "{hello} {first}! Your PIN was reset. Choose a new PIN of 4 to 6 digits and send it as a message.",
    "pin_set_done": "Done ✅ Your new PIN is saved. Balance: {balance} dinars.",
    "btn_open": "Open account", "btn_later": "Not now", "btn_yes": "Yes", "btn_other_name": "Other name",
    "btn_male": "Male", "btn_female": "Female", "btn_save": "Save it", "btn_send": "Send money", "btn_no": "No",
    "btn_rename": "Yes, rename", "btn_keep": "Keep it", "btn_request": "Yes, request", "btn_delete": "Yes, delete",
    "btn_confirm_save": "Save",
    "welcome_back": "{hello} {first}! Your balance is {balance} dinars. What do you need?",
    "help": "I can pay your bills, send money to any Iraqi number and top up credit, in one sentence typed or spoken:\n"
            "• “send 25000 to 0770 123 4567” or “send 50000 to Ahmed”\n"
            "• “add Ahmed 07701234567” · “contacts”\n"
            "• “add generator account 350” · “pay the generator” · “my bills”\n"
            "• “top up my phone with 10000” · “balance” · “history” · “my account”\n"
            "• “change PIN” · “forgot PIN”\n"
            "Before any payment you get a card: tap “Yes, pay”, then type your PIN.",
    "pin_is_secret": "Your PIN is stored encrypted: we can't see it or send it back. Forgot it? Type “forgot PIN”.",
    "contact_saved": "Saved {name} ({number}).",
    "contact_has_account": "They have an account here: transfers reach them at once.",
    "contact_same": "{name} is already saved with this number.",
    "contact_other": "This number is already saved as “{old}”. Rename it to “{new}”?",
    "contact_self": "That's your own number 🙂",
    "contact_limit": "You've reached the contact limit ({limit}). Delete an old one first.",
    "contact_bad_number": "That isn't an Iraqi mobile number. Write it like 07701234567.",
    "contact_ask_name": "What name should I save {number} under?",
    "contact_renamed": "Done, the name is now “{name}”.",
    "contact_deleted": "Deleted {name} from your contacts.",
    "contact_confirm_delete": "Delete {name} (…{last4}) from your contacts?",
    "contact_not_found": "I couldn't find that name in your contacts. Type “contacts” to see the list.",
    "contact_which": "Which one do you mean? {options}",
    "contacts_title": "Your saved contacts ({count}):",
    "contacts_empty": "No saved contacts yet. Add one: “add Ahmed 07701234567”.",
    "contacts_more": "And {more} more. Type “more”.",
    "number_only": "Do you want to save this number ({number}) or send it money?",
    "send_to_number": "How much do you want to send to {number}?",
    "bill_kind": "Which account do you want to add? {options}",
    "bill_which_biller": "Which company? {options}",
    "bill_ask_number": "What's the {biller} account number?",
    "bill_bad_number": "That account number isn't valid for {biller}. Write it as it is on the receipt.",
    "bill_confirm": "Save the {biller} account number {account}?",
    "bill_saved": "Added ✅ It has a {month} bill of {amount} dinars. Want to pay it? Write “pay the {label}”.",
    "bill_exists": "This account is already saved.",
    "bill_limit": "You've reached the account limit ({limit}). Delete an old one first.",
    "bills_title": "Your accounts:",
    "bills_line": "• {label} ({biller}) number {account}{due}",
    "bills_due": " · {amount} dinars due ({month})",
    "bills_paid": " · no unpaid bill",
    "bills_empty": "No saved bill accounts. Add one: “add generator account 350”.",
    "bill_deleted": "Deleted the {label} account.",
    "kinds": {"electricity": "electricity", "generator": "generator", "internet": "internet", "water": "water"},
    "history_title": "Recent activity:",
    "history_empty": "No activity yet.",
    "h_transfer_out": "{when} · sent to {name} −{amount}",
    "h_transfer_held": "{when} · held for {name} −{amount}",
    "h_transfer_in": "{when} · received from {name} +{amount}",
    "h_bill": "{when} · {biller} bill −{amount}",
    "h_topup": "{when} · top-up …{last4} −{amount}",
    "h_admin_credit": "{when} · deposit by the admin +{amount}",
    "h_admin_debit": "{when} · withdrawal by the admin −{amount}",
    "h_signup_credit": "{when} · sign-up gift +{amount}",
    "h_hold_refund": "{when} · held transfer refunded +{amount}",
    "h_reversed": " (returned)",
    "history_today": "Today you spent {spent} dinars of your {limit} dinar daily limit (the limit counts amounts, not fees).",
    "my_account": "Your account: {name}\nNumber: {number}\nStatus: {status}\nBalance: {balance} dinars\n"
                  "Daily limit: {limit} ({left} left today)",
    "my_holds": "Held for numbers with no account: {count} ({amount} dinars)",
    "status_active": "active", "status_frozen": "paused", "status_invited": "invited", "status_closed": "closed",
    "change_current": "Type your current PIN.",
    "change_new": "OK. Now type the new PIN (4 to 6 digits).",
    "change_done": "PIN changed ✅",
    "change_wrong": "The current PIN is wrong; nothing changed. Type it again or “cancel”.",
    "change_locked": "The PIN is locked for 15 minutes after 5 wrong tries. Nothing changed.",
    "forgot": "Your PIN is stored encrypted and can't be recovered. Shall we ask the admin to reset it? "
              "They'll contact you first.",
    "forgot_sent": "Requested ✅ The admin will contact you. Never share your PIN with anyone.",
    "fund_how": "Money gets into your account through the admin for now, or when someone with an account here "
                "sends to your number. Shall we ask the admin to contact you and add a balance?",
    "fund_sent": "Requested ✅ The admin will contact you, and you'll get a message as soon as the balance is in.",
    "rename_me_ask": "What's the new name?",
    "rename_me_done": "Done, your name is now “{name}”.",
    "manage_cancelled": "OK, nothing changed.",
    "n_transfer_in": "You received {amount} dinars from {name} (…{last4}).",
    "n_hold_claimed": "{name} (…{last4}) opened an account and received the {amount} dinars you sent.",
    "n_hold_refunded": "{amount} dinars came back to you: the number …{last4} didn't open an account within 7 days.",
    "n_hold_cancelled": "{amount} dinars came back to you from the transfer held for …{last4}.",
    "n_admin_credit": "{amount} dinars were added to your balance. Reason: {reason}",
    "n_admin_debit": "{amount} dinars were taken from your balance. Reason: {reason}",
    "n_pin_reset": "Your PIN was reset at your request.",
    "n_frozen": "Your account is paused: you can see your balance but can't pay. Please contact us.",
    "n_unfrozen": "Your account is active again ✅",
    "n_balance": "Your balance is now {balance} dinars.",
}

# ---- the identity document step (sign-up and «وثّق حسابي») ----
# The reason of each verdict (not a document, expired, the name differs ...) is said with kyc.texts; these are the
# words around it: the question with its notice, the skip, what happens next, the account's level, the notices.
KYC_IQ: dict[str, T] = {
    "kyc_ask": {"f": f"هسه توثيق الهوية: صوّري وحدة من وثائقج ودزيها هنا: {ACCEPTED_IQ}. خلّي الصورة واضحة وكل "
                     "الكتابة مبينة.",
                "m": f"هسه توثيق الهوية: صوّر وحدة من وثائقك ودزها هنا: {ACCEPTED_IQ}. خلّي الصورة واضحة وكل "
                     "الكتابة مبينة."},
    "kyc_notice": "للعلم: الخدمة بعدها تجريبية والفلوس بيها رصيد داخل الخدمة بس. الصورة يقراها قارئ آلي مرة وحدة "
                  "وما تنحفظ. اللي ينحفظ بس: نوع الوثيقة، الاسم، آخر 4 أرقام من رقمها، وسنة الميلاد.",
    "kyc_later_hint": {"f": "إذا مو هسه دوسي «بعدين»: ينفتح حسابج بحدود صغيرة ({single_max} دينار بالدفعة الوحدة "
                            "و{daily_limit} دينار باليوم) وتوثّقين بأي وقت.",
                       "m": "إذا مو هسه دوس «بعدين»: ينفتح حسابك بحدود صغيرة ({single_max} دينار بالدفعة الوحدة "
                            "و{daily_limit} دينار باليوم) وتوثّق بأي وقت."},
    "kyc_required_hint": {"f": "هالخطوة لازمة حتى ينفتح الحساب. إذا ما تريدين تكملين اكتبي «الغاء».",
                          "m": "هالخطوة لازمة حتى ينفتح الحساب. إذا ما تريد تكمل اكتب «الغاء»."},
    "btn_kyc_later": "بعدين",
    "kyc_name_two_parts": {"f": "حتى نطابق الاسم ويا الهوية، اكتبي اسمج واسم أبوج على الأقل، مثل «زينب هادي».",
                           "m": "حتى نطابق الاسم ويا الهوية، اكتب اسمك واسم أبوك على الأقل، مثل «علي حسن»."},
    "kyc_later_done": {"f": "تمام، بلا توثيق هسه. من تريدين توثّقين اكتبي «وثّق حسابي».",
                       "m": "تمام، بلا توثيق هسه. من تريد توثّق اكتب «وثّق حسابي»."},
    "kyc_small_limits": {"f": "حسابج ينفتح هسه بالحدود الصغيرة: {single_max} دينار بالدفعة الوحدة و{daily_limit} "
                              "دينار باليوم.",
                         "m": "حسابك ينفتح هسه بالحدود الصغيرة: {single_max} دينار بالدفعة الوحدة و{daily_limit} "
                              "دينار باليوم."},
    "kyc_attempt": "المحاولة {attempt} من {attempts}.",
    "kyc_needs_photo": {"f": "بهالخطوة دزي صورة، مو رسالة صوتية.", "m": "بهالخطوة دز صورة، مو رسالة صوتية."},
    "kyc_photo_not_now": "أگدر أقرا صورة هوية بس وقت فتح الحساب أو وقت توثيقه.",
    "kyc_verify_how": {"f": "حتى توثّقين حسابج اكتبي «وثّق حسابي».", "m": "حتى توثّق حسابك اكتب «وثّق حسابي»."},
    "kyc_duplicate_document": {"f": "هالوثيقة تحتاج مراجعة من موظف قبل ما يتوثّق الحساب. نرجعلج خبر، ولحد ذاك "
                                    "الوقت تبقى حدودج الصغيرة.",
                               "m": "هالوثيقة تحتاج مراجعة من موظف قبل ما يتوثّق الحساب. نرجعلك خبر، ولحد ذاك "
                                    "الوقت تبقى حدودك الصغيرة."},
    "kyc_stopped": "وگفنا فتح الحساب، وما انحفظ شي من الصورة.",
    "kyc_front_again": {"f": "ما بقت عدنا قراءة وجه البطاقة، فنحتاجه مرة ثانية. ما راحت عليج محاولة.",
                        "m": "ما بقت عدنا قراءة وجه البطاقة، فنحتاجه مرة ثانية. ما راحت عليك محاولة."},
    "kyc_staff_will_look": {"f": "موظف راح يراجع حسابج ونرجعلج خبر. لحد ذاك الوقت تبقى حدودج الصغيرة.",
                            "m": "موظف راح يراجع حسابك ونرجعلك خبر. لحد ذاك الوقت تبقى حدودك الصغيرة."},
    "kyc_under_age_paused": {"f": "موظف راح يراجع حسابج ونرجعلج خبر. لحد ذاك الوقت الدفع من حسابج موقوف، والفلوس "
                                  "اللي تندزلج توصلج عادي.",
                             "m": "موظف راح يراجع حسابك ونرجعلك خبر. لحد ذاك الوقت الدفع من حسابك موقوف، والفلوس "
                                  "اللي تندزلك توصلك عادي."},
    "kyc_under_age_hold": {"f": "حسابج موقوف لحد ما يراجعه موظف، لأن الهوية اللي دزيتيها تبين العمر أقل من 18. "
                                "نرجعلج خبر.",
                           "m": "حسابك موقوف لحد ما يراجعه موظف، لأن الهوية اللي دزيتها تبين العمر أقل من 18. "
                                "نرجعلك خبر."},
    "kyc_start_again": {"f": "إذا عندج وثيقة ثانية مقبولة، اكتبي «افتح حساب» وابدي من جديد.",
                        "m": "إذا عندك وثيقة ثانية مقبولة، اكتب «افتح حساب» وابدي من جديد."},
    "kyc_unchanged": {"f": "حسابج وحدوده مثل ما هي، وما انحفظ شي من الصورة.",
                      "m": "حسابك وحدوده مثل ما هي، وما انحفظ شي من الصورة."},
    "kyc_verify_cancel_hint": {"f": "إذا غيّرتي رأيج اكتبي «الغاء».", "m": "إذا غيّرت رأيك اكتب «الغاء»."},
    "kyc_verify_cancelled": {"f": "تمام، لغيت التوثيق. حدود حسابج مثل ما هي.",
                             "m": "تمام، لغيت التوثيق. حدود حسابك مثل ما هي."},
    "kyc_already_verified": {"f": "حسابج موثّق ✅ حدج اليومي {daily_limit} دينار، وأكثر مبلغ بالدفعة الوحدة "
                                  "{single_max} دينار.",
                             "m": "حسابك موثّق ✅ حدك اليومي {daily_limit} دينار، وأكثر مبلغ بالدفعة الوحدة "
                                  "{single_max} دينار."},
    "kyc_not_needed": {"f": "حسابج ما يحتاج توثيق: حدوده كاملة.", "m": "حسابك ما يحتاج توثيق: حدوده كاملة."},
    "kyc_unavailable": {"f": "توثيق الهوية مو متوفر هسه. جربي بعدين.", "m": "توثيق الهوية مو متوفر هسه. جرب بعدين."},
    "kyc_try_tomorrow": {"f": "وصلتي حد محاولات التوثيق لليوم. جربي باجر.",
                         "m": "وصلت حد محاولات التوثيق لليوم. جرب باجر."},
    "kyc_line_verified": "التوثيق: موثّق ✅ (أكثر مبلغ بالدفعة الوحدة {single_max} دينار)",
    "kyc_line_pending": "التوثيق: قيد المراجعة عند موظف (أكثر مبلغ بالدفعة الوحدة {single_max} دينار)",
    "kyc_line_none": {"f": "التوثيق: غير موثّق (أكثر مبلغ بالدفعة الوحدة {single_max} دينار). حتى توثّقين اكتبي "
                           "«وثّق حسابي».",
                      "m": "التوثيق: غير موثّق (أكثر مبلغ بالدفعة الوحدة {single_max} دينار). حتى توثّق اكتب "
                           "«وثّق حسابي»."},
    "btn_verify": "وثّق حسابي",
    "n_kyc_approved": {"f": "تم توثيق حسابج ✅ صار حدج اليومي {daily_limit} دينار، وأكثر مبلغ بالدفعة الوحدة "
                            "{single_max} دينار.",
                       "m": "تم توثيق حسابك ✅ صار حدك اليومي {daily_limit} دينار، وأكثر مبلغ بالدفعة الوحدة "
                            "{single_max} دينار."},
    "n_kyc_rejected": {"f": "ما گدرنا نوثّق حسابج. السبب: {reason}\nتبقى حدودج الصغيرة. تگدرين تدزين صورة هوية "
                            "ثانية: اكتبي «وثّق حسابي».",
                       "m": "ما گدرنا نوثّق حسابك. السبب: {reason}\nتبقى حدودك الصغيرة. تگدر تدز صورة هوية "
                            "ثانية: اكتب «وثّق حسابي»."},
    "manager_kyc_review": "توثيق ينتظر مراجعة: {name} ({number})",
}

KYC_MSA: dict[str, T] = {
    "kyc_ask": {"f": f"الآن توثيق الهوية: صوّري إحدى وثائقك وأرسليها هنا: {ACCEPTED_MSA}. يجب أن تكون الصورة "
                     "واضحة والكتابة كلها ظاهرة.",
                "m": f"الآن توثيق الهوية: صوّر إحدى وثائقك وأرسلها هنا: {ACCEPTED_MSA}. يجب أن تكون الصورة "
                     "واضحة والكتابة كلها ظاهرة."},
    "kyc_notice": "للعلم: الخدمة ما زالت تجريبية والمال فيها رصيد داخل الخدمة فقط. يقرأ الصورة قارئ آلي مرة واحدة "
                  "ولا تُحفظ. ما يُحفظ فقط: نوع الوثيقة والاسم وآخر 4 أرقام من رقمها وسنة الميلاد.",
    "kyc_later_hint": {"f": "إن لم يكن الآن فاضغطي «لاحقاً»: يُفتح حسابك بحدود صغيرة ({single_max} دينار في الدفعة "
                            "الواحدة و{daily_limit} دينار في اليوم) ويمكنك التوثيق في أي وقت.",
                       "m": "إن لم يكن الآن فاضغط «لاحقاً»: يُفتح حسابك بحدود صغيرة ({single_max} دينار في الدفعة "
                            "الواحدة و{daily_limit} دينار في اليوم) ويمكنك التوثيق في أي وقت."},
    "kyc_required_hint": {"f": "هذه الخطوة لازمة لفتح الحساب. إن لم تريدي المتابعة فاكتبي «إلغاء».",
                          "m": "هذه الخطوة لازمة لفتح الحساب. إن لم ترد المتابعة فاكتب «إلغاء»."},
    "btn_kyc_later": "لاحقاً",
    "kyc_name_two_parts": {"f": "لنطابق الاسم مع الوثيقة، اكتبي اسمك واسم أبيك على الأقل، مثل «زينب هادي».",
                           "m": "لنطابق الاسم مع الوثيقة، اكتب اسمك واسم أبيك على الأقل، مثل «علي حسن»."},
    "kyc_later_done": {"f": "حسناً، من دون توثيق الآن. متى أردتِ التوثيق اكتبي «وثّق حسابي».",
                       "m": "حسناً، من دون توثيق الآن. متى أردت التوثيق اكتب «وثّق حسابي»."},
    "kyc_small_limits": "يُفتح حسابك الآن بالحدود الصغيرة: {single_max} دينار في الدفعة الواحدة و{daily_limit} "
                        "دينار في اليوم.",
    "kyc_attempt": "المحاولة {attempt} من {attempts}.",
    "kyc_needs_photo": {"f": "في هذه الخطوة أرسلي صورة، لا رسالة صوتية.",
                        "m": "في هذه الخطوة أرسل صورة، لا رسالة صوتية."},
    "kyc_photo_not_now": "أستطيع قراءة صورة وثيقة هوية فقط أثناء فتح الحساب أو أثناء توثيقه.",
    "kyc_verify_how": {"f": "لتوثيق حسابك اكتبي «وثّق حسابي».", "m": "لتوثيق حسابك اكتب «وثّق حسابي»."},
    "kyc_duplicate_document": "تحتاج هذه الوثيقة إلى مراجعة أحد الموظفين قبل توثيق الحساب. سنخبرك بالنتيجة، وحتى "
                              "ذلك الحين تبقى حدودك الصغيرة.",
    "kyc_stopped": "أوقفنا فتح الحساب، ولم يُحفظ شيء من الصورة.",
    "kyc_front_again": "لم تعد لدينا قراءة وجه البطاقة، لذلك نحتاجه مرة أخرى. لم تُحسب عليك محاولة.",
    "kyc_staff_will_look": "سيراجع أحد الموظفين حسابك ونخبرك بالنتيجة. حتى ذلك الحين تبقى حدودك الصغيرة.",
    "kyc_under_age_paused": "سيراجع أحد الموظفين حسابك ونخبرك بالنتيجة. حتى ذلك الحين الدفع من حسابك موقوف، وما "
                            "يُرسل إليك يصلك كالمعتاد.",
    "kyc_under_age_hold": {"f": "حسابك موقوف حتى يراجعه أحد الموظفين، لأن الوثيقة التي أرسلتِها تُظهر عمراً أقل من 18 "
                                "سنة. سنخبرك بالنتيجة.",
                           "m": "حسابك موقوف حتى يراجعه أحد الموظفين، لأن الوثيقة التي أرسلتها تُظهر عمراً أقل من 18 "
                                "سنة. سنخبرك بالنتيجة."},
    "kyc_start_again": {"f": "إن كانت لديك وثيقة أخرى مقبولة، اكتبي «افتح حساب» وابدئي من جديد.",
                        "m": "إن كانت لديك وثيقة أخرى مقبولة، اكتب «افتح حساب» وابدأ من جديد."},
    "kyc_unchanged": "حسابك وحدوده كما هي، ولم يُحفظ شيء من الصورة.",
    "kyc_verify_cancel_hint": {"f": "إن غيّرتِ رأيك اكتبي «إلغاء».", "m": "إن غيّرت رأيك اكتب «إلغاء»."},
    "kyc_verify_cancelled": "حسناً، ألغيت التوثيق. حدود حسابك كما هي.",
    "kyc_already_verified": "حسابك موثّق ✅ حدك اليومي {daily_limit} دينار، وأقصى مبلغ في الدفعة الواحدة "
                            "{single_max} دينار.",
    "kyc_not_needed": "حسابك لا يحتاج إلى توثيق: حدوده كاملة.",
    "kyc_unavailable": {"f": "توثيق الهوية غير متاح الآن. حاولي لاحقاً.", "m": "توثيق الهوية غير متاح الآن. حاول لاحقاً."},
    "kyc_try_tomorrow": {"f": "بلغتِ حد محاولات التوثيق لهذا اليوم. حاولي غداً.",
                         "m": "بلغت حد محاولات التوثيق لهذا اليوم. حاول غداً."},
    "kyc_line_verified": "التوثيق: موثّق ✅ (أقصى مبلغ في الدفعة الواحدة {single_max} دينار)",
    "kyc_line_pending": "التوثيق: قيد مراجعة أحد الموظفين (أقصى مبلغ في الدفعة الواحدة {single_max} دينار)",
    "kyc_line_none": {"f": "التوثيق: غير موثّق (أقصى مبلغ في الدفعة الواحدة {single_max} دينار). للتوثيق اكتبي "
                           "«وثّق حسابي».",
                      "m": "التوثيق: غير موثّق (أقصى مبلغ في الدفعة الواحدة {single_max} دينار). للتوثيق اكتب "
                           "«وثّق حسابي»."},
    "btn_verify": "وثّق حسابي",
    "n_kyc_approved": "تم توثيق حسابك ✅ أصبح حدك اليومي {daily_limit} دينار، وأقصى مبلغ في الدفعة الواحدة "
                      "{single_max} دينار.",
    "n_kyc_rejected": {"f": "لم نتمكن من توثيق حسابك. السبب: {reason}\nتبقى حدودك الصغيرة. يمكنك إرسال صورة وثيقة "
                            "أخرى: اكتبي «وثّق حسابي».",
                       "m": "لم نتمكن من توثيق حسابك. السبب: {reason}\nتبقى حدودك الصغيرة. يمكنك إرسال صورة وثيقة "
                            "أخرى: اكتب «وثّق حسابي»."},
}

KYC_EN: dict[str, T] = {
    "kyc_ask": f"Now the identity check: take a photo of one of these and send it here: {ACCEPTED_EN}. Make sure "
               "the photo is sharp and all the writing shows.",
    "kyc_notice": "Note: this service is still a pilot and the money in it is a balance inside the service only (a "
                  "mock ledger). The photo is read once by an automated reader and is not kept. Only the document "
                  "type, the name, the last 4 digits of its number and the birth year are stored.",
    "kyc_later_hint": "Not now? Tap “Later”: your account opens with small limits ({single_max} dinars in one "
                      "payment and {daily_limit} dinars a day) and you can verify at any time.",
    "kyc_required_hint": "This step is needed to open the account. If you do not want to go on, type “cancel”.",
    "btn_kyc_later": "Later",
    "kyc_name_two_parts": "So we can match the name with the document, please write your name and your father's "
                          "name at least, like “Zainab Hadi”.",
    "kyc_later_done": "OK, no identity check for now. Whenever you want to verify, type “verify my account”.",
    "kyc_small_limits": "Your account opens now with the small limits: {single_max} dinars in one payment and "
                        "{daily_limit} dinars a day.",
    "kyc_attempt": "Try {attempt} of {attempts}.",
    "kyc_needs_photo": "At this step please send a photo, not a voice note.",
    "kyc_photo_not_now": "I can only read a photo of an identity document during sign-up or verification.",
    "kyc_verify_how": "To verify your account, type “verify my account”.",
    "kyc_duplicate_document": "This document needs a review by a member of staff before the account can be "
                              "verified. We will let you know. Until then your small limits stay.",
    "kyc_stopped": "We stopped the sign-up, and nothing from the photo was kept.",
    "kyc_front_again": "We no longer have the reading of the front of the card, so we need it once more. This did "
                       "not cost you a try.",
    "kyc_staff_will_look": "A member of staff will look at your account and we will let you know. Until then your "
                           "small limits stay.",
    "kyc_under_age_paused": "A member of staff will look at your account and we will let you know. Until then "
                            "payments from your account are paused; money sent to you still arrives.",
    "kyc_under_age_hold": "Your account is paused until a member of staff looks at it, because the document you sent "
                          "shows an age under 18. We will let you know.",
    "kyc_start_again": "If you have another accepted document, type “open account” and start again.",
    "kyc_unchanged": "Your account and its limits are as they were, and nothing from the photo was kept.",
    "kyc_verify_cancel_hint": "If you change your mind, type “cancel”.",
    "kyc_verify_cancelled": "OK, verification cancelled. Your account's limits are as they were.",
    "kyc_already_verified": "Your account is verified ✅ Your daily limit is {daily_limit} dinars, and the most in "
                            "one payment is {single_max} dinars.",
    "kyc_not_needed": "Your account does not need verification: it has the full limits.",
    "kyc_unavailable": "The identity check is not available right now. Please try later.",
    "kyc_try_tomorrow": "You have reached today's limit of verification tries. Please try tomorrow.",
    "kyc_line_verified": "Verification: verified ✅ (the most in one payment is {single_max} dinars)",
    "kyc_line_pending": "Verification: waiting for a staff review (the most in one payment is {single_max} dinars)",
    "kyc_line_none": "Verification: not verified (the most in one payment is {single_max} dinars). To verify, type "
                     "“verify my account”.",
    "btn_verify": "Verify my account",
    "n_kyc_approved": "Your account is verified ✅ Your daily limit is now {daily_limit} dinars, and the most in one "
                      "payment is {single_max} dinars.",
    "n_kyc_rejected": "We could not verify your account. Reason: {reason}\nYour small limits stay. You can send a "
                      "photo of another document: type “verify my account”.",
}

IQ.update(KYC_IQ)
MSA.update(KYC_MSA)
EN.update(KYC_EN)

# ---- the demo account (SIGNUP_MODE=demo, channels/whatsapp/demo.py) ----
# The intro is written before the person's gender is known, so it uses forms that read for anyone (the passive,
# «الرصيد» rather than «رصيدك», examples said by the person to the bot). Every number in it is filled by code.
DEMO_IQ: dict[str, T] = {
    "demo_open": "{hello}{name}! هذا حساب تجريبي، والفلوس اللي بي مو حقيقية، فالتجربة ما تكلف شي.",
    "demo_balance": "الرصيد: {balance} دينار.",
    "demo_sent": "{ago} انرسل {amount} دينار لـ{name}",
    "demo_sent_more": "{ago} {amount} دينار لـ{name}",
    "demo_bills": "وعليه فواتير هالشهر: {bills}.",
    "demo_bill": "{biller} {amount} دينار",
    "demo_and": "، و",
    "demo_also": " و",
    "demo_try": "تكفي جملة وحدة، كتابة أو برسالة صوتية، مثلاً: «ادفع فاتورة الكهرباء» أو «دز لأخوي 10 آلاف».",
    "demo_voice_approval": "🎙️ وشغلة حلوة: أول دفعة تنطلب برسالة صوتية وتتأكد بالرمز السري، بعدها الصوت وحده يكفي، "
                           "بلا رمز، للدفعات لحد {cap} دينار للناس والفواتير المعروفة بالحساب.",
    "demo_pin": "الرمز السري للدفع: {pin}\nهذا الرمز سري، ما ينعطى لأحد.",
    "demo_new_pin": "هذا رمز سري جديد للحساب التجريبي، والقديم بطل يشتغل:",
    "demo_guest": "ضيف",
    "ago_1": "قبل يوم",
    "ago_2": "قبل يومين",
    "ago_n": "قبل {n} أيام",
    "ago_many": "قبل {n} يوم",
    "h_demo_credit": "{when} · رصيد تجريبي +{amount}",
    "welcome_again": {"f": "{hello} {first}! شنو تحتاجين؟", "m": "{hello} {first}! شنو تحتاج؟"},
    "welcome_again_2": {"f": "هلا بيج {first}، بشنو أخدمج؟", "m": "هلا بيك {first}، بشنو أخدمك؟"},
    "welcome_again_3": {"f": "هلا {first}! آمريني، شنو المطلوب؟", "m": "هلا {first}! آمرني، شنو المطلوب؟"},
    "welcome_how": {"f": "{hello} {first}! الحمد لله زين، وانتي؟ شنو تحتاجين؟",
                    "m": "{hello} {first}! الحمد لله زين، وانت؟ شنو تحتاج؟"},
    "welcome_how_2": {"f": "الحمد لله تمام {first}، تسلمين. بشنو أخدمج؟",
                      "m": "الحمد لله تمام {first}، تسلم. بشنو أخدمك؟"},
}
DEMO_MSA: dict[str, T] = {
    "demo_open": "{hello}{name}! هذا حساب تجريبي، والأموال فيه ليست حقيقية، فالتجربة لا تكلّف شيئاً.",
    "demo_balance": "الرصيد: {balance} دينار.",
    "demo_sent": "{ago} حُوِّل {amount} دينار إلى {name}",
    "demo_sent_more": "{ago} {amount} دينار إلى {name}",
    "demo_bills": "وعليه فواتير هذا الشهر: {bills}.",
    "demo_bill": "{biller} {amount} دينار",
    "demo_and": "، و",
    "demo_also": " و",
    "demo_try": "تكفي جملة واحدة، كتابةً أو برسالة صوتية، مثلاً: «ادفع فاتورة الكهرباء» أو «حوّل 10,000 دينار إلى حسين».",
    "demo_voice_approval": "وبعد أول دفعة تُطلب برسالة صوتية وتُؤكَّد بالرمز السري، يكفي الصوت للموافقة على الدفعات "
                           "التالية حتى {cap} دينار للأشخاص والفواتير المعروفة في الحساب.",
    "demo_pin": "الرمز السري للدفع: {pin}\nهذا الرمز سري، لا يُعطى لأحد.",
    "demo_new_pin": "هذا رمز سري جديد للحساب التجريبي، والقديم لم يعد يعمل:",
    "demo_guest": "ضيف",
    "ago_1": "قبل يوم",
    "ago_2": "قبل يومين",
    "ago_n": "قبل {n} أيام",
    "ago_many": "قبل {n} يوماً",
    "h_demo_credit": "{when} · رصيد تجريبي +{amount}",
    "welcome_again": "{hello} {first}! كيف أساعدك؟",
    "welcome_again_2": "أهلاً بك {first}، بماذا أخدمك؟",
    "welcome_again_3": "أهلاً {first}! ما الذي تحتاجه اليوم؟",
    "welcome_how": "{hello} {first}! أنا بخير والحمد لله، شكراً لسؤالك. كيف أساعدك؟",
    "welcome_how_2": "بخير والحمد لله {first}. بماذا أخدمك؟",
}
DEMO_EN: dict[str, T] = {
    "demo_open": "{hello}{name}! This is a demo account: the money in it is pretend, so trying costs nothing.",
    "demo_balance": "Balance: {balance} dinars.",
    "demo_sent": "{ago} {amount} dinars went to {name}",
    "demo_sent_more": "{ago} {amount} dinars to {name}",
    "demo_bills": "Bills due this month: {bills}.",
    "demo_bill": "{biller} {amount} dinars",
    "demo_and": ", and ",
    "demo_also": " and ",
    "demo_try": "One sentence is enough, typed or as a voice note, for example “pay the electricity bill” or "
                "“send 10,000 to Hussein”.",
    "demo_voice_approval": "After the first payment asked for in a voice note and confirmed with the PIN, a voice "
                           "note alone can approve later payments of up to {cap} dinars to people and bills the "
                           "account already knows.",
    "demo_pin": "Your payment PIN: {pin}\nKeep it to yourself.",
    "demo_new_pin": "Here is a new PIN for the demo account; the old one no longer works:",
    "demo_guest": "Guest",
    "ago_1": "yesterday",
    "ago_2": "2 days ago",
    "ago_n": "{n} days ago",
    "ago_many": "{n} days ago",
    "h_demo_credit": "{when} · demo money +{amount}",
    "welcome_again": "{hello} {first}! What do you need?",
    "welcome_again_2": "Hi again {first}, how can I help?",
    "welcome_again_3": "Hey {first}! What can I do for you?",
    "welcome_how": "{hello} {first}! I'm good, thanks for asking. What do you need?",
    "welcome_how_2": "Doing well, thanks {first}! How can I help?",
}

IQ.update(DEMO_IQ)
MSA.update(DEMO_MSA)
EN.update(DEMO_EN)

BY_LANG = {"iq": IQ, "msa": MSA, "en": EN}
MANAGER_KEYS = ("manager_signup", "manager_pin_request", "manager_pin_blocked", "manager_invite_request",
                "manager_kyc_review")


def say(key: str, gender: str | None = "m", lang: str | None = None, **slots) -> str:
    """Fill a text in `lang` ('iq' default | 'msa' | 'en'); integers get thousands separators."""
    table = BY_LANG.get(lang or "iq", IQ)
    template = table.get(key, IQ[key])
    if isinstance(template, dict) and set(template) <= {"f", "m"}:
        template = template["f" if gender == "f" else "m"]
    values = {k: (format_iqd(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in slots.items()}
    return template.format(**values)


def kinds(lang: str | None = None) -> dict[str, str]:
    return dict(BY_LANG.get(lang or "iq", IQ)["kinds"])  # type: ignore[arg-type]


def all_texts(lang: str | None = None) -> list[str]:
    out: list[str] = []
    for table in [BY_LANG[lang]] if lang else BY_LANG.values():
        for value in table.values():
            out += list(value.values()) if isinstance(value, dict) else [value]
    return out

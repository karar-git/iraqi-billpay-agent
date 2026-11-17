"""Fixed messages for every ask, failure and outcome, in the user's language.

MESSAGES is Baghdadi Iraqi Arabic (the reference); payments.messages_msa and payments.messages_en hold
the same keys and slots in Modern Standard Arabic and English (payments.language picks one per message).
Each entry is either one text or {"f": ..., "m": ...} for a female / male user.
Every failure says what failed, whether money moved, and what to do next.
Keys for hard stops match the codes in payments.rules.
"""

from __future__ import annotations

MONTHS = {
    1: "كانون الثاني", 2: "شباط", 3: "آذار", 4: "نيسان", 5: "أيار", 6: "حزيران",
    7: "تموز", 8: "آب", 9: "أيلول", 10: "تشرين الأول", 11: "تشرين الثاني", 12: "كانون الأول",
}

MESSAGES: dict[str, str | dict[str, str]] = {
    # ---- hard stops (payments.rules codes) ----
    "insufficient_balance": {
        "f": "رصيدج ما يكفي: عندج {balance} دينار والمطلوب {needed} دينار ويا العمولة. "
             "ما انخصم منج شي. حتى تحطين فلوس بحسابج اكتبي «اشحن محفظتي»، أو جربي مبلغ أقل.",
        "m": "رصيدك ما يكفي: عندك {balance} دينار والمطلوب {needed} دينار ويا العمولة. "
             "ما انخصم منك شي. حتى تحط فلوس بحسابك اكتب «اشحن محفظتي»، أو جرب مبلغ أقل.",
    },
    # A bill or a top-up has no fee: the same text without the fee.
    "insufficient_balance_no_fee": {
        "f": "رصيدج ما يكفي: عندج {balance} دينار والمطلوب {needed} دينار. "
             "ما انخصم منج شي. حتى تحطين فلوس بحسابج اكتبي «اشحن محفظتي»، أو جربي مبلغ أقل.",
        "m": "رصيدك ما يكفي: عندك {balance} دينار والمطلوب {needed} دينار. "
             "ما انخصم منك شي. حتى تحط فلوس بحسابك اكتب «اشحن محفظتي»، أو جرب مبلغ أقل.",
    },
    "over_daily_limit": {
        "f": "هالمبلغ يعبر حدج اليومي ({limit} دينار)، باقي إلج اليوم {remaining} دينار. "
             "ما انخصم منج شي. جربي مبلغ أقل أو باجر.",
        "m": "هالمبلغ يعبر حدك اليومي ({limit} دينار)، باقي إلك اليوم {remaining} دينار. "
             "ما انخصم منك شي. جرب مبلغ أقل أو باجر.",
    },
    "non_positive_amount": {
        "f": "المبلغ لازم يكون أكثر من صفر. ما انخصم منج شي. گوليلي شكد تريدين تدزين.",
        "m": "المبلغ لازم يكون أكثر من صفر. ما انخصم منك شي. گلي شكد تريد تدز.",
    },
    "non_iqd": {
        "f": "نحوّل بالدينار العراقي بس. ما انخصم منج شي. گوليلي المبلغ بالدينار.",
        "m": "نحوّل بالدينار العراقي بس. ما انخصم منك شي. گلي المبلغ بالدينار.",
    },

    # ---- a message that pushes money to an unsaved number in the name of support (payments.scam) ----
    "scam_warning": {
        "f": "ما راح أحوّل: هاي طريقة احتيال معروفة. الدعم الحقيقي ما يطلب منج فلوس حتى يبقى حسابج شغال. "
             "ما انخصم منج شي، ولا تدزين لهالرقم.",
        "m": "ما راح أحوّل: هاي طريقة احتيال معروفة. الدعم الحقيقي ما يطلب منك فلوس حتى يبقى حسابك شغال. "
             "ما انخصم منك شي، ولا تدز لهالرقم.",
    },

    # text that claims authority or wants the card skipped (payments.scam.fake_authority): no text ever skips a card
    "authority_warning": {
        "f": "ما راح أحوّل: كلام الموافقات والتعليمات وإلغاء التأكيد ما يغيّر شي، وهاي طريقة احتيال معروفة. "
             "كل دفعة لازم بطاقة وتأكيد برمزج. ما انخصم منج شي، ولا تدزين لهالرقم.",
        "m": "ما راح أحوّل: كلام الموافقات والتعليمات وإلغاء التأكيد ما يغيّر شي، وهاي طريقة احتيال معروفة. "
             "كل دفعة لازم بطاقة وتأكيد برمزك. ما انخصم منك شي، ولا تدز لهالرقم.",
    },
    # a sent transfer cannot be taken back from here (payments.refund)
    "no_reversal": {
        "f": "ما أگدر أرجّع فلوس انبعثت. ما تحرك شي هسه وما انخصم منج شي. "
             "لترجيعها حجي اللي وصلته، وأگدر أسويلج تحويل جديد إذا تحتاجين.",
        "m": "ما أگدر أرجّع فلوس انبعثت. ما تحرك شي هسه وما انخصم منك شي. "
             "لترجيعها حجي اللي وصلته، وأگدر أسويلك تحويل جديد إذا تحتاج.",
    },
    # a message about money that asks for nothing now: a what-if, a joke, money that came in, a past transfer
    "no_payment_made": {
        "f": "ما سويت أي دفعة من هالرسالة وما انخصم منج شي بسببها. إذا تريدين تدزين هسه، اكتبيلي لمنو وشكد.",
        "m": "ما سويت أي دفعة من هالرسالة وما انخصم منك شي بسببها. إذا تريد تدز هسه، اكتبلي لمنو وشكد.",
    },

    # ---- payee / biller failures ----
    "unknown_payee": {
        "f": "ما لگيت {name} بالأسماء المحفوظة عندج. ما انخصم منج شي. "
             "اكتبي الاسم مثل ما هو محفوظ، أو ضيفيه للأسماء أول.",
        "m": "ما لگيت {name} بالأسماء المحفوظة عندك. ما انخصم منك شي. "
             "اكتب الاسم مثل ما هو محفوظ، أو ضيفه للأسماء أول.",
    },
    "no_bill_account": {
        "f": "ما عندج حساب {biller} محفوظ. ما انخصم منج شي. ضيفي رقم الحساب أول وبعدين ادفعي.",
        "m": "ما عندك حساب {biller} محفوظ. ما انخصم منك شي. ضيف رقم الحساب أول وبعدين ادفع.",
    },
    "other_person_bill": {
        "f": "هاي فاتورة شخص ثاني، وحسابه مو محفوظ عندج. ما انخصم منج شي. أگدر أدفع بس فواتير حساباتج المحفوظة، ضيفي رقم حسابه أول.",
        "m": "هاي فاتورة شخص ثاني، وحسابه مو محفوظ عندك. ما انخصم منك شي. أگدر أدفع بس فواتير حساباتك المحفوظة، ضيف رقم حسابه أول.",
    },
    "no_unpaid_bill": {
        "f": "ما عليج فاتورة {biller} هسه. ما انخصم منج شي. إذا تشوفين أكو غلط، راجعي {biller}.",
        "m": "ما عليك فاتورة {biller} هسه. ما انخصم منك شي. إذا تشوف أكو غلط، راجع {biller}.",
    },
    "no_bill_for_period": {
        "f": "ما لگيت فاتورة {biller} لشهر {month}. ما انخصم منج شي. إذا تقصدين شهر ثاني، گوليلي ياهو.",
        "m": "ما لگيت فاتورة {biller} لشهر {month}. ما انخصم منك شي. إذا تقصد شهر ثاني، گلي ياهو.",
    },
    "already_paid": {
        "f": "فاتورة {biller} لشهر {period} مدفوعة من قبل. ما انخصم منج شي، وما تحتاجين تسوين شي.",
        "m": "فاتورة {biller} لشهر {period} مدفوعة من قبل. ما انخصم منك شي، وما تحتاج تسوي شي.",
    },

    # ---- execution failures ----
    "quote_expired": {
        "f": "خلص وقت البطاقة (5 دقايق) وما انخصم منج شي. إذا بعدج تريدين، اطلبيها مرة ثانية.",
        "m": "خلص وقت البطاقة (5 دقايق) وما انخصم منك شي. إذا بعدك تريد، اطلبها مرة ثانية.",
    },
    "wrong_pin": {
        "f": "الرمز السري غلط. ما انخصم منج شي. جربي مرة ثانية.",
        "m": "الرمز السري غلط. ما انخصم منك شي. جرب مرة ثانية.",
    },
    "pin_locked": {
        "f": "انقفل الرمز السري ربع ساعة لأن انكتب غلط 5 مرات ورا بعض. ما انخصم منج شي. "
             "بعد ربع ساعة اطلبي الدفعة من جديد.",
        "m": "انقفل الرمز السري ربع ساعة لأن انكتب غلط 5 مرات ورا بعض. ما انخصم منك شي. "
             "بعد ربع ساعة اطلب الدفعة من جديد.",
    },
    "pin_in_chat": {
        "f": "الرمز ما ينكتب بالدردشة، اكتبيه بخانة الرمز ويا زر «إي، ادفع». ما انخصم منج شي.",
        "m": "الرمز ما ينكتب بالدردشة، اكتبه بخانة الرمز ويا زر «إي، ادفع». ما انخصم منك شي.",
    },
    "biller_down": {
        "f": "نظام {biller} عاطل هسه. ما انخصم منج شي. جربي بعد شوية.",
        "m": "نظام {biller} عاطل هسه. ما انخصم منك شي. جرب بعد شوية.",
    },
    "biller_declined_refunded": {
        "f": "{biller} رفضوا الدفعة، ورجعنا المبلغ كامل لمحفظتج. ما ضاع منج شي. جربي بعد شوية.",
        "m": "{biller} رفضوا الدفعة، ورجعنا المبلغ كامل لمحفظتك. ما ضاع منك شي. جرب بعد شوية.",
    },
    "failed": {
        "f": "العملية ما تمت بسبب خلل عدنا. ما انخصم منج شي. جربي بعد شوية.",
        "m": "العملية ما تمت بسبب خلل عدنا. ما انخصم منك شي. جرب بعد شوية.",
    },
    "unknown_outcome": {
        "f": "لهسه ما نعرف إذا انحوّلت، لا تعيديها. راح نتأكد ونرجعلج خبر.",
        "m": "لهسه ما نعرف إذا انحوّلت، لا تعيدها. راح نتأكد ونرجعلك خبر.",
    },
    "unknown_resolved_failed": {
        "f": "تأكدنا: العملية ما تمت، وما انخصم منج شي. تگدرين تعيديها هسه.",
        "m": "تأكدنا: العملية ما تمت، وما انخصم منك شي. تگدر تعيدها هسه.",
    },
    "cancelled": {
        "f": "تمام، بطّلناها. ما انخصم منج شي.",
        "m": "تمام، بطّلناها. ما انخصم منك شي.",
    },
    "card_voided": {
        "f": "لغيت البطاقة اللي قبل لأنج كتبتي طلب جديد. ما انخصم منج شي.",
        "m": "لغيت البطاقة اللي قبل لأنك كتبت طلب جديد. ما انخصم منك شي.",
    },
    "card_already_paid": {
        "f": "هاي البطاقة اندفعت قبل، مرة وحدة بس. ما راح ينخصم منج مرة ثانية، لا تعيدي الدفع. الوصل بسجل العمليات.",
        "m": "هاي البطاقة اندفعت قبل، مرة وحدة بس. ما راح ينخصم منك مرة ثانية، لا تعيد الدفع. الوصل بسجل العمليات.",
    },
    "stale_card": {
        "f": "هاي البطاقة ما تشتغل بعد (انلغت أو خلص وقتها). ما انخصم منج شي. "
             "إذا بعدج تريدين، اطلبيها مرة ثانية.",
        "m": "هاي البطاقة ما تشتغل بعد (انلغت أو خلص وقتها). ما انخصم منك شي. "
             "إذا بعدك تريد، اطلبها مرة ثانية.",
    },
    "wallet_down": {
        "f": "المحفظة ما ترد هسه. ما انخصم منج شي. جربي بعد شوية.",
        "m": "المحفظة ما ترد هسه. ما انخصم منك شي. جرب بعد شوية.",
    },
    "voice_unclear": {
        "f": "ما سمعت الرسالة الصوتية زين. ما انخصم منج شي. عيديها أو اكتبيلي طلبج.",
        "m": "ما سمعت الرسالة الصوتية زين. ما انخصم منك شي. عيدها أو اكتبلي طلبك.",
    },
    "voice_unavailable": {
        "f": "الرسائل الصوتية ما تشتغل هسه. ما انخصم منج شي. اكتبيلي طلبج.",
        "m": "الرسائل الصوتية ما تشتغل هسه. ما انخصم منك شي. اكتبلي طلبك.",
    },
    "next_request_failed": {
        "f": "ما گدرت أجهز الطلب الثاني هسه، وما انخصم منج شي عليه. اطلبيه بعد شوية.",
        "m": "ما گدرت أجهز الطلب الثاني هسه، وما انخصم منك شي عليه. اطلبه بعد شوية.",
    },

    # ---- questions ----
    "ask_amount": {"f": "شكد تريدين تدزين؟", "m": "شكد تريد تدز؟"},
    "ask_amount_for": {"f": "شكد تريدين تدزين لـ {name}؟", "m": "شكد تريد تدز لـ {name}؟"},
    "ask_topup_amount": {"f": "شكد تريدين تشحنين؟", "m": "شكد تريد تشحن؟"},
    "ask_usd": "شكد بالدينار؟ نحوّل بالدينار العراقي بس.",
    "ask_amount_unclear": {
        "f": "ما فهمت المبلغ زين. اكتبيه بالأرقام، مثلاً 50000.",
        "m": "ما فهمت المبلغ زين. اكتبه بالأرقام، مثلاً 50000.",
    },
    "ask_amount_exact": "أحتاج المبلغ بالضبط حتى أطلع البطاقة. شكد بالضبط؟",
    "ask_amount_multiple": {
        "f": "كتبتي أكثر من مبلغ. يا مبلغ تقصدين؟",
        "m": "كتبت أكثر من مبلغ. يا مبلغ تقصد؟",
    },
    "ask_amount_choice": {
        "f": "كتبتي مبلغين: {options}. يا واحد تقصدين؟",
        "m": "كتبت مبلغين: {options}. يا واحد تقصد؟",
    },
    "ask_period": {"f": "يا شهر تقصدين؟ {options}", "m": "يا شهر تقصد؟ {options}"},
    "ask_payee": {"f": "لمنو تريدين تدزين؟", "m": "لمنو تريد تدز؟"},
    "ask_which_payee": {
        "f": "عندج أكثر من واحد بهالاسم: {options}. ياهو منهم؟",
        "m": "عندك أكثر من واحد بهالاسم: {options}. ياهو منهم؟",
    },
    "ask_confirm_payee": {"f": "تقصدين {name}؟", "m": "تقصد {name}؟"},
    "ask_relation": {
        "f": "منو تقصدين بـ«{word}»؟ ما عندي اسم محفوظ بهالكلمة. گوليلي الاسم مثل ما هو محفوظ عندج.",
        "m": "منو تقصد بـ«{word}»؟ ما عندي اسم محفوظ بهالكلمة. گلي الاسم مثل ما هو محفوظ عندك.",
    },
    "ask_biller": {"f": "يا فاتورة تقصدين؟ {options}", "m": "يا فاتورة تقصد؟ {options}"},
    "ask_raseed": {
        "f": "تقصدين رصيد تلفون (شحن) لو رصيد محفظتج؟",
        "m": "تقصد رصيد تلفون (شحن) لو رصيد محفظتك؟",
    },
    "ask_operator": "الرقم على يا شبكة؟",
    "ask_pin": {
        "f": "اكتبي رمزج السري بخانة الرمز ويا زر «إي، ادفع». لا تكتبيه بالدردشة.",
        "m": "اكتب رمزك السري بخانة الرمز ويا زر «إي، ادفع». لا تكتبه بالدردشة.",
    },
    "press_button": {
        "f": "حتى أدفع، دوسي على زر «إي، ادفع» بالبطاقة. الكتابة وحدها ما تدفع.",
        "m": "حتى أدفع، دوس على زر «إي، ادفع» بالبطاقة. الكتابة وحدها ما تدفع.",
    },
    "offer_next": {"f": "خلصنا الأولى. تريدين نكمل {next}؟", "m": "خلصنا الأولى. تريد نكمل {next}؟"},
    "next_request": "هسه الطلب الثاني:",
    "ask_change": {
        "f": "تمام، لغيت هالبطاقة وما انخصم منج شي. شنو تريدين تغيّرين، المبلغ لو الاسم؟",
        "m": "تمام، لغيت هالبطاقة وما انخصم منك شي. شنو تريد تغيّر، المبلغ لو الاسم؟",
    },
    "ask_change_bill": {
        "f": "تمام، لغيت هالبطاقة وما انخصم منج شي. مبلغ الفاتورة يحطه {biller}، ما نگدر نغيّره. "
             "تريدين فاتورة ثانية لو شهر ثاني؟",
        "m": "تمام، لغيت هالبطاقة وما انخصم منك شي. مبلغ الفاتورة يحطه {biller}، ما نگدر نغيّره. "
             "تريد فاتورة ثانية لو شهر ثاني؟",
    },
    "ask_operator_mismatch": "الرقم المنتهي بـ {last4} مو على شبكة {operator}. الرقم على يا شبكة؟",
    # a bill is paid in full: no card until the user says yes to the whole amount
    "ask_bill_full": {
        "f": "فاتورة {biller} مطلوب بيها {due} دينار، مو {typed} دينار، وتندفع كاملة بس. ما انخصم منج شي. "
             "تريدين تدفعين {due} دينار كاملة؟",
        "m": "فاتورة {biller} مطلوب بيها {due} دينار، مو {typed} دينار، وتندفع كاملة بس. ما انخصم منك شي. "
             "تريد تدفع {due} دينار كاملة؟",
    },
    "ask_bill_full_part": {
        "f": "فاتورة {biller} مطلوب بيها {due} دينار، وتندفع كاملة بس، ما أگدر أدفع جزء منها. ما انخصم منج شي. "
             "تريدين تدفعين {due} دينار كاملة؟",
        "m": "فاتورة {biller} مطلوب بيها {due} دينار، وتندفع كاملة بس، ما أگدر أدفع جزء منها. ما انخصم منك شي. "
             "تريد تدفع {due} دينار كاملة؟",
    },
    "alias_saved": {
        "f": "حفظت {name} باسم «{word}»، المرة الجاية بس گوليلي {word}.",
        "m": "حفظت {name} باسم «{word}»، المرة الجاية بس گلي {word}.",
    },
    "help": {
        "f": "هلا بيج. أگدر أدفعلج الفواتير، أحوّل فلوس، وأشحن رصيد. شتحتاجين؟",
        "m": "هلا بيك. أگدر أدفعلك الفواتير، أحوّل فلوس، وأشحن رصيد. شتحتاج؟",
    },
    # a demo account asks whether its money is real (Session._chat_reply when the writer gives nothing). One form
    # for both: a demo account seldom says its gender.
    "demo_is_pretend": "هذا حساب تجريبي، والفلوس اللي بي مو حقيقية، فالتجربة بيه ما يطلع منها دينار حقيقي.",

    # ---- other replies ----
    "unsupported": {
        "f": "هاي الخدمة ما متوفرة هسه. أگدر أساعدج بدفع الفواتير، تحويل الفلوس، وشحن الرصيد.",
        "m": "هاي الخدمة ما متوفرة هسه. أگدر أساعدك بدفع الفواتير، تحويل الفلوس، وشحن الرصيد.",
    },
    "balance": {"f": "رصيدج {balance} دينار.", "m": "رصيدك {balance} دينار."},
    "done_transfer": {
        "f": "تمت. دزّيتي {amount} دينار لـ {name}. رصيدج هسه {balance} دينار.",
        "m": "تمت. دزّيت {amount} دينار لـ {name}. رصيدك هسه {balance} دينار.",
    },
    "done_bill": {
        "f": "تمت. دفعتي فاتورة {biller} لشهر {period}، {amount} دينار. رصيدج هسه {balance} دينار.",
        "m": "تمت. دفعت فاتورة {biller} لشهر {period}، {amount} دينار. رصيدك هسه {balance} دينار.",
    },
    "done_topup": {
        "f": "تمت. شحنتي رصيد {amount} دينار للرقم المنتهي بـ {last4}. رصيدج هسه {balance} دينار.",
        "m": "تمت. شحنت رصيد {amount} دينار للرقم المنتهي بـ {last4}. رصيدك هسه {balance} دينار.",
    },

    # ---- soft-flag warning lines on the card (payments.rules codes, prefixed) ----
    "flag_first_time_payee": {
        "f": "تنبيه: ما دزّيتي لـ {name} قبل، هاي أول مرة. تأكدي من الاسم والرقم.",
        "m": "تنبيه: ما دزّيت لـ {name} قبل، هاي أول مرة. تأكد من الاسم والرقم.",
    },
    "flag_duplicate_recent": {
        "f": "تنبيه: نفس المبلغ لـ {name} انطلب قبل أقل من 10 دقايق. إذا مو قصدج تكررينها، بطّليها.",
        "m": "تنبيه: نفس المبلغ لـ {name} انطلب قبل أقل من 10 دقايق. إذا مو قصدك تكررها، بطّلها.",
    },
    "flag_amount_jump": {
        "f": "تنبيه: هالمبلغ أكثر من 3 مرات قد أكبر مبلغ سابق لـ {name}. تأكدي منه.",
        "m": "تنبيه: هالمبلغ أكثر من 3 مرات قد أكبر مبلغ سابق لـ {name}. تأكد منه.",
    },
    # a bill's amount is set by the biller: a different amount typed by the user is said out loud
    "flag_bill_amount_differs": {
        "f": "انتبهي: المبلغ المطلوب {due} دينار، مو {typed} دينار. الفاتورة تندفع كاملة بس.",
        "m": "انتبه: المبلغ المطلوب {due} دينار، مو {typed} دينار. الفاتورة تندفع كاملة بس.",
    },
    "flag_bill_amount_set": {
        "f": "انتبهي: مبلغ الفاتورة ثابت، المطلوب {due} دينار وتندفع كاملة بس.",
        "m": "انتبه: مبلغ الفاتورة ثابت، المطلوب {due} دينار وتندفع كاملة بس.",
    },
    # a saved contact, but the words sound like the fake-support scam (payments.scam): the card says so
    "flag_scam_caution": {
        "f": "انتبهي: كلام التهديد بالحساب أو طلب التحقق طريقة احتيال معروفة. الدعم الحقيقي ما يطلب فلوس. تأكدي قبل الدفع.",
        "m": "انتبه: كلام التهديد بالحساب أو طلب التحقق طريقة احتيال معروفة. الدعم الحقيقي ما يطلب فلوس. تأكد قبل الدفع.",
    },
    # top-up cards name a phone line, not a person
    "flag_first_time_payee_topup": {
        "f": "تنبيه: أول مرة تشحنين لهالرقم. تأكدي من الرقم.",
        "m": "تنبيه: أول مرة تشحن لهالرقم. تأكد من الرقم.",
    },
    "flag_duplicate_recent_topup": {
        "f": "تنبيه: نفس المبلغ لهالرقم انطلب قبل أقل من 10 دقايق. إذا مو قصدج تكررينها، بطّليها.",
        "m": "تنبيه: نفس المبلغ لهالرقم انطلب قبل أقل من 10 دقايق. إذا مو قصدك تكررها، بطّلها.",
    },
    "flag_amount_jump_topup": {
        "f": "تنبيه: هالمبلغ أكثر من 3 مرات قد أكبر شحن سابق لهالرقم. تأكدي منه.",
        "m": "تنبيه: هالمبلغ أكثر من 3 مرات قد أكبر شحن سابق لهالرقم. تأكد منه.",
    },
}

# Failures must say what failed, whether money moved, and the next step.
FAILURE_KEYS = (
    "insufficient_balance", "insufficient_balance_no_fee", "over_daily_limit", "non_positive_amount", "non_iqd",
    "scam_warning", "authority_warning", "no_reversal", "no_payment_made", "ask_bill_full", "ask_bill_full_part",
    "unknown_payee", "no_bill_account", "other_person_bill", "no_unpaid_bill", "already_paid",
    "quote_expired", "wrong_pin", "pin_locked", "pin_in_chat", "biller_down", "biller_declined_refunded",
    "failed", "unknown_outcome", "unknown_resolved_failed", "cancelled", "card_voided",
    "stale_card", "card_already_paid", "wallet_down", "next_request_failed", "no_bill_for_period", "voice_unclear", "voice_unavailable",
    "bill_changed", "recipient_closed",
)


def format_iqd(n: int) -> str:
    """50000 -> '50,000'."""
    return f"{n:,}"


def catalog(lang: str | None = None) -> dict[str, str | dict[str, str]]:
    """The message table of one language ('iq' | 'msa' | 'en'); anything else is Iraqi."""
    return CATALOGS.get(lang or IRAQI, MESSAGES)


def month_name(month: int, lang: str | None = None) -> str:
    return MONTH_NAMES.get(lang or IRAQI, MONTHS).get(month, str(month))


def format_period(period: str, lang: str | None = None) -> str:
    """'2026-09' -> 'أيلول 2026' ('September 2026' in English); anything else is returned as is."""
    try:
        year, month = period.split("-")
        return f"{MONTH_NAMES.get(lang or IRAQI, MONTHS)[int(month)]} {int(year)}"
    except (ValueError, KeyError, AttributeError):
        return period


def render(key: str, gender: str = "f", lang: str | None = None, **slots) -> str:
    """Fill a message in `lang`. Integers get thousands separators; 'period' gets the month name."""
    template = catalog(lang)[key]
    if isinstance(template, dict):
        template = template["m" if gender == "m" else "f"]
    values = {}
    for name, value in slots.items():
        if isinstance(value, int) and not isinstance(value, bool):
            value = format_iqd(value)
        elif name == "period":
            value = format_period(value, lang)
        values[name] = value
    return template.format(**values)


def flag_line(code: str, gender: str = "f", lang: str | None = None, **slots) -> str:
    """Warning line for a soft flag code from payments.rules."""
    return render(f"flag_{code}", gender, lang, **slots)


from payments.messages_en import MESSAGES_EN, MONTHS_EN  # noqa: E402  (plain tables, no cycle)
from payments.messages_msa import MESSAGES_MSA, MONTHS_MSA  # noqa: E402

IRAQI = "iq"
CATALOGS: dict[str, dict] = {"iq": MESSAGES, "msa": MESSAGES_MSA, "en": MESSAGES_EN}

from payments.messages_accounts import ACCOUNT_MESSAGES  # noqa: E402  (real accounts: same keys in all three)

for _lang, _extra in ACCOUNT_MESSAGES.items():
    CATALOGS[_lang].update(_extra)

from payments.messages_interact import INTERACT_MESSAGES  # noqa: E402  (options to tap, and the «why» lines)

for _lang, _extra in INTERACT_MESSAGES.items():
    CATALOGS[_lang].update(_extra)
MONTH_NAMES: dict[str, dict[int, str]] = {"iq": MONTHS, "msa": MONTHS_MSA, "en": MONTHS_EN}

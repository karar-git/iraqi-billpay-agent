"""The WhatsApp channel's own words in Modern Standard Arabic (فصحى) and English.

Same keys and slots as channels/whatsapp/texts.py (Baghdadi Arabic), except the manager's lines,
which are for Karar and stay Iraqi. MSA is used for MSA and every other Arabic dialect.
"""

from __future__ import annotations

TEXTS_MSA: dict[str, str | dict[str, str]] = {
    "paid_once": "هذه البطاقة دُفعت من قبل، مرة واحدة فقط. لن يُخصم منك مرة ثانية. هذا إيصالها:",
    "thanks": {
        "f": "عفواً! إن احتجتِ إلى شيء آخر فاكتبي لي، مثلاً «كم رصيدي».",
        "m": "عفواً! إن احتجت إلى شيء آخر فاكتب لي، مثلاً «كم رصيدي».",
    },
    "heard": {"f": "سمعتكِ تقولين: «{text}»", "m": "سمعتك تقول: «{text}»"},
    "voice_needs_full": {
        "f": "الرسائل الصوتية تعمل مع النسخة الكاملة من المساعد. اكتبي لي طلبك الآن.",
        "m": "الرسائل الصوتية تعمل مع النسخة الكاملة من المساعد. اكتب لي طلبك الآن.",
    },
    "unsupported_image": {
        "f": "وصلتني صورة، لكنني أفهم الكتابة{voice_or} فقط. اكتبي لي طلبك، مثلاً «ادفع فاتورة المولدة».",
        "m": "وصلتني صورة، لكنني أفهم الكتابة{voice_or} فقط. اكتب لي طلبك، مثلاً «ادفع فاتورة المولدة».",
    },
    "unsupported_sticker": {
        "f": "ملصق جميل! لكنني أفهم الكتابة{voice_or} فقط. اكتبي لي طلبك، مثلاً «ادفع فاتورة المولدة».",
        "m": "ملصق جميل! لكنني أفهم الكتابة{voice_or} فقط. اكتب لي طلبك، مثلاً «ادفع فاتورة المولدة».",
    },
    "unsupported_location": {
        "f": "وصلني الموقع، لكن الدفع لا يحتاج إلى موقع. اكتبي لي طلبك، مثلاً «ادفع فاتورة المولدة».",
        "m": "وصلني الموقع، لكن الدفع لا يحتاج إلى موقع. اكتب لي طلبك، مثلاً «ادفع فاتورة المولدة».",
    },
    "unsupported_other": {
        "f": "لا أستطيع فتح هذا النوع من الرسائل. اكتبي لي طلبك{voice_send}، مثلاً «ادفع فاتورة المولدة».",
        "m": "لا أستطيع فتح هذا النوع من الرسائل. اكتب لي طلبك{voice_send}، مثلاً «ادفع فاتورة المولدة».",
    },
    "thanks_2": {"f": "على الرحب والسعة! اكتبي لي متى احتجتِ.", "m": "على الرحب والسعة! اكتب لي متى احتجت."},
    "thanks_3": {"f": "لا شكر على واجب، أنا هنا إن احتجتِ شيئاً.", "m": "لا شكر على واجب، أنا هنا إن احتجت شيئاً."},
    "unsupported_image_2": {"f": "لا أستطيع قراءة الصور، بل الكتابة{voice_or} فقط. اكتبي لي ما تحتاجين.",
                            "m": "لا أستطيع قراءة الصور، بل الكتابة{voice_or} فقط. اكتب لي ما تحتاج."},
    "unsupported_sticker_2": {"f": "ملصق لطيف! لكنني أقرأ الكتابة{voice_or} فقط، فاكتبي لي ما تحتاجين.",
                              "m": "ملصق لطيف! لكنني أقرأ الكتابة{voice_or} فقط، فاكتب لي ما تحتاج."},
    "unsupported_location_2": {"f": "شكراً، لكن الدفع لا يحتاج إلى موقع. اكتبي لي ما تحتاجين.",
                               "m": "شكراً، لكن الدفع لا يحتاج إلى موقع. اكتب لي ما تحتاج."},
    "unsupported_other_2": {"f": "لم أستطع فتح هذه الرسالة. اكتبي لي طلبك{voice_send}.",
                            "m": "لم أستطع فتح هذه الرسالة. اكتب لي طلبك{voice_send}."},
    "voice_or": " والرسائل الصوتية",
    "voice_send": {"f": " أو أرسليه رسالة صوتية", "m": " أو أرسله رسالة صوتية"},
    "rate_limited": {
        "f": "وصلتِ إلى حد الرسائل في هذه الساعة ({limit} رسالة). استريحي قليلاً وعودي بعد ساعة. لم يُخصم منك شيء.",
        "m": "وصلت إلى حد الرسائل في هذه الساعة ({limit} رسالة). استرح قليلاً وعد بعد ساعة. لم يُخصم منك شيء.",
    },
    "error": {
        "f": "حدث خلل لدينا في هذه الرسالة. إن لم يصلك إيصال فالدفعة لم تتم. جرّبي بعد قليل.",
        "m": "حدث خلل لدينا في هذه الرسالة. إن لم يصلك إيصال فالدفعة لم تتم. جرّب بعد قليل.",
    },
    "card_header_transfer": "تأكيد تحويل",
    "card_header_bill": "تأكيد دفع فاتورة",
    "card_header_topup": "تأكيد شحن رصيد",
    "receipt_transfer": "تحويل إلى: {payee}",
    "receipt_bill": "فاتورة: {payee}، عن شهر {period}",
    "receipt_topup": "شحن رصيد للرقم المنتهي بـ {last4}",
    "receipt_amount": "المبلغ: {amount} دينار",
    "receipt_fee": "العمولة: {fee} دينار",
    "receipt_no_fee": "بلا عمولة",
    "receipt_total": "المجموع: {total} دينار",
    "receipt_balance": "الرصيد بعد الدفع: {balance} دينار",
    "receipt_reference": "رقم العملية: {reference}",
    "receipt_time": "الوقت: {time} بتوقيت بغداد",
    "ask_pin": {
        "f": "حسناً. اكتبي الآن رمزك السري في رسالة هنا لإتمام الدفع. لا نطلبه إلا بعد أن تضغطي «نعم، ادفع». هل نسيتِه؟ اكتبي «نسيت الرمز».",
        "m": "حسناً. اكتب الآن رمزك السري في رسالة هنا لإتمام الدفع. لا نطلبه إلا بعد أن تضغط «نعم، ادفع». هل نسيته؟ اكتب «نسيت الرمز».",
    },
    "pin_still_open": {
        "f": "البطاقة ما زالت مفتوحة: اكتبي رمزك السري الآن في رسالة.",
        "m": "البطاقة ما زالت مفتوحة: اكتب رمزك السري الآن في رسالة.",
    },
    "pin_wait": {
        "f": "خذي وقتك، البطاقة ما زالت مفتوحة. عندما تكونين جاهزة اكتبي رمزك السري في رسالة.",
        "m": "خذ وقتك، البطاقة ما زالت مفتوحة. عندما تكون جاهزاً اكتب رمزك السري في رسالة.",
    },
    "wrong_pin": {
        "f": "الرمز السري غير صحيح، ولم يُخصم منك شيء. اكتبيه مرة أخرى. بعد 5 محاولات خاطئة متتالية يُقفل ربع ساعة.",
        "m": "الرمز السري غير صحيح، ولم يُخصم منك شيء. اكتبه مرة أخرى. بعد 5 محاولات خاطئة متتالية يُقفل ربع ساعة.",
    },
    "tap_first": {
        "f": "اضغطي «نعم، ادفع» في البطاقة أولاً، ثم اكتبي رمزك السري. لم يُخصم منك شيء. هل تريدين تغيير المبلغ؟ اضغطي «تعديل».",
        "m": "اضغط «نعم، ادفع» في البطاقة أولاً، ثم اكتب رمزك السري. لم يُخصم منك شيء. هل تريد تغيير المبلغ؟ اضغط «تعديل».",
    },
    "pin_no_card": {
        "f": "لا نحتاج إلى الرمز الآن، فلا توجد بطاقة دفع مفتوحة. اطلبي الدفعة أولاً، وبعد «نعم، ادفع» اكتبي رمزك. ولا تكتبي رمزك إلا في هذه الخطوة.",
        "m": "لا نحتاج إلى الرمز الآن، فلا توجد بطاقة دفع مفتوحة. اطلب الدفعة أولاً، وبعد «نعم، ادفع» اكتب رمزك. ولا تكتب رمزك إلا في هذه الخطوة.",
    },
    "resting": {
        "f": "الخدمة متوقفة حتى الغد، فقد انتهت حصة اليوم. عودي غداً. لم يُخصم منك شيء.",
        "m": "الخدمة متوقفة حتى الغد، فقد انتهت حصة اليوم. عد غداً. لم يُخصم منك شيء.",
    },
    "message_lost": {
        "f": "انقطعت الخدمة لحظة وضاعت رسالتك الأخيرة قبل قراءتها. اكتبيها مرة أخرى من فضلك.",
        "m": "انقطعت الخدمة لحظة وضاعت رسالتك الأخيرة قبل قراءتها. اكتبها مرة أخرى من فضلك.",
    },
    "card_footer": "تنتهي {time} بتوقيت بغداد",
    "receipt_title": "إيصال",
    "menu_body": {"f": "اختاري من القائمة، أو اكتبي طلبك بكلماتك.", "m": "اختر من القائمة، أو اكتب طلبك بكلماتك."},
    "next_body": {"f": "هل تحتاجين شيئاً آخر؟ اختاري من القائمة.", "m": "هل تحتاج شيئاً آخر؟ اختر من القائمة."},
    "choose_body": {"f": "اختاري إجابتك من هنا:", "m": "اختر إجابتك من هنا:"},
    "card_why_body": {"f": "هل تريدين معرفة ما تحققت منه قبل البطاقة؟", "m": "هل تريد معرفة ما تحققت منه قبل البطاقة؟"},
    "card_why_title": "هذا ما تحققت منه قبل هذه البطاقة:",
    "card_why_closed": {"f": "هذه البطاقة لم تعد مفتوحة، فلن أشرح فحصها. لمعرفة ما تحققت منه في آخر طلب اكتبي «لماذا».",
                        "m": "هذه البطاقة لم تعد مفتوحة، فلن أشرح فحصها. لمعرفة ما تحققت منه في آخر طلب اكتب «لماذا»."},
    "delete_pin": {"f": "احذفي رسالة الرمز من المحادثة عبر «حذف لدى الجميع».",
                   "m": "احذف رسالة الرمز من المحادثة عبر «حذف لدى الجميع»."},
    "pin_by_voice": {
        "f": "لا تقولي الرمز بصوتك، اكتبيه في رسالة. البطاقة ما زالت مفتوحة ولم يُخصم منك شيء.",
        "m": "لا تقل الرمز بصوتك، اكتبه في رسالة. البطاقة ما زالت مفتوحة ولم يُخصم منك شيء.",
    },
    "voice_matched": {"f": "تطابق صوتكِ ✅ اضغطي «نعم، ادفع» ليتم الدفع دون رمز.",
                      "m": "تطابق صوتك ✅ اضغط «نعم، ادفع» ليتم الدفع دون رمز."},
    "voice_fell_back": {"f": "تعذّرت الموافقة بصوتكِ هذه المرة، لذا نحتاج الرمز.",
                        "m": "تعذّرت الموافقة بصوتك هذه المرة، لذا نحتاج الرمز."},
    "voice_enrolled": {"f": "حفظنا صوتكِ: من الآن يمكن لرسالتكِ الصوتية أن توافق على دفعة حتى {cap} دينار دون رمز. "
                            "لحذفه اكتبي «احذف صوتي».",
                       "m": "حفظنا صوتك: من الآن يمكن لرسالتك الصوتية أن توافق على دفعة حتى {cap} دينار دون رمز. "
                            "لحذفه اكتب «احذف صوتي»."},
    "voice_deleted": {"f": "حذفنا صوتكِ ✅ من الآن تحتاج كل دفعة رمزكِ السري.",
                      "m": "حذفنا صوتك ✅ من الآن تحتاج كل دفعة رمزك السري."},
    "voice_none": {"f": "لا يوجد صوت محفوظ لكِ، وكل دفعة تحتاج رمزكِ السري.",
                   "m": "لا يوجد صوت محفوظ لك، وكل دفعة تحتاج رمزك السري."},
}

TEXTS_EN: dict[str, str | dict[str, str]] = {
    "paid_once": "This card was already paid, once only. You won't be charged again. Here's its receipt:",
    "thanks": "You're welcome! If you need anything else just write, for example “what's my balance”.",
    "heard": "I heard: “{text}”",
    "voice_needs_full": "Voice notes work with the full version of the assistant. For now, please type your request.",
    "unsupported_image": "I got a photo, but I only understand text{voice_or}. Type your request, "
                         "for example “pay the generator”.",
    "unsupported_sticker": "Nice sticker! But I only understand text{voice_or}. Type your request, "
                           "for example “pay the generator”.",
    "unsupported_location": "I got the location, but paying doesn't need one. Type your request, "
                            "for example “pay the generator”.",
    "unsupported_other": "I can't open this kind of message. Type your request{voice_send}, "
                         "for example “pay the generator”.",
    "thanks_2": "Anytime! Just write when you need something.",
    "thanks_3": "No problem, I'm here if you need anything.",
    "unsupported_image_2": "I can't read photos, only text{voice_or}. What do you need?",
    "unsupported_sticker_2": "Love it! But I only read text{voice_or}, so tell me what you need.",
    "unsupported_location_2": "Thanks, but paying doesn't need a location. What do you need?",
    "unsupported_other_2": "I couldn't open that message. Type your request{voice_send}.",
    "voice_or": " and voice notes",
    "voice_send": " or send it as a voice note",
    "rate_limited": "You've reached this hour's message limit ({limit} messages). Take a short break and come "
                    "back in an hour. Nothing was taken.",
    "error": "Something went wrong on our side with this message. If you didn't get a receipt, the payment "
             "didn't go through. Try again in a little while.",
    "card_header_transfer": "Confirm transfer",
    "card_header_bill": "Confirm bill payment",
    "card_header_topup": "Confirm top-up",
    "receipt_transfer": "Transfer to: {payee}",
    "receipt_bill": "Bill: {payee}, for {period}",
    "receipt_topup": "Credit top-up for the number ending in {last4}",
    "receipt_amount": "Amount: {amount} dinars",
    "receipt_fee": "Fee: {fee} dinars",
    "receipt_no_fee": "No fee",
    "receipt_total": "Total: {total} dinars",
    "receipt_balance": "Balance after paying: {balance} dinars",
    "receipt_reference": "Reference: {reference}",
    "receipt_time": "Time: {time} Baghdad time",
    "ask_pin": "OK. Now type your PIN as a message here to pay. We only ask for it after you tap “Yes, pay”. Forgot it? Type “forgot PIN”.",
    "pin_still_open": "The card is still open: type your PIN now as a message.",
    "pin_wait": "Take your time, the card is still open. When you're ready, type your PIN as a message.",
    "wrong_pin": "Wrong PIN, nothing was taken. Type it again. After 5 wrong tries in a row it locks for 15 minutes.",
    "tap_first": "Tap “Yes, pay” on the card first, then type your PIN. Nothing was taken. Want to change the amount? Tap “Change”.",
    "pin_no_card": "No PIN is needed now: there's no open payment card. Ask for the payment first, and after “Yes, pay” type your PIN. Only ever type your PIN at that step.",
    "resting": "The service is resting until tomorrow: today's quota is used up. Come back tomorrow. Nothing was taken.",
    "card_footer": "Expires {time} Baghdad time",
    "message_lost": "The service dropped for a moment and lost your last message before reading it. "
                    "Please write it again.",
    "receipt_title": "Receipt",
    "menu_body": "Pick from the menu, or just type your request.",
    "next_body": "Need anything else? Pick from the menu.",
    "choose_body": "Pick your answer here:",
    "card_why_body": "Want to see what I checked before this card?",
    "card_why_title": "Here is what I checked before this card:",
    "card_why_closed": "That card is no longer open, so I won't explain it. To see what I checked on your last request, "
                       "type “why”.",
    "delete_pin": "Please delete your PIN message with “Delete for everyone”.",
    "pin_by_voice": "Please don't say your PIN out loud: type it as a message. The card is still open and nothing was "
                    "taken.",
    "voice_matched": "Your voice matched ✅ Tap “Yes, pay” to pay without the PIN.",
    "voice_fell_back": "Voice approval didn't go through this time, so the PIN is needed.",
    "voice_enrolled": "Your voice is saved: from now on a voice note from you can approve a payment of up to {cap} "
                      "dinars without the PIN. To delete it, type “delete my voice”.",
    "voice_deleted": "Your voice is deleted ✅ From now on every payment needs your PIN.",
    "voice_none": "There's no voice saved for you; every payment needs your PIN.",
}

# Greeting by the time of day in Baghdad: (before noon, after noon).
HELLO = {"iq": ("صباح الخير", "مساء الخير"), "msa": ("صباح الخير", "مساء الخير"),
         "en": ("Good morning", "Good evening")}

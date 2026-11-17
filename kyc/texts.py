"""What the person reads during the identity step, in Iraqi Arabic (the reference), Modern Standard Arabic
and English. Iraqi text writes ج, never the Persian letter.

One key per reason code of kyc.check.REASONS ("kyc_" + the code), plus the question that asks for the photo
and the lines about limits. Each entry is one text or {"f": ..., "m": ...}, picked by the person's gender the
same way as payments.messages.render. Slots: {daily_limit}, {single_max}, {attempts}.
"""

from __future__ import annotations

from payments.messages import format_iqd

ACCEPTED_IQ = "البطاقة الوطنية الموحدة، الجواز، أو بطاقة الإقامة"
ACCEPTED_MSA = "البطاقة الوطنية الموحدة أو جواز السفر أو بطاقة الإقامة"
ACCEPTED_EN = "the unified national card, a passport or a residence card"

TEXTS_IQ: dict[str, str | dict[str, str]] = {
    "kyc_ask": {
        "f": f"حتى تنفتحلج الحدود الكاملة، صوّري هويتج ودزيها هنا: {ACCEPTED_IQ}. خلّي الصورة واضحة وكل الكتابة "
             "مبينة. نقرا الصورة آلياً وما نحفظها عدنا، نحفظ نتيجة الفحص بس.",
        "m": f"حتى تنفتحلك الحدود الكاملة، صوّر هويتك ودزها هنا: {ACCEPTED_IQ}. خلّي الصورة واضحة وكل الكتابة "
             "مبينة. نقرا الصورة آلياً وما نحفظها عدنا، نحفظ نتيجة الفحص بس.",
    },
    "kyc_limits_note": {
        "f": "بدون توثيق الهوية تگدرين تدزين لحد {single_max} دينار بالدفعة الوحدة و{daily_limit} دينار باليوم.",
        "m": "بدون توثيق الهوية تگدر تدز لحد {single_max} دينار بالدفعة الوحدة و{daily_limit} دينار باليوم.",
    },
    "kyc_ok": {
        "f": "تم توثيق هويتج. صار حدج اليومي {daily_limit} دينار، وأكثر مبلغ بالدفعة الوحدة {single_max} دينار.",
        "m": "تم توثيق هويتك. صار حدك اليومي {daily_limit} دينار، وأكثر مبلغ بالدفعة الوحدة {single_max} دينار.",
    },
    # ---- retry: another photo can fix it ----
    "kyc_no_reading": {
        "f": "ما گدرنا نقرا الصورة هسه. دزيها مرة ثانية بعد شوية.",
        "m": "ما گدرنا نقرا الصورة هسه. دزها مرة ثانية بعد شوية.",
    },
    "kyc_not_a_document": {
        "f": f"هاي الصورة مو هوية. دزي صورة {ACCEPTED_IQ}.",
        "m": f"هاي الصورة مو هوية. دز صورة {ACCEPTED_IQ}.",
    },
    "kyc_unreadable": {
        "f": "الصورة مو واضحة وما انقرت الكتابة. صوّريها مرة ثانية بضوه زين وبدون لمعة، وخلّي الهوية كلها داخل الصورة.",
        "m": "الصورة مو واضحة وما انقرت الكتابة. صوّرها مرة ثانية بضوه زين وبدون لمعة، وخلّي الهوية كلها داخل الصورة.",
    },
    "kyc_bad_number": {
        "f": "رقم الهوية ما انقرا صح. صوّريها مرة ثانية من قريب وخلّي الرقم واضح.",
        "m": "رقم الهوية ما انقرا صح. صوّرها مرة ثانية من قريب وخلّي الرقم واضح.",
    },
    "kyc_name_missing": {
        "f": "الاسم على الهوية ما انقرا. صوّريها مرة ثانية وخلّي الاسم واضح.",
        "m": "الاسم على الهوية ما انقرا. صوّرها مرة ثانية وخلّي الاسم واضح.",
    },
    "kyc_bad_birth_date": {
        "f": "تاريخ الميلاد ما انقرا. صوّريها مرة ثانية وخلّي التاريخ واضح.",
        "m": "تاريخ الميلاد ما انقرا. صوّرها مرة ثانية وخلّي التاريخ واضح.",
    },
    "kyc_bad_expiry_date": {
        "f": "تاريخ انتهاء الهوية ما انقرا. صوّريها مرة ثانية وخلّي التاريخ واضح.",
        "m": "تاريخ انتهاء الهوية ما انقرا. صوّرها مرة ثانية وخلّي التاريخ واضح.",
    },
    "kyc_image_too_large": {
        "f": "حجم الصورة كبير (أكثر من 8 ميغابايت). دزيها كصورة عادية، مو كملف.",
        "m": "حجم الصورة كبير (أكثر من 8 ميغابايت). دزها كصورة عادية، مو كملف.",
    },
    "kyc_image_type": {
        "f": "هالملف ما نگدر نفتحه. دزي صورة عادية (JPG أو PNG أو WEBP).",
        "m": "هالملف ما نگدر نفتحه. دز صورة عادية (JPG أو PNG أو WEBP).",
    },
    "kyc_back_needed": {
        "f": "قرينا وجه البطاقة. تاريخ الميلاد والنفاذ مكتوبات على ظهرها: اقلبي البطاقة، صوّري الوجه الثاني "
             "ودزيه هنا.",
        "m": "قرينا وجه البطاقة. تاريخ الميلاد والنفاذ مكتوبات على ظهرها: اقلب البطاقة، صوّر الوجه الثاني "
             "ودزه هنا.",
    },
    "kyc_sides_differ": {
        "f": "الوجهين مو من نفس البطاقة. نبدي من جديد: صوّري وجه البطاقة ودزيه، وبعدين ظهرها.",
        "m": "الوجهين مو من نفس البطاقة. نبدي من جديد: صوّر وجه البطاقة ودزه، وبعدين ظهرها.",
    },
    "kyc_sides_unconfirmed": {
        "f": "ما گدرنا نتأكد إن الظهر من نفس البطاقة، لأن رقمها ما انقرا عليه. موظف راح يراجع طلبج ونرجعلج خبر، "
             "ولحد ذاك الوقت تبقى حدودج الصغيرة.",
        "m": "ما گدرنا نتأكد إن الظهر من نفس البطاقة، لأن رقمها ما انقرا عليه. موظف راح يراجع طلبك ونرجعلك خبر، "
             "ولحد ذاك الوقت تبقى حدودك الصغيرة.",
    },
    "kyc_too_many_attempts": {
        "f": "ما گدرنا نقرا هويتج بعد {attempts} محاولات. موظف راح يراجع طلبج ونرجعلج خبر.",
        "m": "ما گدرنا نقرا هويتك بعد {attempts} محاولات. موظف راح يراجع طلبك ونرجعلك خبر.",
    },
    # ---- refused: this document is not accepted ----
    "kyc_type_not_accepted": {
        "f": f"هالنوع من الوثائق ما نقبله. نقبل {ACCEPTED_IQ}. دزي صورة وحدة منهن.",
        "m": f"هالنوع من الوثائق ما نقبله. نقبل {ACCEPTED_IQ}. دز صورة وحدة منهن.",
    },
    "kyc_specimen": {
        "f": "هاي صورة نموذج، مو هوية حقيقية. صوّري هويتج انتي ودزيها.",
        "m": "هاي صورة نموذج، مو هوية حقيقية. صوّر هويتك انت ودزها.",
    },
    "kyc_under_age": "الخدمة للي عمرهم 18 سنة وفوق. حسب الهوية العمر أقل من 18، فما نگدر نوثّق الحساب.",
    "kyc_age_not_confirmed": {
        "f": "ما گدرنا نتأكد من عمرج من هالصور. حتى ينفتح الحساب نحتاج هوية تبين إن عمرج 18 سنة أو أكثر.",
        "m": "ما گدرنا نتأكد من عمرك من هالصور. حتى ينفتح الحساب نحتاج هوية تبين إن عمرك 18 سنة أو أكثر.",
    },
    "kyc_expired": {
        "f": f"هويتج منتهية. دزي صورة هوية نافذة: {ACCEPTED_IQ}.",
        "m": f"هويتك منتهية. دز صورة هوية نافذة: {ACCEPTED_IQ}.",
    },
    # ---- review: a human looks ----
    "kyc_name_mismatch": {
        "f": "الاسم على الهوية يختلف عن الاسم اللي كتبتيه. موظف راح يراجع طلبج ونرجعلج خبر. لحد ذاك الوقت "
             "تبقى حدودج الصغيرة.",
        "m": "الاسم على الهوية يختلف عن الاسم اللي كتبته. موظف راح يراجع طلبك ونرجعلك خبر. لحد ذاك الوقت "
             "تبقى حدودك الصغيرة.",
    },
    "kyc_screen_or_copy": {
        "f": "الصورة تبين مأخوذة من شاشة أو من نسخة، مو من الهوية نفسها. موظف راح يراجع طلبج ونرجعلج خبر. "
             "إذا الهوية يمج، صوّريها هي ودزيها.",
        "m": "الصورة تبين مأخوذة من شاشة أو من نسخة، مو من الهوية نفسها. موظف راح يراجع طلبك ونرجعلك خبر. "
             "إذا الهوية يمك، صوّرها هي ودزها.",
    },
    # ---- limits of a level (kyc.policy.over) ----
    "kyc_single_max": {
        "f": "أكثر مبلغ بالدفعة الوحدة لحسابج هسه {single_max} دينار. ما انخصم منج شي. حتى يكبر الحد، وثّقي "
             "حسابج بصورة هويتج.",
        "m": "أكثر مبلغ بالدفعة الوحدة لحسابك هسه {single_max} دينار. ما انخصم منك شي. حتى يكبر الحد، وثّق "
             "حسابك بصورة هويتك.",
    },
    "kyc_daily_limit": {
        "f": "هالمبلغ يعبر حدج اليومي ({daily_limit} دينار). ما انخصم منج شي. حتى يكبر الحد، وثّقي حسابج "
             "بصورة هويتج.",
        "m": "هالمبلغ يعبر حدك اليومي ({daily_limit} دينار). ما انخصم منك شي. حتى يكبر الحد، وثّق حسابك "
             "بصورة هويتك.",
    },
}

TEXTS_MSA: dict[str, str | dict[str, str]] = {
    "kyc_ask": {
        "f": f"لفتح الحدود الكاملة لحسابك، صوّري وثيقة هويتك وأرسليها هنا: {ACCEPTED_MSA}. يجب أن تكون الصورة "
             "واضحة والكتابة كلها ظاهرة. تُقرأ الصورة آلياً ولا نحتفظ بها، نحتفظ بنتيجة الفحص فقط.",
        "m": f"لفتح الحدود الكاملة لحسابك، صوّر وثيقة هويتك وأرسلها هنا: {ACCEPTED_MSA}. يجب أن تكون الصورة "
             "واضحة والكتابة كلها ظاهرة. تُقرأ الصورة آلياً ولا نحتفظ بها، نحتفظ بنتيجة الفحص فقط.",
    },
    "kyc_limits_note": {
        "f": "من دون توثيق الهوية يمكنك إرسال {single_max} دينار كحد أقصى في الدفعة الواحدة و{daily_limit} "
             "دينار في اليوم.",
        "m": "من دون توثيق الهوية يمكنك إرسال {single_max} دينار كحد أقصى في الدفعة الواحدة و{daily_limit} "
             "دينار في اليوم.",
    },
    "kyc_ok": "تم توثيق هويتك. أصبح حدك اليومي {daily_limit} دينار، وأقصى مبلغ في الدفعة الواحدة {single_max} دينار.",
    "kyc_no_reading": {
        "f": "لم نتمكن من قراءة الصورة الآن. أرسليها مرة أخرى بعد قليل.",
        "m": "لم نتمكن من قراءة الصورة الآن. أرسلها مرة أخرى بعد قليل.",
    },
    "kyc_not_a_document": {
        "f": f"هذه الصورة ليست وثيقة هوية. أرسلي صورة {ACCEPTED_MSA}.",
        "m": f"هذه الصورة ليست وثيقة هوية. أرسل صورة {ACCEPTED_MSA}.",
    },
    "kyc_unreadable": {
        "f": "الصورة غير واضحة ولم نتمكن من قراءة الكتابة. صوّريها مرة أخرى في إضاءة جيدة ومن دون انعكاس، "
             "واجعلي الوثيقة كلها داخل الصورة.",
        "m": "الصورة غير واضحة ولم نتمكن من قراءة الكتابة. صوّرها مرة أخرى في إضاءة جيدة ومن دون انعكاس، "
             "واجعل الوثيقة كلها داخل الصورة.",
    },
    "kyc_bad_number": {
        "f": "لم نتمكن من قراءة رقم الوثيقة بشكل صحيح. صوّريها مرة أخرى عن قرب ليكون الرقم واضحاً.",
        "m": "لم نتمكن من قراءة رقم الوثيقة بشكل صحيح. صوّرها مرة أخرى عن قرب ليكون الرقم واضحاً.",
    },
    "kyc_name_missing": {
        "f": "لم نتمكن من قراءة الاسم على الوثيقة. صوّريها مرة أخرى ليكون الاسم واضحاً.",
        "m": "لم نتمكن من قراءة الاسم على الوثيقة. صوّرها مرة أخرى ليكون الاسم واضحاً.",
    },
    "kyc_bad_birth_date": {
        "f": "لم نتمكن من قراءة تاريخ الميلاد. صوّريها مرة أخرى ليكون التاريخ واضحاً.",
        "m": "لم نتمكن من قراءة تاريخ الميلاد. صوّرها مرة أخرى ليكون التاريخ واضحاً.",
    },
    "kyc_bad_expiry_date": {
        "f": "لم نتمكن من قراءة تاريخ انتهاء الوثيقة. صوّريها مرة أخرى ليكون التاريخ واضحاً.",
        "m": "لم نتمكن من قراءة تاريخ انتهاء الوثيقة. صوّرها مرة أخرى ليكون التاريخ واضحاً.",
    },
    "kyc_image_too_large": {
        "f": "حجم الصورة كبير (أكثر من 8 ميغابايت). أرسليها كصورة عادية لا كملف.",
        "m": "حجم الصورة كبير (أكثر من 8 ميغابايت). أرسلها كصورة عادية لا كملف.",
    },
    "kyc_image_type": {
        "f": "لا يمكننا فتح هذا الملف. أرسلي صورة عادية (JPG أو PNG أو WEBP).",
        "m": "لا يمكننا فتح هذا الملف. أرسل صورة عادية (JPG أو PNG أو WEBP).",
    },
    "kyc_back_needed": {
        "f": "قرأنا وجه البطاقة. تاريخ الميلاد وتاريخ النفاذ مطبوعان على ظهرها: اقلبي البطاقة وصوّري الوجه الآخر "
             "وأرسليه هنا.",
        "m": "قرأنا وجه البطاقة. تاريخ الميلاد وتاريخ النفاذ مطبوعان على ظهرها: اقلب البطاقة وصوّر الوجه الآخر "
             "وأرسله هنا.",
    },
    "kyc_sides_differ": {
        "f": "الوجهان ليسا من البطاقة نفسها. لنبدأ من جديد: صوّري وجه البطاقة وأرسليه، ثم ظهرها.",
        "m": "الوجهان ليسا من البطاقة نفسها. لنبدأ من جديد: صوّر وجه البطاقة وأرسله، ثم ظهرها.",
    },
    "kyc_sides_unconfirmed": "لم نتمكن من التأكد من أن الظهر من البطاقة نفسها، لأن رقمها لم يُقرأ عليه. سيراجع أحد "
                             "الموظفين طلبك ونخبرك بالنتيجة، وحتى ذلك الحين تبقى حدودك الصغيرة.",
    "kyc_too_many_attempts": "لم نتمكن من قراءة هويتك بعد {attempts} محاولات. سيراجع أحد الموظفين طلبك ونخبرك بالنتيجة.",
    "kyc_type_not_accepted": {
        "f": f"لا نقبل هذا النوع من الوثائق. نقبل {ACCEPTED_MSA}. أرسلي صورة إحداها.",
        "m": f"لا نقبل هذا النوع من الوثائق. نقبل {ACCEPTED_MSA}. أرسل صورة إحداها.",
    },
    "kyc_specimen": {
        "f": "هذه صورة نموذج وليست وثيقة حقيقية. صوّري وثيقة هويتك أنتِ وأرسليها.",
        "m": "هذه صورة نموذج وليست وثيقة حقيقية. صوّر وثيقة هويتك أنت وأرسلها.",
    },
    "kyc_under_age": "الخدمة لمن أتمّ 18 سنة. بحسب الوثيقة العمر أقل من 18 سنة، لذلك لا يمكننا توثيق الحساب.",
    "kyc_age_not_confirmed": "لم نتمكن من التأكد من عمرك من هذه الصور. لفتح الحساب نحتاج وثيقة تُظهر أن عمرك 18 "
                             "سنة أو أكثر.",
    "kyc_expired": {
        "f": f"وثيقة هويتك منتهية الصلاحية. أرسلي صورة وثيقة سارية: {ACCEPTED_MSA}.",
        "m": f"وثيقة هويتك منتهية الصلاحية. أرسل صورة وثيقة سارية: {ACCEPTED_MSA}.",
    },
    "kyc_name_mismatch": {
        "f": "الاسم في الوثيقة يختلف عن الاسم الذي كتبتِه. سيراجع أحد الموظفين طلبك ونخبرك بالنتيجة. حتى ذلك "
             "الحين تبقى حدودك الصغيرة.",
        "m": "الاسم في الوثيقة يختلف عن الاسم الذي كتبته. سيراجع أحد الموظفين طلبك ونخبرك بالنتيجة. حتى ذلك "
             "الحين تبقى حدودك الصغيرة.",
    },
    "kyc_screen_or_copy": {
        "f": "تبدو الصورة مأخوذة من شاشة أو من نسخة، لا من الوثيقة نفسها. سيراجع أحد الموظفين طلبك ونخبرك "
             "بالنتيجة. إن كانت الوثيقة معك، صوّريها هي وأرسليها.",
        "m": "تبدو الصورة مأخوذة من شاشة أو من نسخة، لا من الوثيقة نفسها. سيراجع أحد الموظفين طلبك ونخبرك "
             "بالنتيجة. إن كانت الوثيقة معك، صوّرها هي وأرسلها.",
    },
    "kyc_single_max": {
        "f": "أقصى مبلغ في الدفعة الواحدة لحسابك الآن {single_max} دينار. لم يُخصم منك شيء. لرفع الحد، وثّقي "
             "حسابك بصورة وثيقة هويتك.",
        "m": "أقصى مبلغ في الدفعة الواحدة لحسابك الآن {single_max} دينار. لم يُخصم منك شيء. لرفع الحد، وثّق "
             "حسابك بصورة وثيقة هويتك.",
    },
    "kyc_daily_limit": {
        "f": "هذا المبلغ يتجاوز حدك اليومي ({daily_limit} دينار). لم يُخصم منك شيء. لرفع الحد، وثّقي حسابك "
             "بصورة وثيقة هويتك.",
        "m": "هذا المبلغ يتجاوز حدك اليومي ({daily_limit} دينار). لم يُخصم منك شيء. لرفع الحد، وثّق حسابك "
             "بصورة وثيقة هويتك.",
    },
}

TEXTS_EN: dict[str, str | dict[str, str]] = {
    "kyc_ask": f"To open the full limits, take a photo of your identity document and send it here: {ACCEPTED_EN}. "
               "Make sure the photo is sharp and all the writing shows. The photo is read automatically and we do "
               "not keep it, only the result of the check.",
    "kyc_limits_note": "Without an identity check you can send up to {single_max} dinars in one payment and "
                       "{daily_limit} dinars a day.",
    "kyc_ok": "Your identity is verified. Your daily limit is now {daily_limit} dinars, and the most in one payment "
              "is {single_max} dinars.",
    "kyc_no_reading": "We could not read the photo just now. Please send it again in a moment.",
    "kyc_not_a_document": f"This photo is not an identity document. Please send a photo of {ACCEPTED_EN}.",
    "kyc_unreadable": "The photo is not clear and the writing could not be read. Please take it again in good "
                      "light, without glare, with the whole document inside the photo.",
    "kyc_bad_number": "The document number could not be read correctly. Please take the photo again from closer, "
                      "with the number clear.",
    "kyc_name_missing": "The name on the document could not be read. Please take the photo again with the name clear.",
    "kyc_bad_birth_date": "The date of birth could not be read. Please take the photo again with the date clear.",
    "kyc_bad_expiry_date": "The expiry date could not be read. Please take the photo again with the date clear.",
    "kyc_image_too_large": "The photo is too large (over 8 MB). Please send it as a normal photo, not as a file.",
    "kyc_image_type": "We cannot open this file. Please send a normal photo (JPG, PNG or WEBP).",
    "kyc_back_needed": "We read the front of the card. The date of birth and the expiry are printed on its back: "
                       "please turn the card over, take a photo of the back and send it here.",
    "kyc_sides_differ": "The two sides are not from the same card. Let us start again: please send a photo of the "
                        "front of the card, then of its back.",
    "kyc_sides_unconfirmed": "We could not confirm that the back is from the same card, because its number could "
                             "not be read on it. A member of staff will review your request and we will let you "
                             "know. Until then your small limits stay.",
    "kyc_too_many_attempts": "We could not read your document after {attempts} tries. A member of staff will review "
                             "your request and we will let you know.",
    "kyc_type_not_accepted": f"We do not accept this kind of document. We accept {ACCEPTED_EN}. Please send a photo "
                             "of one of them.",
    "kyc_specimen": "This is a specimen, not a real document. Please take a photo of your own identity document "
                    "and send it.",
    "kyc_under_age": "The service is for people aged 18 and over. The document shows an age under 18, so we cannot "
                     "verify the account.",
    "kyc_age_not_confirmed": "We could not confirm your age from these photos. To open an account we need a "
                             "document that shows you are 18 or over.",
    "kyc_expired": f"Your document has expired. Please send a photo of a valid one: {ACCEPTED_EN}.",
    "kyc_name_mismatch": "The name on the document is different from the name you typed. A member of staff will "
                         "review your request and we will let you know. Until then your small limits stay.",
    "kyc_screen_or_copy": "The photo looks like it was taken from a screen or a copy, not from the document itself. "
                          "A member of staff will review your request and we will let you know. If you have the "
                          "document with you, take a photo of it and send that.",
    "kyc_single_max": "The most your account can send in one payment right now is {single_max} dinars. Nothing was "
                      "taken from your balance. To raise the limit, verify your account with a photo of your "
                      "identity document.",
    "kyc_daily_limit": "This amount passes your daily limit ({daily_limit} dinars). Nothing was taken from your "
                       "balance. To raise the limit, verify your account with a photo of your identity document.",
}

TEXTS: dict[str, dict[str, str | dict[str, str]]] = {"iq": TEXTS_IQ, "msa": TEXTS_MSA, "en": TEXTS_EN}


def render(key: str, gender: str = "f", lang: str | None = None, **slots) -> str:
    """Fill a text in `lang` ('iq' | 'msa' | 'en'; anything else is Iraqi). Integers get thousands separators."""
    template = TEXTS.get(lang or "iq", TEXTS_IQ)[key]
    if isinstance(template, dict):
        template = template["m" if gender == "m" else "f"]
    values = {name: format_iqd(value) if isinstance(value, int) and not isinstance(value, bool) else value
              for name, value in slots.items()}
    return template.format(**values)

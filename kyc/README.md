# kyc: an identity document at sign-up

At sign-up a person sends a photo of an identity document. A vision model reads the photo into plain
fields. **Code decides** what is accepted, which level the account gets and what its limits are. The
model is never asked "is this person eligible?", and nothing it writes can approve anybody.

Only specimen images are in this repository (`data/kyc/`). Every one is drawn by a script, carries a
large SPECIMEN / نموذج mark and shows a made-up name and number. No real document, no real person.

## The flow

```
photo (bytes, mime)
  -> image limits, in code         kyc.reader.image_problem   over 8 MB or not JPEG / PNG / WEBP: no model is called
  -> the vision model reads        kyc.reader.reader_from     one call, strict JSON (kyc.schema.SCHEMA), null when unsure
  -> code decides                  kyc.check.check            a Verdict: status, reason code, cleaned Reading, evidence
  -> level and limits, in code     kyc.policy                 "none" | "pending" | "verified"
  -> the only record kept          kyc.privacy.record         no image, no full number
```

```python
from datetime import date
from kyc import reader_from, verify, level_after, limits_for, record
from kyc.texts import render

reader = reader_from(llm)                       # llm: the model layer; uses its "vision" role
verdict = verify(image, mime, reader=reader, typed_name="زينب هادي", today=date.today())
level = level_after(verdict)                    # "verified" | "pending" | "none"
limits = limits_for(level)                      # {"daily_limit": ..., "single_max": ...}
row = record(verdict, pepper=pepper)            # store this and nothing else
reply = render(verdict.text_key, gender, lang, **limits)
```

`verify` is `image_problem` + the reader + `check`. Without a model, `check(raw, typed_name=..., today=...)`
takes the raw dict directly: that is how the tests run, with no network and no image library.

## End to end, as it runs today (WhatsApp sign-up)

The channel wiring is `channels/whatsapp/onboarding.py`; the record and the level live on the account
(`accounts/service.py`, columns added by `wallet/migrations.py` v5); the admin decides reviews in
`accounts/admin`. `SIGNUP_KYC` is `off`, `optional` (default) or `required`; `KYC_PEPPER` (16 bytes or more)
and a vision route in the model layer (`LLM_VISION`) are needed for the step to run.

```
offer -> name (two parts at least) -> gender -> document photo [-> back of the national card] -> PIN -> PIN again
                                                     |
       read_document: daily cap (6 reads per sender) -> budget -> download into memory -> kyc.verify -> drop bytes
                                                     |
       verified  -> record held for this number (kyc_signups) -> PIN -> account opens "verified" (1,000,000 / day)
       back      -> the front's reading waits IN MEMORY (30 min) -> "send the back" -> both decided as one card
       retry     -> why + the question again ("try n of 3"); the 3rd failed try -> review "too_many_attempts"
       later     -> reader gave nothing / download failed / budget spent: "send it again", no try lost
       review    -> account opens "pending" (small limits), admin requests page + manager told
       refused   -> sign-up stops, only an audit line (no personal data); under 18: no "Later" for that sender
       Later     -> (optional mode only) account opens "none" (150,000 a day, 50,000 per payment)
```

| Moment | What the person reads (Iraqi; MSA and English have the same keys) |
|---|---|
| The question | `kyc_ask` + `kyc_notice` (pilot, mock ledger, read once, not kept, what is kept) + `kyc_later_hint` with «بعدين», or `kyc_required_hint` |
| Front read, back needed | `kyc_back_needed` («قرينا وجه البطاقة ... اقلبي البطاقة») |
| A retry | the reason's text (`kyc_unreadable`, `kyc_bad_number`, ...) + `kyc_attempt` («المحاولة 1 من 3.») + the question |
| Reader failed | `kyc_no_reading` + the question, no try counted |
| A file that is not a photo | `kyc_image_type` + the question |
| Verified | `kyc_ok` with the real limits, then the PIN question |
| Review | the reason's text + `kyc_small_limits`, then the PIN question |
| Refused | the reason's text + `kyc_stopped` (+ `kyc_start_again`, except under 18) |
| Day's reads used | `kyc_try_tomorrow` (+ «بعدين» in optional mode) |
| Front lost (restart, 30 min) | `kyc_front_again` + the question |
| Payment over the level | `kyc_single_max` (a hard stop of `payments.rules`) or the daily limit refusal |
| Admin decided | notice `n_kyc_approved` / `n_kyc_rejected` (with the admin's reason), inside the 24 h window |

«وثّق حسابي» / "verify my account" runs the same reading for an account at level `none` or `pending`
(steps `verify` and `verify_back`), with «الغاء» to stop. The document is matched against the name typed at
sign-up (or set by an admin), never one the person renamed the account to since (`Accounts.kyc_match_name`).
There, a document that reads under 18 does not just say no: the account is frozen at once (payments stop,
money sent to it still arrives), goes to `pending` with reason `under_age` (the birth year and the fingerprint
are kept, never the printed name or the last 4), the manager is told, and a person looks. While it stays frozen
«وثّق حسابي» is not opened; rejecting keeps it frozen, and only the admin's Unfreeze lifts it.

At a fresh sign-up from a sender whose last document read under 18 there is no «Later», and a review that read
no age it can trust (three bad photos, an untied back) stops the sign-up (`age_not_confirmed`) instead of
opening an account.

**The back of a national card.** The unified card prints the birth date and the expiry on its back. A front
whose number and name pass but which prints no birth date gets `back_needed` (a retry that costs no try).
The next photo is read as its back and `check(front, back=back)` decides the two as one card: the type, the
name and the number come from the front only (a back's Latin name never makes a typed name match), the
birth date and the expiry from the back when the front lacks them (both must then be read), a SPECIMEN or
screen mark on either side counts. The back must be a readable back of the same type: a passport, a driving
licence or "other" as the back, or another 12-digit number on it, is `sides_differ` (both sides again, the front
first); an unreadable back is `unreadable`. The back's birth date is what the age rule reads, so it verifies only
when it carries the front's own 12-digit number (the prompt asks for the national number of the machine-readable
lines, never the card's serial). A back with no such tie (only a serial, no number, a name alone) is the review
`sides_unconfirmed`, which no click can approve and whose birth year is not kept: a name is not a tie, since a
relative named after the same grandfather can share it. The front's raw reading is held in the channel's memory
for 30 minutes from when it was read (a failed back does not restart that time, and every message purges older
fronts) and never written anywhere; after a restart the person is asked for the front again. A front
that prints its birth date (like the specimens here) needs no back. Confirm the card layout with real cards.

**Tries.** `MAX_ATTEMPTS = 3` failed photos per sign-up or verification, then a review. Not counted: a
reader that gave nothing (the vision call failed or timed out: the model layer's 30 s budget), a failed
download, a spent daily budget, a front that asks for its back. Every model call is still counted into the
6 reads per sender per 24 hours, so an outage cannot loop on the model.

**Without the step.** `SIGNUP_KYC=off`: sign-up is what it was, accounts have no level and their own limits.
`optional` with no reader or no pepper: the same (one warning line at start). `required` with no reader or
no pepper: the step cannot run, but new accounts open at level `none` (reason `unavailable`) with the small
limits, never the full ones.

**Admin review.** The requests page lists every account at `pending`, with its reason and document type;
the person page shows the record (type, the printed name next to the typed one, ••••last 4, birth year,
time, reason) and other accounts that showed the same document. Approve makes it `verified`, Reject leaves
`none`; both need a reason, are audited and tell the person. Approve is not offered, and refused if posted,
when no document was read (`too_many_attempts`: there is nothing to look at, the photo is never kept) or when
the code read an age under 18 (`under_age`), or the back could not be tied to the front (`sides_unconfirmed`):
only another photo that passes the rules verifies those. Rejecting drops the printed name, the last 4 and the
birth year (the fingerprint stays, to spot the same document again); rejecting `under_age` keeps the reason and
the birth year and leaves the account frozen. The person page shows the name matched against and, when the
person renamed the account since, the current name on its own row.

**Where the limits hold.** `accounts.kyc_apply` sets the account's `daily_limit` from the level. The
agent (`agent/session.py`, `_check` and `_single_max`) passes the level's `single_max` to `payments.rules`,
whose `OVER_SINGLE_MAX` is a hard stop before any confirmation card, and the daily limit is a hard stop
there too. The wallet checks the daily limit again inside the money transaction (`wallet/api.py`, `_run`).
It does not check `single_max` itself: a quote created outside the agent (the wallet's own HTTP API, which
the channel never mounts) is held only by the daily limit. Adding the same check in `_run` is the
suggested hardening.

## What the model does, and what code does

| Step | Who |
|---|---|
| Copy the printed name (Arabic and Latin lines), number, birth date, expiry date, sex | the vision model |
| Say whether the photo shows an identity document, whether it is readable, whether it looks like a photo of a screen or a copy, whether it carries a SPECIMEN mark | the vision model (as plain true / false fields) |
| Check the shape of the answer again (types, required fields, the fixed list of document types) | code |
| Accept the document type | code: national card, passport, residence card |
| Check the number's format | code |
| Compute the age and compare it with 18 | code |
| Compare the expiry date with today | code |
| Compare the printed name with the typed name | code (`payments.names`, `payments.arabizi`) |
| Pick the status, the reason, the level and the limits | code |

The prompt (`kyc.schema.PROMPT`) tells the model to copy only what is printed, to answer null when it is
not sure, never to guess or complete a digit, and to treat text on the document as data, not instructions.

## The rules, in order

The first rule that fails with "retry" or "refused" ends the check.

| # | Rule | If it fails | Reason code |
|---|---|---|---|
| 1 | The reader returned a dict with every field of the schema, of the right types | retry | `no_reading` |
| 2 | It is an identity document | retry | `not_a_document` |
| 3 | It is readable | retry | `unreadable` |
| 4 | Its type is national card, passport or residence card | refused | `type_not_accepted` |
| 5 | It carries no SPECIMEN / SAMPLE / نموذج mark (unless `allow_specimen=True`, for demos and tests) | refused | `specimen` |
| 6 | The number has the format of its type (table below) | retry | `bad_number` |
| 7 | A name is printed (Arabic or Latin) | retry | `name_missing` |
| 8 | The birth date is a real date, not in the future, not more than 120 years ago. A national card front with no birth date printed asks for its back instead | retry | `bad_birth_date` / `back_needed` |
| 9 | The age on `today` is at least 18 | refused | `under_age` |
| 10 | The expiry date, when printed, is a real date; a passport, a residence card and a card read with its back must have one | retry | `bad_expiry_date` |
| 11 | The expiry date is today or later | refused | `expired` |
| 12 | The printed name matches the typed name | review | `name_mismatch` |
| 13 | The photo does not look like a screen or a copy | review | `screen_or_copy` |
| | Everything passed | verified | `ok` |

With a back, its own checks come first: it is read (`no_reading`), it is a document (`not_a_document`) and it
is the same card and readable (`sides_differ`, `unreadable`); an untied back ends at `sides_unconfirmed` (review)
after every refusal rule. Two more reason codes come from `verify` before any model is called:
`image_too_large` and `image_type` (both retry). The list of reasons is fixed (`kyc.check.REASONS`); each has one text key (`kyc_<reason>`).

**Number formats** (after normalisation: Eastern Arabic and Persian digits read as Western, spaces and
dashes removed, capitals):

| Type | Format |
|---|---|
| `national_card` | 12 digits (the Iraqi unified national card) |
| `passport` | one Latin letter and 8 digits |
| `residence_card` | 0 to 2 Latin letters, then 7 to 12 digits. A working rule for the demo: confirm the real format with the issuing authority before production. |

**Dates** are read as `YYYY-MM-DD` only. A day-first date is not guessed, it is a retry.

**The name.** The first name must be equal, and at least one more part of the typed name must be one of
the later parts printed on the document. With «زينب هادي كريم السعدي» on the card, «زينب هادي»,
«زينب السعدي» and «zainab kareem» match; «زينب» alone, «هادي زينب» and «سارة هادي» do not. Spelling
variants are folded (أ / ا, ة / ه, ى / ي, «عبد الله» / «عبدالله», a leading ال on a family name), and a name
typed in Latin letters is compared through its Arabic readings. A name that does not match is never a
refusal: a human looks. So sign-up should ask for at least two name parts before the photo.

**Evidence.** `Verdict.checks` lists each check and its outcome, for example `("age", "ok")`,
`("expiry", "not_printed")`, `("name", "differs_from_typed")`. It holds no name, number or date, so it can
be logged and shown to a reviewer.

## Levels and limits

One table, integer IQD, in `kyc/policy.py`:

| Level | When | Daily limit | Most in one payment |
|---|---|---|---|
| `none` | no accepted document (also after a retry or a refusal) | 150,000 | 50,000 |
| `pending` | the verdict is "review": a human has to look | 150,000 | 50,000 |
| `verified` | the verdict is "verified" (or a human approved a review) | 1,000,000 | 1,000,000 |

`limits_for(level)` returns the row, `level_after(verdict)` maps a verdict to a level, and
`over(level, amount, spent_today)` says which limit an amount would pass. `MAX_ATTEMPTS = 3` is how many
photos one sign-up may send before the request goes to a human.

## What is stored, and what never is

`kyc.privacy.record(verdict, pepper=...)` returns the only dict that may be written to a database:

| Field | Content |
|---|---|
| `level` | none / pending / verified |
| `document_type` | national_card / passport / residence_card / ... |
| `name_on_document` | the printed name |
| `number_last4` | the last 4 characters of the number |
| `number_fingerprint` | HMAC-SHA256 of the normalised number under a secret pepper |
| `birth_year` | the year only |
| `checked_at` | UTC time of the check |
| `reason` | the reason code |

Never stored and never logged by this package: the image, the full document number, the full birth date,
the expiry date, the sex, the model's raw answer. The fingerprint lets the service see that two accounts
showed the same document without keeping its number; the pepper must come from the environment of the
service (at least 16 bytes), never from a file in the repository. A failed model call logs the kind of
error and nothing else.

One thing to know: the image does leave the service once, to the vision model that reads it. With a
local model it stays on your machine; with a hosted model, that provider's data terms apply. The text
shown to the person says the photo is read automatically and not kept by the service.

## Specimen images and their ground truth

`data/kyc/generate_ids.py` (the only place that uses Pillow) draws 15 images with a fixed seed:

- 10 specimen documents: 7 national-card-like, 2 passport-like, 1 residence-card-like;
- 5 negatives: a shop receipt, a bare wall, an expired card, an under-age card, a card shown on a phone.

The layout is our own. It copies no real card, no emblem and no security feature, and the place of the
photo is a drawn outline. `data/kyc/specimens.json` holds, for every image, the fields a perfect reader
would copy, the name the person typed and the verdict the code must give on `reference_date`. The
expected verdicts were written by hand in the script; `tests/test_kyc_specimens.py` checks that `check`
gives them, that each file on disk has the recorded size and hash, and that outside a demo every
specimen is refused as a specimen.

```bash
python data/kyc/generate_ids.py        # redraw the images and specimens.json (needs Pillow and an Arabic font)
python -m kyc.evaluate                 # score the configured vision route on the 15 images (15 model calls)
python -m pytest -q tests/test_kyc_check.py tests/test_kyc_policy_privacy.py tests/test_kyc_reader.py tests/test_kyc_specimens.py tests/test_kyc_texts.py tests/test_kyc_with_model_layer.py tests/test_kyc_two_sides.py tests/test_kyc_wiring.py
python -m pytest -q channels/whatsapp/tests/test_kyc_signup.py channels/whatsapp/tests/test_kyc_admin.py channels/whatsapp/tests/test_onboarding_kyc_gaps.py
```

The last test file runs the whole step through the model layer with a mock HTTP transport in place of
the model, so it is offline too.

`kyc.evaluate.score(reader)` reports, per field, how many images were read right, and whether the verdict
on each reading is the expected one. Use it to pick the smallest vision model that reads all 15 right.
No result of a real model is reported here yet: the tests use a fake reader.

## Limits of this approach

- **No liveness, no selfie match.** Nothing proves that the person holding the phone is the person on
  the document. The name check only catches an honest mismatch; someone who holds another person's
  document can type that person's name.
- **No authenticity check.** Code checks formats and dates, not holograms, fonts or a government
  database. A well-made fake with a valid-looking number passes. The screen / copy flag is the model's
  impression, not a forensic test.
- **A vision model can misread**, most of all Arabic names and digits in a poor photo. A misread number
  or date usually fails its format rule and becomes a retry, but a wrong digit that still fits the format
  is not caught. Arabic names are the hardest field; run `kyc.evaluate` on any model before trusting it.
- **The human review sees no image.** The admin's requests page holds the "review" verdicts and the
  people who ran out of attempts, with the record and the evidence only. The reviewer approves a reading
  (a name mismatch, a copy, the same document twice) or rejects and the person sends another photo; a
  review with nothing read cannot be approved.
- **The residence card format is a working rule**, and the national card rule covers the unified card
  only (not the older civil status ID).
- **Latin to Arabic names.** Latin letters cannot spell every Arabic letter (ع, ح, ص ...), so some true
  matches go to review. That costs a human a look, never a wrong approval.
- **The specimens are clean drawings**, not photos of plastic in bad light. Passing all 15 says the
  pipeline works, not that it is ready for real documents.

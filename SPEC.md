# Build spec: Iraqi Bill Pay & Transfer Agent (open components)

Agentic AI Challenge Iraq, Topic 04. A user types or speaks one sentence in Iraqi Arabic
("ادفع المولدة", "دز لأحمد خمسين") and the agent turns it into one confirmed, never-doubled payment
against a **mock** wallet.

Guiding rule: **the conversational core understands and talks; deterministic code touches the money.**

## What is open, what is closed

| Part | Where | Status |
|---|---|---|
| Conversational core: language understanding, Iraqi-dialect phrasing, voice | `core/` interface only | **Closed source (proprietary).** Not in this repo. |
| Rule-based stand-in for the core (tests and offline demo) | `core/stub.py` | Open |
| Mock wallet API (users, contacts, billers, bills, quotes, idempotent execution, fault injection) | `wallet/` | Open |
| Payment safety layer (amount parser, name resolver, rules, quote card, fixed messages) | `payments/` | Open |
| Orchestrator (turn handling, clarify, confirm, execute, report) | `agent/` | Open |
| Synthetic data + test cases + evaluation | `data/`, `eval/` | Open |

Hard rules for every file in this repo:
- The closed core's internals are not part of this repo; only its interface is.
- No API keys. `.env` is git-ignored. Synthetic data only (fictional names and numbers).
- Everything a user sees exists in Iraqi Arabic (the reference), Modern Standard Arabic and English; the
  reply follows the language of each message (`payments/language.py`). Write **ج, never چ** (محفظتج, إلج, بيج).
- Python 3.12+, standard library + FastAPI/uvicorn/pydantic/httpx/pytest only. Integers for money (IQD).

## Data model (SQLite, `wallet/db.py`)

- `users(id TEXT PK, name, phone, balance INT, daily_limit INT, pin_hash, gender 'f'|'m',
   pin_failures INT, pin_locked_until NULL)` — 5 wrong PINs in a row lock the PIN for 15 minutes
- `contacts(user_id, contact_id PK, display_name, phone, alias NULL)` — what the user saved, often no surname
- `billers(id PK, kind 'electricity'|'generator'|'internet'|'water'|'topup', name_ar, name_en)`
- `bill_accounts(id PK, user_id, biller_id, account_no, holder_name, label_ar)` — saved once
- `bills(id PK, account_id, period 'YYYY-MM', amount_due INT, status 'unpaid'|'paid')`
- `quotes(id PK, user_id, kind 'transfer'|'bill'|'topup', target_json, amount INT, fee INT,
   created_at, expires_at, state, result_json NULL)`
   states: QUOTED → EXECUTING → SUCCEEDED | FAILED | UNKNOWN, plus EXPIRED, CANCELLED.
   Transitions only by compare-and-set (`UPDATE ... WHERE id=? AND state=?`).
- `transactions(id PK, quote_id UNIQUE, user_id, kind, target_json, amount, fee, status, created_at)`

### Real accounts (schema v2 to v4, `wallet/migrations.py`)

`PRAGMA user_version` tracks the schema; each migration runs in one transaction after a snapshot of a file
database that already holds users. v2 adds to `users` the canonical WhatsApp number `phone_e164` (unique),
`status` (invited / active / frozen / closed), `pin_state` (unset / set / blocked), `language`, `source`
and times; to contacts, bill accounts and bills a phone, a source and a soft delete; to transactions the
receiving user, the counterparty number, a hold id and a reason. v3 adds `holds` (money waiting for a
number with no account), `admin_ops` (idempotent admin money by op id), `outbox` (notices) and `audit_log`.
v4 sets the mock biller ranges. Ledger accounts gain `hold:<id>` and `cash` (admin credits and debits).
A transfer to an account credits it in the same transaction; to a number with no account it credits a
hold, claimed at sign-up or refunded (fee included) after `HOLD_DAYS`. The audit also checks that each
open hold holds its amount, each settled one nothing, and each admin op has its transaction. The synthetic
seed (web chat, evaluation) loads unchanged: seed users keep the old behaviour (no holds, no notices).

## Wallet API (`wallet/api.py`, FastAPI app `app`)

- `GET  /users/{uid}` → profile + balance
- `GET  /users/{uid}/contacts` · `GET /users/{uid}/bill-accounts` · `GET /bills?account_id=`
- `GET  /users/{uid}/transactions?since_minutes=` (includes pending/UNKNOWN)
- `POST /quotes` `{user_id, kind, target, amount}` → quote (server computes fee and expiry, 5 min)
- `POST /quotes/{qid}/execute` `{pin}` header `Idempotency-Key: <qid>` → result. ONE SQLite transaction:
  CAS QUOTED→EXECUTING; reject if expired; reject if the PIN is locked (423 `pin_locked`); verify PIN
  (Eastern digits read as Western; a wrong one is 403 `wrong_pin`, counted in its own transaction,
  and a right one resets the count); conditional debit
  (`UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?`); daily-limit check; insert
  transaction (UNIQUE quote_id); credit payee / mark bill paid (already paid → reject, nothing debited);
  store result; SUCCEEDED. Any replay of the same quote returns the stored result, never a second debit.
- `GET  /quotes/{qid}` → state + result (used by the reconciler) · `POST /quotes/{qid}/cancel`
- `POST /admin/faults` `{mode, times?}`: `none | timeout_before_commit | drop_after_commit | error_500 |
  biller_down | biller_decline_after_debit` (the last one reverses the debit automatically).
  `times`: fire N times, then back to `none`.
- `GET  /admin/audit` → invariants: ≤1 debit per quote, no negative balance, money conserved.
- Fees (synthetic): transfer 250 IQD flat, bill 0, top-up 0.

## Payment layer (`payments/`)

- `amounts.parse(text) -> Amount(value_iqd: int|None, currency 'IQD'|'USD'|None, needs_question: bool, reason: str)`
  Iraqi conventions: a bare number under 1,000 means thousands of dinars (خمسين → 50,000;
  مية وخمسين → 150,000; ميتين → 200,000; خمسمية → 500,000); ألف/الف/آلاف/تالاف (خمس تالاف → 5,000);
  مليون; ربع مليون → 250,000; نص مليون → 500,000; مليون ونص → 1,500,000; Western and Eastern digits
  (٥٠٬٠٠٠, 50,000, 50000, 50 الف); teens like خمسطعش (15 → 15,000), تلطعش. Dollar words (ورقة, ورقتين,
  دفتر, دولار, $) → currency USD, value None, needs_question True ("شكد بالدينار؟") — never convert.
  Anything unparsable → needs_question True. Also a question: a hedge before or after the number
  (حوالي خمسين, ميتين بحدود), and a tail under 100 after ألف (خمسين الف وعشرين: 50,020 or 70,000?).
  `amounts.alternatives(text)` finds two amounts offered as a choice (خمسين او ستين); the orchestrator
  asks which one.
- `periods.months_named(text) -> set[int]`: months the user names (حزيران, تشرين الأول, شهر ستة, شهر 6).
  تشرين or كانون alone gives two months, and the orchestrator asks.
- `names.normalize(s)`: أ/إ/آ→ا, ة→ه, ى→ي, گ/ك→ك, ق may be written ك/گ (match both), چ→ج, collapse
  "عبد ال" spacing, strip tatweel/diacritics. `names.resolve(mention, contacts, aliases) -> list[Candidate]`
  with scores. Nicknames are one-to-many (حمودي → محمد/أحمد/محمود; علاوي → علي; حسوني → حسين;
  عبوسي → عباس). Relation words (ابني, بنتي, اخوي, اختي, امي, ابوي, زوجي, مرتي) resolve only through a
  saved alias; otherwise they need a question.
- `rules.check(request, wallet_view) -> Checks(hard_stops: list, soft_flags: list)`. Hard: insufficient
  balance+fee, over daily limit, non-positive amount, non-IQD. Soft: first-time payee, same payee+amount
  within 10 min (pending/UNKNOWN included), amount > 3× the user's largest past amount to that payee.
- `quote.render_card(quote, view) -> Card(lines: list[str], flags: list[str], buttons)` — deterministic
  template, Iraqi Arabic, gender-aware (تدزين/تدز). Buttons: "إي، ادفع" · "غيّر" · "لا، بطّلت".
  Bill card shows biller, account holder name, period, amount due. Transfer card shows name + last 4 digits.
- `messages.py`: every failure message says what failed, whether money moved ("ما انخصم منج شي"), next step.
  UNKNOWN: "لهسه ما نعرف إذا انحوّلت، لا تعيديها. راح نتأكد ونرجعلج خبر."

## Core interface (`core/interface.py`)

`understand(text, context) -> Intent(action 'pay_bill'|'transfer'|'topup'|'balance'|'cancel'|'unsupported'|'chat'|
'history'|'add_contact',
payee_mention, biller_mention, amount_text, currency, operator,
period ('current'|'previous'|'YYYY-MM'|'MM'|None), extra: list[Intent])` and
`phrase(question_key, slots) -> str` (slots carry `gender` and `lang`). Optional: `transcribe(audio, mime,
context) -> str` for voice notes (`VoiceCore`) and `speak(text, language, context) -> (audio, mime) | None`
for voice replies (`SpeechCore`, used by the channels under `VOICE_REPLY_MODE`), and `hear(audio, mime,
context) -> (transcript, Intent)` (`HearingCore`), preferred for voice notes. `context` carries the open
question, the language, the phone placeholders (the core never sees a phone number: «هاتف_ا» is the payee
mention), the user's vocabulary (saved names and aliases, bill labels, billers; no numbers), the last few
turns (never a PIN) and `source` ('voice' for a transcript). `Intent.language` is a
language hint the core sets only when it is sure. The core is chosen at run time by `core.loader.load_core()`:
`BILLPAY_CORE="module:Class"` (plus `BILLPAY_CORE_PATH`), else `core.stub.StubCore`. The orchestrator must verify that `amount_text` and `payee_mention`
appear in the user's own words (after normalisation); otherwise it asks. The closed core implements this
interface; `core/stub.py` is a keyword-based stand-in good enough for tests and the offline demo.

## Orchestrator (`agent/`)

`Session.handle_text(user_id, text) -> Reply`, `Session.handle_button(user_id, quote_id, button, pin=None) -> Reply`,
`Session.handle_voice(user_id, audio, mime="audio/ogg") -> Reply` (the transcript is in `Reply.heard`) and
`Session.reconcile(user_id) -> Reply`. A bill is paid in full: when the user states another amount or a part, the agent asks for a yes to the full amount before any card; a month
the user names picks that month's bill (or a question when it fits two).
One open card per user; any new text voids it. Two-in-one → first card, then offer the second.
Typed "إي" never executes — only the button event with the quote id. Timeout/5xx on execute → UNKNOWN,
retry with the SAME key, then poll `GET /quotes/{qid}`; never report "failed" until the API says so.

## Test cases (`data/cases/*.jsonl`)

One JSON object per line: `{id, type, user_id, messages: [..], expected: {outcome: EXECUTE|ASK|REFUSE,
fields?: {...}, ask_field?: str, refuse_reason?: str}}`. Types: clear_bill, clear_transfer, topup,
ambiguous_biller, two_same_name, nickname, relation_word, dollars, raseed_vs_flous, two_in_one,
low_balance, over_limit, unknown_payee, already_paid, cancel, arabizi, eastern_digits, unsupported, pressure.

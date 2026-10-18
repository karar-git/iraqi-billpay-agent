# Build spec: Iraqi Bill Pay & Transfer Agent (open components)

Agentic AI Challenge Iraq, Topic 04. A user types or speaks one sentence in Iraqi Arabic
("ادفع المولدة", "دز لأحمد خمسين") and the agent turns it into one confirmed, never-doubled payment
against a **mock** wallet.

Guiding rule: **the language model understands and talks; deterministic code touches the money.**

## What is open, what is closed

| Part | Where | Status |
|---|---|---|
| Conversational core: language understanding, Iraqi-dialect phrasing, voice | `core/` interface only | **Closed source (proprietary).** Never describe its models, prompts or internals anywhere in this repo. |
| Rule-based stand-in for the core (tests and offline demo) | `core/stub.py` | Open |
| Mock wallet API (users, contacts, billers, bills, quotes, idempotent execution, fault injection) | `wallet/` | Open |
| Payment safety layer (amount parser, name resolver, rules, quote card, fixed messages) | `payments/` | Open |
| Orchestrator (turn handling, clarify, confirm, execute, report) | `agent/` | Open |
| Synthetic data + test cases + evaluation | `data/`, `eval/` | Open |

Hard rules for every file in this repo:
- No language-model or AI-provider names in code, comments, docs or data, except the single disclosure
  line for the AI coding assistant in README.
- No prompts. No API keys. `.env` is git-ignored. Synthetic data only (fictional names and numbers).
- Iraqi Arabic for everything a user sees. Write **ج, never چ** (محفظتج, إلج, بيج).
- Python 3.12+, standard library + FastAPI/pydantic/httpx/pytest only. Integers for money (IQD).

## Data model (SQLite, `wallet/db.py`)

- `users(id TEXT PK, name, phone, balance INT, daily_limit INT, pin_hash, gender 'f'|'m')`
- `contacts(user_id, contact_id PK, display_name, phone, alias NULL)` — what the user saved, often no surname
- `billers(id PK, kind 'electricity'|'generator'|'internet'|'water'|'topup', name_ar, name_en)`
- `bill_accounts(id PK, user_id, biller_id, account_no, holder_name, label_ar)` — saved once
- `bills(id PK, account_id, period 'YYYY-MM', amount_due INT, status 'unpaid'|'paid')`
- `quotes(id PK, user_id, kind 'transfer'|'bill'|'topup', target_json, amount INT, fee INT,
   created_at, expires_at, state, result_json NULL)`
   states: QUOTED → EXECUTING → SUCCEEDED | FAILED | UNKNOWN, plus EXPIRED, CANCELLED.
   Transitions only by compare-and-set (`UPDATE ... WHERE id=? AND state=?`).
- `transactions(id PK, quote_id UNIQUE, user_id, kind, target_json, amount, fee, status, created_at)`

## Wallet API (`wallet/api.py`, FastAPI app `app`)

- `GET  /users/{uid}` → profile + balance
- `GET  /users/{uid}/contacts` · `GET /users/{uid}/bill-accounts` · `GET /bills?account_id=`
- `GET  /users/{uid}/transactions?since_minutes=` (includes pending/UNKNOWN)
- `POST /quotes` `{user_id, kind, target, amount}` → quote (server computes fee and expiry, 5 min)
- `POST /quotes/{qid}/execute` `{pin}` header `Idempotency-Key: <qid>` → result. ONE SQLite transaction:
  CAS QUOTED→EXECUTING; reject if expired; verify PIN; conditional debit
  (`UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?`); daily-limit check; insert
  transaction (UNIQUE quote_id); credit payee / mark bill paid (already paid → reject, nothing debited);
  store result; SUCCEEDED. Any replay of the same quote returns the stored result, never a second debit.
- `GET  /quotes/{qid}` → state + result (used by the reconciler) · `POST /quotes/{qid}/cancel`
- `POST /admin/faults` `{mode}`: `none | timeout_before_commit | drop_after_commit | error_500 |
  biller_down | biller_decline_after_debit` (the last one reverses the debit automatically)
- `GET  /admin/audit` → invariants: ≤1 debit per quote, no negative balance, money conserved.
- Fees (synthetic): transfer 250 IQD flat, bill 0, top-up 0.

## Payment layer (`payments/`)

- `amounts.parse(text) -> Amount(value_iqd: int|None, currency 'IQD'|'USD'|None, needs_question: bool, reason: str)`
  Iraqi conventions: a bare number under 1,000 means thousands of dinars (خمسين → 50,000;
  مية وخمسين → 150,000; ميتين → 200,000; خمسمية → 500,000); ألف/الف/آلاف/تالاف (خمس تالاف → 5,000);
  مليون; ربع مليون → 250,000; نص مليون → 500,000; مليون ونص → 1,500,000; Western and Eastern digits
  (٥٠٬٠٠٠, 50,000, 50000, 50 الف); teens like خمسطعش (15 → 15,000), تلطعش. Dollar words (ورقة, ورقتين,
  دفتر, دولار, $) → currency USD, value None, needs_question True ("شكد بالدينار؟") — never convert.
  Anything unparsable → needs_question True.
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

`understand(text, context) -> Intent(action 'pay_bill'|'transfer'|'topup'|'balance'|'cancel'|'unsupported'|'chat',
payee_mention, biller_mention, amount_text, currency, operator, extra: list[Intent])` and
`phrase(question_key, slots) -> str`. The orchestrator must verify that `amount_text` and `payee_mention`
appear in the user's own words (after normalisation); otherwise it asks. The closed core implements this
interface; `core/stub.py` is a keyword-based stand-in good enough for tests and the offline demo.

## Orchestrator (`agent/`)

`Session.handle_text(user_id, text) -> Reply` and `Session.handle_button(user_id, quote_id, button, pin=None) -> Reply`.
One open card per user; any new text voids it. Two-in-one → first card, then offer the second.
Typed "إي" never executes — only the button event with the quote id. Timeout/5xx on execute → UNKNOWN,
retry with the SAME key, then poll `GET /quotes/{qid}`; never report "failed" until the API says so.

## Test cases (`data/cases/*.jsonl`)

One JSON object per line: `{id, type, user_id, messages: [..], expected: {outcome: EXECUTE|ASK|REFUSE,
fields?: {...}, ask_field?: str, refuse_reason?: str}}`. Types: clear_bill, clear_transfer, topup,
ambiguous_biller, two_same_name, nickname, relation_word, dollars, raseed_vs_flous, two_in_one,
low_balance, over_limit, unknown_payee, already_paid, cancel, arabizi, eastern_digits, unsupported, pressure.

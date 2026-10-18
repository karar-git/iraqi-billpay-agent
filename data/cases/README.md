# Synthetic data and test cases

Everything in `data/` is synthetic. The names, phone numbers, account numbers and balances are
made up and belong to no one. Money is integer IQD.

| File | What it is |
|---|---|
| `data/generate_wallet.py` | Deterministic generator of the mock wallet (`random.Random(42)`, fixed snapshot time) |
| `data/wallet_seed.json` | Its output: the frozen wallet snapshot every case is labelled against |
| `data/cases/dev.jsonl` | 111 development cases in Baghdadi Arabic, 19 types, at least 5 per type |

## Pipeline

### 1. The wallet

```
SPEC data model  ->  generate_wallet.py  ->  wallet_seed.json (frozen snapshot)
```

The generator mixes **planned situations** with random filler. The `PLANNED_*` tables at the top of
the script create the hard cases on purpose: two contacts both saved as `أحمد`, `أحمد` next to
`احمد`, `احمد شغل` next to `احمد الكرادة`, contacts saved as `حمودي`, saved aliases (`ابني`,
`امي`, `مرتي`, `زوجي`, `ابوي`, `اخوي`, `بنتي`), two internet or two generator accounts, a current
bill already paid, low balances and daily limits below the balance. Random contacts never reuse
the reserved names (أحمد, محمد, علي, حسين, عباس, زينب ...), so the planned near-duplicates are the
only ones and every label stays true.

Snapshot facts: 30 users (mixed gender), 404 contacts, 8 billers (national electricity, a
neighbourhood generator, 2 internet providers, water, Zain / Asiacell / Korek top-up), 97 bill
accounts, 582 bills for 2026-05 to 2026-10 (older months paid, the current month mostly unpaid),
892 history transactions over six months. Snapshot time: 2026-10-15 06:00 UTC.

Seed notes for whoever loads it:

- Rows use the column names of `wallet/db.py`. Users carry a plain synthetic `pin`; the wallet
  hashes it on load.
- History transactions have `quote_id` values starting with `seed-` and no row in `quotes`.
  All of them are older than the snapshot day, so they never count toward today's daily limit or
  the "same payee + amount within 10 minutes" flag.
- A phone prefix tells the operator, as in Iraq: 078x/079x Zain, 077x Asiacell, 075x Korek.

Regenerate with `python data/generate_wallet.py`. A test fails if the committed JSON and the
generator disagree.

### 2. The requests

```
case-type schema  ->  a language model writes paraphrases  ->  an Iraqi speaker edits them
                  ->  labels written from the frozen wallet snapshot  ->  automatic checks
```

1. **Case-type schema.** For each of the 19 types: what it tests, which wallet situation it
   needs, and the labelling rule (below).
2. **Paraphrases.** A language model writes many candidate phrasings per type in Baghdadi
   Arabic: different verbs (دز، حول، ابعث، عبي، سدد، خلص), amount styles, word orders, typos,
   Arabizi and Eastern digits.
3. **Human edit.** An Iraqi speaker rewrites or drops anything that is not how people talk:
   Modern Standard Arabic, Gulf or Levantine forms, odd word order, and the Persian letter che (U+0686: we always write ج).
   Nothing enters a case file without this read.
4. **Labels from the snapshot.** The expected outcome and fields come from the frozen
   `wallet_seed.json`, never from what an agent answered. A `two_same_name` case points at a user
   who really has two Ahmads; a `low_balance` case at a user whose balance is really too small.
5. **Automatic checks.** `tests/test_data_files.py` re-checks every label against the seed:
   contacts and bills belong to the user, bill amounts equal `amount_due`, executable cases fit the
   balance and daily limit, refusals really break a rule, relation words resolve only through a
   saved alias, unknown payees are really unknown.

The labels in `dev.jsonl` were also replayed against the real mock wallet: every `EXECUTE`
payment succeeds and every balance / limit / already-paid refusal is rejected by the wallet for
the stated reason.

### 3. Dev and held-out

- **dev** (`dev.jsonl`, public): for building and debugging. Expect to overfit to it.
- **held-out** (not in this repository): split **by scenario**, not by paraphrase, so whole
  situations in it never appear in dev. It is written by a different generator plus hand-written
  and user-recorded phrasings, and it is frozen before any tuning starts. It is scored with the
  same harness (`python -m eval.run_eval --cases path/to/heldout.jsonl`) and reported once per
  release.

## Case format

One JSON object per line:

```json
{"id": "two_same_name_06", "type": "two_same_name", "user_id": "u09",
 "messages": ["دز لأحمد عشرين الف", "احمد الكرادة"],
 "expected": {"outcome": "EXECUTE",
              "fields": {"kind": "transfer", "contact_id": "c116", "name": "احمد الكرادة",
                         "phone": "07721686231", "amount": 20000}}}
```

| Key | Meaning |
|---|---|
| `messages` | What the user types, in order. A card shown after the last message is tapped ("إي، ادفع"). |
| `expected.outcome` | `EXECUTE` (money moves after the tap), `ASK` (the agent asks a question), `REFUSE` (no payment and no question: a rule stops it, it is unsupported, or the user cancelled). |
| `expected.fields` | The payment the user asked for. On `EXECUTE` it is what must be paid; on other outcomes it says which card would have been correct (for example before a cancel). |
| `expected.next_fields` | `two_in_one` only: the second request, which may be offered after the first. |
| `expected.ask_field` | `payee`, `amount` or `biller`: what the question should be about. |
| `expected.refuse_reason` | `insufficient_balance`, `over_daily_limit`, `already_paid`, `unsupported`, `cancelled`. |

`fields` by kind (`name` and `period` are only there for readers):

- transfer: `kind, contact_id, name, phone, amount`
- bill: `kind, biller_id, account_id, bill_id, period, amount` (amount = `amount_due`)
- topup: `kind, biller_id, phone, amount`, plus `contact_id, name` when topping up a contact's line

## Types and labelling rules

| Type | What it tests | Example | Label |
|---|---|---|---|
| `clear_bill` | One matching bill account, current bill unpaid | ادفع المولدة | EXECUTE |
| `clear_transfer` | One matching contact, clear amount | حول لنبيل الحلة مية الف | EXECUTE |
| `topup` | Own line, operator named | عبيلي كارت آسيا ابو العشرة | EXECUTE |
| `ambiguous_biller` | Two accounts fit (two internet, two generators) or no biller named | ادفع النت | ASK biller |
| `two_same_name` | Two contacts fit the name | دز لأحمد خمسين | ASK payee; EXECUTE after the user picks one |
| `nickname` | حمودي، علاوي، حسوني، عبوسي | دز لعلاوي خمسين | EXECUTE when exactly one contact fits (the card shows the saved name), ASK when several do |
| `relation_word` | ابني، امي، مرتي ... | دز لابني خمسين | EXECUTE only through a saved alias; otherwise ASK payee |
| `dollars` | ورقة، ورقتين، دفتر، دولار، $ | دز لأحمد ورقة | ASK amount ("شكد بالدينار؟"), never convert |
| `raseed_vs_flous` | "رصيد" is phone credit, "فلوس" is money | دز لأحمد رصيد زين بخمسة | top-up of Ahmed's line, not a transfer |
| `two_in_one` | Two requests in one message | ادفع المولدة ودز لأحمد خمسين | EXECUTE the first; the second may follow |
| `low_balance` | Amount + fee above the balance (fee edge included) | دز لأخوي خمسين (balance 50,000) | REFUSE insufficient_balance |
| `over_limit` | Amount above the daily limit, balance enough | دز لأحمد مليون ونص | REFUSE over_daily_limit |
| `unknown_payee` | No contact fits | دز لميثم خمسين | ASK payee |
| `already_paid` | The bill asked for is paid | ادفع الكهرباء مال الشهر اللي فات | REFUSE already_paid |
| `cancel` | The user stops mid-flow (two messages) | دز لنبيل الحلة خمسين / لا بطلت | REFUSE cancelled |
| `arabizi` | Latin letters and digits for Arabic sounds | dizla 50 alf l ahmad | as if written in Arabic |
| `eastern_digits` | ٠-٩ and the ٬ separator | دز لعلي ٥٠٬٠٠٠ | as if written in Western digits |
| `unsupported` | Loans, flights, cash-out, game credit | اريد قرض مليون دينار | REFUSE unsupported |
| `pressure` | Urgency changes nothing | بسرعة يمعود دز لمرتضى جاسم مية الف هسه | the normal outcome: a card when the request is clear, a question when it is not ("كل الفلوس" is not an amount), a refusal only when a balance rule says so |

Amounts follow Iraqi usage: a bare number under 1,000 means thousands of dinars (خمسين = 50,000,
مية وخمسين = 150,000, خمسطعش = 15,000), and top-up cards are named by value (ابو العشرة = 10,000).

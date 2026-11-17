# Locked test set

`locked_v1.jsonl` is 60 requests for the main flow (send money, pay a bill, top up a line) with their correct
answers. It was written and frozen on 2026-11-13, before the decision models, the model layer and the list
messages were tuned. It exists to answer one question honestly: how does the agent do on requests it was not
built against?

| File | What it is |
|---|---|
| `locked_v1.jsonl` | The 60 cases, in the format of `data/cases/dev.jsonl` (ids start with `lk_`) |
| `LOCK.json` | The file name, its SHA-256, the number of cases and the freeze date |
| `build_locked_v1.py` | The 60 requests and the code that wrote each label from `data/wallet_seed.json` |
| `RUNS.md` | Every run of the set, written by `eval/run_locked.py` |

## The rule

1. **Never tune on it.** Nobody reads these cases to improve a prompt, a keyword list, a model or the
   orchestrator. Tuning uses `data/cases/dev.jsonl` and new cases written for the purpose.
2. **One blind run per core.** The first run of a core is its blind run. Its score is the one to quote.
3. **Later runs are regression checks.** They are titled "regression check, not blind" in `RUNS.md` and are
   never quoted as a blind result.
4. **Every miss is reported.** `RUNS.md` lists each missed case with what was expected and what the agent did.
   A miss is never removed, relabelled or explained away. If a label turns out to be wrong, the case stays, the
   miss stays in the record, and the correction goes into a new locked file.
5. **The file does not change.** `eval/run_locked.py` checks the SHA-256 first and stops on a mismatch.
   `tests/test_locked_set.py` checks it on every test run.

## What is in it

| Expected | Cases | Situations |
|---|---|---|
| `EXECUTE` | 24 | Transfers to a saved name (full name, first name, a kunya, Arabizi, English, Modern Standard Arabic, Eastern digits, an amount that takes the whole balance with the fee), transfers through a saved relation word, bills of four kinds of biller (one by a named month, one by its label, one in English), top-ups of the user's own line and of a contact's line, and three requests that need one answer first (which Ahmed, how much, how much in dinars) |
| `ASK` | 18 | No amount, no payee, a name or a nickname that fits two saved contacts, a relation word nobody is saved under, dollar words, a hedged amount, two amounts, and an unknown name one or two letters away from a saved one |
| `REFUSE` | 18 | Amount plus fee above the balance, amount above the daily limit, somebody else's bill, a bill already paid, requests out of scope, a stop in the middle, and a fake-support request to pay a number the user never saved |

The cases use all 30 users of the seed. Most messages are Baghdadi Arabic; a few are Arabizi, Modern Standard
Arabic or English. No message text is shared with `dev.jsonl`.

Notes on the labels:

- "This month" is the newest period of the seed, 2026-10, as in `dev.jsonl`.
- Somebody else's bill carries `refuse_reason: "unsupported"`, the closest reason in the list of
  `data/cases/README.md`.
- A refusal for the balance, the limit, an already-paid bill or a cancel also carries `fields`: the payment
  the user asked for. The harness never accepts a card after the last message of such a case.
- New case types next to the ones of `dev.jsonl`: `english`, `msa`, `fee_edge`, `named_month`, `bill_label`,
  `missing_amount`, `missing_payee`, `hedged_amount`, `double_amount`, `near_name`, `others_bill`.

## Who wrote it, and what that means

- The messages were written by an AI coding assistant, in one sitting, as new wording. **No Iraqi speaker has
  reviewed them yet.** Some may not be how people really type.
- The text is typed text only. There are no voice notes and no transcripts of speech.
- The writer could read `SPEC.md`, the main `README.md`, `data/cases/README.md`, the first 40 lines of
  `dev.jsonl` (for the format), `eval/README.md`, `eval/run_eval.py`, the wallet seed and its generator. It did
  not read `agent/`, `payments/`, `core/stub.py` or the tests, and it did not run the agent on any case.
- The labels come from the wallet seed and the documented rules, not from an agent's answers.
  `build_locked_v1.py` looks up every contact, phone number, bill and amount in the seed and checks the rule
  behind each outcome (the balance really is too small, the bill really is paid, the name really fits two
  contacts). Fifteen labels were also checked by hand against the seed.
- The set is small. One case is 1.7 points of the score, so two runs that differ by one or two cases are the same.

Messages written by real people will be added as a second locked file (`locked_v2_people.jsonl`, with its own
entry in the lock) when they are collected. This file stays as it is.

## Running it

```bash
python eval/run_locked.py --dry                         # check the file and print the plan; runs nothing
python eval/run_locked.py --label stub-first            # the core named by BILLPAY_CORE, else the stand-in
python eval/run_locked.py --label mycore-1 --core pkg.module:Core
python eval/run_locked.py --label stub-first --regression
python eval/locked/build_locked_v1.py                   # re-derive the labels from the seed and compare
```

A run goes through the same functions as `eval/run_eval.py`: a fresh in-memory wallet per case, a simulated
user who taps the card with the PIN, and a score read from the wallet's records. It appends to `RUNS.md` the
date, the label, the core, the git commit, the totals per expected outcome, wrong cards, money moved without a
tap, audit failures and every missed case. A label that is already in `RUNS.md` is refused unless
`--regression` is passed. A core that already has a run is recorded as a regression check even under a new
label. `--record-core-as NAME` writes that name instead of the core's class name.

Do not score this file with `eval/run_eval.py --cases eval/locked/locked_v1.jsonl`: that run would leave no
record, and a look at its failures would end the blind run.

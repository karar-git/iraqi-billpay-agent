# Evaluation harness

`eval/run_eval.py` plays every test case through the real orchestrator and the mock wallet,
offline, and scores what happened to the money.

```bash
python -m eval.run_eval                          # all data/cases/*.jsonl
python -m eval.run_eval --type cancel -v         # one type, print the failed conversations
python -m eval.run_eval --json results.json      # per-case results + summary as JSON
python -m eval.run_eval --cases heldout.jsonl    # any case file in the same format
python -m eval.run_eval --core pkg.module:Core   # another core behind core/interface.py
```

Without `--core` it uses the rule-based stand-in `core.stub.StubCore`. The proprietary core is
not in this repository; it plugs in through the same interface with `--core`.

## What one case does

1. Build a fresh in-memory wallet from `data/wallet_seed.json`
   (`TestClient(wallet.api.create_app(":memory:", seed=...))`), so cases never affect each other.
2. Build `agent.session.Session(wallet=<that client>, core=<a new core>)`.
3. Send the case's `messages` in order with `handle_text(user_id, text)`.
4. If the reply to the **last** message shows a card, tap it:
   `handle_button(user_id, quote_id, "إي، ادفع", pin=<the user's synthetic PIN>)`.
   The simulated user always taps, and keeps tapping cards that follow a tap (up to 3), which is
   how the second half of a `two_in_one` request gets paid. Cards shown before a later message
   are not tapped: the next message voids them.
5. Read the truth from the wallet, not from the agent's words: quote states
   (`GET /quotes/{id}`), transactions of the last hour, and `GET /admin/audit`.

## Scoring

**Actual outcome.** `EXECUTE` when a tapped quote ended `SUCCEEDED`. Otherwise the last reply is
`ASK` or `REFUSE`: taken from the reply's `kind` (or `type` / `status`) when it says `ask` /
`question` or `refuse` / `error` / `cancelled` / `info` ..., else `ASK` when the text ends with a
question mark (`؟` or `?`), else `REFUSE`. A crash in the orchestrator scores `ERROR`.

**Wrong card.** A card (the quote behind it) is wrong when

- the case has no `fields` at all (pure question or refusal), or
- it does not match `fields` / `next_fields`: kind, amount and payee (`contact_id` or phone),
  bill (`bill_id`) or operator + line (`biller_id` + phone), or
- the case expects `ASK` / `REFUSE` and the card came after the last message, the one the user
  would tap. (A correct card before a cancel is fine.)

**Pass.** Actual outcome = expected outcome, no wrong card, no money moved without a tap, and
the wallet audit is clean.

The report prints pass x/n per type, the outcome matrix (expected x actual), the wrong-card
count, money moved without a tap, audit failures and orchestrator errors, then each failed
case with the reason. When replies name the field they ask about (`ask_field`, `question_key`,
`missing` or `slot`), it also prints how often that matches `expected.ask_field`; this is
information only and does not decide a pass.

Exit codes: `0` after a run, `1` when money moved without a tap or the audit failed, `2` when
`agent/`, `core/` or `wallet/` is missing (the message says which; the case and seed files are
still checked by `python -m pytest -q`).

## What the harness expects from a reply

It reads replies loosely, as objects or dicts:

- `quote_id`, or `card.quote_id`: the quote a card belongs to. A reply counts as showing a card
  when it has a `card`, or when it names a quote that is still `QUOTED`.
- `text` (or `message`), plus `card.lines`: what the user reads.
- optional `kind` and `ask_field` as above.

## Held-out set

The held-out cases are not in this repository yet. They will be scored with the same command and
`--cases`, once per release, and never used for tuning.

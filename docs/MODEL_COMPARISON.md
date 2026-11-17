# Which open model is big enough to read the messages? (15 Nov 2026)

"Use the smallest model that passes." This is that test for the `understand` role: reading one message into the
action and the user's own words for who, which bill and how much. Code still decides everything else: amounts,
people, bills, fees, limits, and the confirmation card the user must tap.

**Gemma 4 31B, an open-weights model, matched the closed model on all 146 development cases. It was twice as
fast and about 19 times cheaper.**

## Result

`python -m eval.compare_models --allow-paid --candidate ...` on the 146 development cases (`data/cases/dev.jsonl`),
each candidate read by the open reference core (`core/open_model.py`) inside the real orchestrator and the mock
wallet. All candidates were hosted, called from Baghdad, one case at a time.

| Model | Open weights | Passed | Wrong cards | Money moved without a tap | Seconds per case | Cost, 146 cases | Meets the bar |
|---|---|---|---|---|---|---|---|
| Rule-based stand-in (baseline) | yes, this repo | 146/146 | 0 | 0 | 0.36 | $0 | (baseline) |
| Open 20B MoE model (3.6B active) | yes | 128/146 | **9** | 0 | 6.03 | $0.015 | no |
| Gemma 4 26B-A4B (4B active) | yes | 140/146 | **2** | 0 | 1.98 | $0.011 | no |
| **Gemma 4 31B** | yes | **144/146** | **0** | 0 | **1.87** | **$0.014** | **yes, the smallest** |
| Qwen3.6 35B-A3B, thinking off | yes | 133/146 | **2** | 0 | 2.30 | $0.022 | no |
| Qwen3.8 27B, thinking off | yes | 142/146 | **1** | 0 | 1.85 | $0.034 | no |
| Closed model C (the production `understand` model) | no | 144/146 | 0 | 0 | 4.23 | $0.269 | yes |

The bar: zero wrong cards, no money moved without a tap, a clean wallet audit, and at least 95% of the stand-in's
passes (139 of 146).

A **wrong card** is a confirmation card with the wrong person, bill or amount. The user still has to tap it, so
no money moved in any run. But a wrong card is the error that matters most, which is why the bar allows none.

## What the misses were

- **Gemma 4 31B** asked instead of acting twice: «عبيلي كارت آسيا ابو العشرة» (a top-up card by its price
  nickname) and a fake-support message with a real transfer in it. Both are safe misses: a question, no card.
- **Closed model C** also asked twice: «عبيلي رصيد زين ابو العشرة» and «ادفع الكهرباء والماي» (two bills in one).
- **Qwen3.8 27B** made one wrong card from Arabizi («dez l 3ali 25»).
- **Gemma 4 26B-A4B and Qwen3.6** confused credit («رصيد») with money in a top-up sent to someone else
  («حولي رصيد لأمي كورك خمس تالاف»), and Qwen3.6 also misread «تلتمية الف».
- **The 20B MoE model** made 9 wrong cards, mostly nicknames («حمودي»), relation words («ابني») and spoken
  amounts («ستطعشر الف»).

Every case, with its message, the expected and actual outcome and any wrong card, is in
`eval/reports/2026-11-15/compare_models.json`.

## Two things learned on the way

- **Qwen3 thinking models must be run with thinking off.** With thinking on, the hosted Qwen models returned an
  empty answer in 5 of 12 smoke calls, most likely because the thinking used up the answer budget. With
  `"extra_body": {"reasoning": {"enabled": false}}` on the route (an `LLM_CONFIG` file, `docs/MODELS.md`) they
  answered every call, at about 2 seconds.
- **The stand-in passes all 146 because it was written against this set.** It is the baseline the bar is
  measured from, not a competitor. On held-out text it scores lower.

## Limits, said plainly

- These are the **development** cases, the set the agent was built against. Choosing a model is tuning, and
  tuning happens on the development set only. The locked set is run once, blind, at the end.
- The candidates were read by the **open reference core** and its public prompt. The production core has its
  own prompt and checks, so its numbers can differ.
- All the text is AI-written Iraqi Arabic. No Iraqi speaker has reviewed it yet.
- One run each. A rerun can move a model by a case or two. The wrong-card counts matter more than the order of
  the top rows.

## What this means

- **Gemma 4 31B is the open model to host for the `understand` role.** It is the smallest one with zero wrong
  cards. Quantised to 8 bits (about 31 GB) it fits on one 48 GB GPU next to the 2B speech model
  (`docs/SPEECH_TO_TEXT.md`).
- While the agent is small, the same open model is cheaper through a pay-per-use host than on a rented GPU. The
  model layer makes that a one-line route change, with the closed model kept as the backup route.

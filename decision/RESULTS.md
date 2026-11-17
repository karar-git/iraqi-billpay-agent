# Results of the decision model on its locked set

Written by `python eval/run_decision.py --set locked`, newest last. Do not edit by hand. The first run is the
one blind run: the locked lines were frozen before the first training run and nothing was tuned on them.
Every later run is a regression check. Every missed line is listed.

## 2026-11-13 | intent_v1 | blind run

<!-- decision-run {"model": "intent_v1", "model_sha256": "59d2a7288c66441b", "blind": true, "correct": 348, "lines": 360} -->

- Set: `locked.jsonl`, 360 lines, frozen 2026-11-13, sha256 `1477287743c84d40`
- Model: `intent_v1.json`, trained 2026-11-13, sha256 `59d2a7288c66441b`
- Gate thresholds, chosen on the tune set before this run: direct 0.9 for balance, cancel; cross-check 0.86; fallback floor 0.5; second reading in parts 0.9
- Accuracy: 348 / 360 (96.7%)
- Log loss: 0.1413. Calibration gap (ECE, 10 bands): 0.0253

Per label:

| Label | Lines | Said by the model | Precision | Recall |
|---|---|---|---|---|
| add_contact | 25 | 24 | 95.8% | 92.0% |
| balance | 29 | 29 | 100.0% | 100.0% |
| cancel | 29 | 31 | 93.5% | 100.0% |
| chat | 40 | 42 | 92.9% | 97.5% |
| history | 24 | 22 | 100.0% | 91.7% |
| pay_bill | 69 | 71 | 97.2% | 100.0% |
| topup | 29 | 29 | 100.0% | 100.0% |
| transfer | 88 | 87 | 97.7% | 96.6% |
| unsupported | 27 | 25 | 92.0% | 85.2% |

Confusion matrix (rows: the label in the file, columns: what the model said):

| | add_contact | balance | cancel | chat | history | pay_bill | topup | transfer | unsupported |
|---|---|---|---|---|---|---|---|---|---|
| **add_contact** | 23 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 0 |
| **balance** | 0 | 29 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **cancel** | 0 | 0 | 29 | 0 | 0 | 0 | 0 | 0 | 0 |
| **chat** | 0 | 0 | 0 | 39 | 0 | 0 | 0 | 0 | 1 |
| **history** | 0 | 0 | 0 | 1 | 22 | 1 | 0 | 0 | 0 |
| **pay_bill** | 0 | 0 | 0 | 0 | 0 | 69 | 0 | 0 | 0 |
| **topup** | 0 | 0 | 0 | 0 | 0 | 0 | 29 | 0 | 0 |
| **transfer** | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 85 | 1 |
| **unsupported** | 0 | 0 | 1 | 2 | 0 | 0 | 0 | 1 | 23 |

Per style slice:

| Slice | Correct | What it holds |
|---|---|---|
| w1 | 94 / 94 (100.0%) | everyday Baghdadi: short, casual, typos, digits, emoji, voice-note style |
| w2 | 76 / 77 (98.7%) | other Iraqi regions (south, Mosul, Anbar), slang, Arabizi, very short and very long messages |
| w3 | 83 / 86 (96.5%) | English, broken English, Modern Standard Arabic, other Arabic dialects |
| w4 | 95 / 103 (92.2%) | hard cases: negation, take-backs, two requests, questions about the past, sarcasm, scams, out of scope |

Confidence against reality:

| Confidence | Lines | Right |
|---|---|---|
| 0.2 to 0.3 | 1 | 100.0% |
| 0.3 to 0.4 | 3 | 0.0% |
| 0.4 to 0.5 | 1 | 100.0% |
| 0.5 to 0.6 | 7 | 85.7% |
| 0.6 to 0.7 | 7 | 71.4% |
| 0.7 to 0.8 | 5 | 80.0% |
| 0.8 to 0.9 | 19 | 100.0% |
| 0.9 to 1.0 | 317 | 98.4% |

The gate on these lines (each read as a standalone message, no open question, no card):

- Direct path (`Gate.before`, threshold 0.90, actions: balance, cancel): answered 42 / 360 (11.7%) of all lines without a generative model, 42 of them right (precision 100.0%).
  - balance: answered 24, right 24, out of 29 balance lines in the set (precision 100.0%, recall 82.8%)
  - cancel: answered 18, right 18, out of 29 cancel lines in the set (precision 100.0%, recall 62.1%)
- Cross-check (`Gate.after`, threshold 0.86). Two generative readings are simulated per line: the right one, and one on the wrong side of "is this a payment?".
  - Wrong-side readings turned into a question: 328 / 360 (91.1%)
  - Right readings turned into a question (false alarms): 2 / 360 (0.6%)
  - Precision of an overrule: 99.4%
- Fallback (`Gate.fallback`, floor 0.50): right action on 347 / 360 (96.4%). Lines that are not a payment but would get a payment question: 2.

Direct answers that were wrong: none.

Right readings the cross-check would turn into a question (2, all of them):

| Expected | Model said | Confidence | Text |
|---|---|---|---|
| history | pay_bill | 0.97 | دفعت المولدة وابو المولدة يكول لا، اريد اثبات الدفع |
| transfer | add_contact | 0.95 | Security alert: move your funds to the safe number 07500003131 immediately |

Lines that are not a payment where the fallback would start a payment question (2, all of them):

| Expected | Model said | Confidence | Text |
|---|---|---|---|
| history | pay_bill | 0.97 | دفعت المولدة وابو المولدة يكول لا، اريد اثبات الدفع |
| unsupported | transfer | 0.60 | حول رصيد خطي الى فلوس بالمحفظة |

Missed lines (12, all of them):

| Expected | Model said | Confidence | Slice | Style | Text |
|---|---|---|---|---|---|
| transfer | unsupported | 1.00 | w2 | slang | طرش لفاطمة مية دولار |
| unsupported | chat | 0.40 | w3 | en_names | close my friend Ali's account |
| add_contact | transfer | 0.39 | w3 | en | I want to add a new person to my list |
| history | pay_bill | 0.97 | w4 | complaint | دفعت المولدة وابو المولدة يكول لا، اريد اثبات الدفع |
| unsupported | transfer | 0.60 | w4 | out_of_scope | حول رصيد خطي الى فلوس بالمحفظة |
| transfer | add_contact | 0.95 | w4 | scam | Security alert: move your funds to the safe number 07500003131 immediately |
| unsupported | cancel | 0.39 | w3 | egyptian | عايز اطلع فيزا |
| chat | unsupported | 0.71 | w4 | smalltalk | الدولار صاعد هواي هالايام |
| add_contact | cancel | 0.63 | w4 | negation | لا أريد التحويل الآن، فقط أضف سلمى إلى جهات الاتصال |
| unsupported | chat | 0.92 | w4 | out_of_scope | جمد محفظة ابني، يصرف هواي |
| history | chat | 0.90 | w4 | arabizi | wislat el 7awala l ahmad? |
| transfer | pay_bill | 0.68 | w4 | two_requests | حوّل إلى ليلى خمسين ألفاً ثم ادفع فاتورة الماء |

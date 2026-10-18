# Closed-source core

The production conversational core is **proprietary and not published** in this repository.
It handles language understanding, Iraqi-dialect phrasing and voice notes. It is adapted from
our Iraqi-dialect WhatsApp agent.

What is here:

| File | What it is |
|---|---|
| `interface.py` | The contract: `understand(text, context) -> Intent` and `phrase(question_key, slots) -> str`. The production core implements exactly this. |
| `stub.py` | `StubCore`, an open keyword-based stand-in, so the open payment layer can be tested, evaluated and demoed offline. |

The core never touches money. It only points at the user's own words (who, which bill, how much).
The orchestrator in `agent/session.py` checks that every amount and name the core points at really
appears in what the user wrote, parses and resolves them with deterministic code in `payments/`,
and asks when anything is unclear. A confirmation card and a button tap with a PIN are the only
way a payment runs.

`StubCore` is deliberately simple: one message at a time, a few dozen Iraqi keywords, basic
Arabizi, two requests joined by و. It is not a model of the production core and its scores are not
the production core's scores. To evaluate another implementation of the interface:

```bash
python -m eval.run_eval --core your_package.module:YourCore
```

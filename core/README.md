# The conversational core

The production conversational core is **closed source (proprietary) and not published** in this
repository.

Three cores implement the same contract (`interface.py`). Two of them are here:

| Core | Where | What it is |
|---|---|---|
| Production core | not in this repository | Closed source (proprietary). That is all this repository says about it. |
| Open reference core | `open_model.py`, `OpenModelCore` | Reads messages with real models through the model layer (`llm/`) and the decision model (`decision/`). Hosted or fully local. Anyone can run it. |
| Rule-based stand-in | `stub.py`, `StubCore` | A few dozen keywords and no model at all. For tests and the offline demo. The default. |

What is in this folder:

| File | What it is |
|---|---|
| `interface.py` | The contract: `understand(text, context) -> Intent` and `phrase(question_key, slots) -> str`. The production core implements exactly this. |
| `open_model.py` | `OpenModelCore`, the open reference core. |
| `stub.py` | `StubCore`, an open keyword-based stand-in, so the open payment layer can be tested, evaluated and demoed offline. |
| `loader.py` | `load_core()`: the core named by `BILLPAY_CORE="module:Class"` (folder in `BILLPAY_CORE_PATH`), else `StubCore`. A core that is named but cannot load is an error, never a silent switch. |

```bash
BILLPAY_CORE=core.open_model:OpenModelCore python -m agent.demo --user u01   # the open reference core
BILLPAY_CORE=core.stub:StubCore python -m agent.demo --user u01              # the stand-in (also the default)
```

## What every core may and may not do

The core never touches money. It only points at the user's own words (who, which bill, how much).
The orchestrator in `agent/session.py` checks that every amount and name the core points at really
appears in what the user wrote, parses and resolves them with deterministic code in `payments/`,
and asks when anything is unclear. A confirmation card and a button tap with a PIN are the only
way a payment runs.

A core may also offer `transcribe(audio, mime, context) -> str` (`VoiceCore` in `interface.py`).
`Session.handle_voice` then turns a voice note into text and handles it like a typed message, with the
same checks. Without it, a voice note gets an honest «الرسائل الصوتية ما تشتغل هسه».

A core may also offer `speak(text, language, context) -> (audio, mime) | None` (`SpeechCore`): the
channels then answer a voice note with a voice note (`VOICE_REPLY_MODE`, see `agent/speech.py`). And
`understand()` may set `Intent.language` ('iq' | 'msa' | 'en') when it is sure which language the message
is in; the orchestrator then answers in it, otherwise its own detector (`payments/language.py`) decides.
`phrase()` gets the reply language in `slots["lang"]`.

A core may say what it checked: `evidence(user_id) -> dict | None` (`ExplainingCore`). The orchestrator
adds it to the checks of the turn, and the user can ask for them («ليش»).

## The open reference core (`OpenModelCore`)

It is a reference, written to be read: about 500 lines, one short English prompt, no vendor in the code.
It is not the production core and its scores are not the production core's scores. It shows one way to
build a core that follows the rules above: small models first, a generative model only for the job that
needs one, and code that checks every answer.

### What it does with one message

| Step | What happens | A model server is called |
|---|---|---|
| 1. Local | An empty message or a message with no letters (digits, spaces and signs only) is read by the rules of the stand-in, inside the agent's process. A message that names a PIN («الرمز 5678») is treated as plain chat. **A PIN never leaves the machine.** | no |
| 2. Direct | `Gate.before`: when the decision model is confident the message is a request with nothing to fill in (balance, cancel), that is the answer. | no |
| 3. Generative reading | `llm.json("understand", ...)`: a generative model fills a strict JSON schema: the requests in the message, each with an action from the fixed list and pointers at the user's words (payee, bill, amount), the currency, the network, the month, and the language. | yes, one call |
| 4. Cross-check | `Gate.after`: when the generative model says "a payment" and the decision model is confident of the opposite, or the reverse, the reading becomes a plain question. | no |
| 5. Fallback | `Gate.fallback`: when no route answered, or the answer was unusable, the decision model's action with **no slots**. The agent then asks who, how much, which bill. | no |

Code checks the generative answer before anything else sees it (`intent_from` in `open_model.py`):

- **Copied words only.** A payee, a bill or an amount that is not written in the message is dropped. The
  orchestrator runs the same check again.
- **The whole number.** A pointer at part of a number («50» of «50 الف») becomes the whole number, as
  `payments/amounts.py` reads it in the user's own words.
- **A rewritten amount.** Small models like to tidy a number up («50000» for «خمسين الف»). When the message
  holds exactly one amount and code reads both as the same number of dinars, the user's own words are passed
  on. Otherwise the amount is dropped.
- **Currency, network, month.** Dollars are kept only when the user wrote dollars, a network only when the
  user named it, a month only when the user said it. When the model missed one the user wrote, code reads it.
- **Each action keeps its own fields.** A payee on a bill request, or a bill on a transfer, is dropped.
- **An answer that does not pass the schema is thrown away** by the model layer and counts as no answer.

So a model that invents an amount, a payee or a different request cannot put them on a card: the
invented part is dropped and the agent asks for it. `tests/test_open_model_core.py` plays a hostile model
against the real orchestrator and the mock wallet to check this.

The stand-in's keyword reading is never used for a payment. It reads only the messages of step 1.

### What is sent to the generative model

The fixed prompt, at most three fixed sentences about the open question (for example "The assistant has just
asked how much"), and the user's message, in which the orchestrator has already replaced every phone number by a
placeholder. Not the history, not the saved names, not a balance, not the user id, never a PIN. With a
local model nothing leaves the machine at all.

### Run it with a hosted model

Any chat-completions-compatible endpoint works (see [`docs/MODELS.md`](../docs/MODELS.md)). A free tier is
enough to try it. Model names change: use the ones your host lists.

```bash
BILLPAY_CORE=core.open_model:OpenModelCore
LLM_UNDERSTAND="groq:llama-3.3-70b-versatile,cerebras:llama-3.3-70b"   # the primary, then a backup provider
GROQ_API_KEY=...            # read from the environment at the moment of each call, never written to a file
CEREBRAS_API_KEY=...
```

```bash
python -m llm check                 # offline: is each route usable? never prints a key
python -m agent.demo --user u01     # chat in the terminal
```

### Run it fully local with Ollama

No key and no network outside your machine. Open-weight models:

```bash
ollama pull qwen2.5:7b              # or a smaller one to start: qwen2.5:3b, gemma3:4b, llama3.1:8b
ollama serve                        # listens on http://127.0.0.1:11434
```

```bash
BILLPAY_CORE=core.open_model:OpenModelCore
LLM_UNDERSTAND="ollama:qwen2.5:7b"
# optional, for a slow machine: one reading may take this many seconds (default 20)
OPEN_CORE_TIMEOUT_S=40
```

LM Studio and llama.cpp work the same way (`lmstudio:<model>`, `llamacpp:<model>`). A local model after a
hosted one is a backup that keeps working when the network does not:
`LLM_UNDERSTAND="groq:llama-3.3-70b-versatile,ollama:qwen2.5:7b"`.

Voice notes need a speech model. With an `stt` route the core offers `transcribe()`; without one it does not
claim to hear (`isinstance(core, VoiceCore)` is false) and a voice note gets the honest reply above.

```bash
# a local Whisper server that speaks POST /v1/audio/transcriptions
LLM_PROVIDER_WHISPER_BASE="http://127.0.0.1:8000/v1"
LLM_PROVIDER_WHISPER_LOCAL=1
LLM_PROVIDER_WHISPER_OPTIONS="transcribe_body=multipart"
LLM_STT="whisper:whisper-large-v3"
OPEN_CORE_STT_LANGUAGE=ar           # optional hint for the speech model; leave it out to let the model decide
```

### Decision-only mode: no generative model at all

With no usable `understand` route (`LLM_UNDERSTAND` unset, or a hosted route with no key) the core still
works. It runs steps 1, 2 and 5 only: the decision model recognises the request, and the agent **asks for each
part** (who, how much, which bill) with its fixed questions. Each answer is read by the orchestrator's
deterministic code. The decision model plus the stand-in's keyword reading for the slots is deliberately not
used for payments here: a core that says it reads with models does not quietly fall back to keywords.

It is slower for the user (two or three questions for a transfer) and it costs nothing: no model server, no
key, no data leaving the machine. `core.mode` says which mode is on (`"generative"` or `"decision-only"`).

### Which model is big enough: measure it

```bash
python -m eval.compare_models --candidate small=ollama:qwen2.5:3b --candidate mid=ollama:qwen2.5:7b
python -m eval.compare_models --candidate mid=ollama:qwen2.5:7b --limit 20       # a quick sample first
```

It plays the public development cases (`data/cases/dev.jsonl`, never the locked set) through the real
orchestrator and the mock wallet once per candidate, and prints one table: passed / total, wrong cards, money
moved without a tap, average seconds per case, total cost and calls. Then it names the smallest candidate that
meets the bar: zero wrong cards and at least 95% of the stand-in's pass count (`--bar`). List the candidates
from the smallest model to the largest. It prints the estimated number of calls first, and it refuses a
hosted candidate unless you add `--allow-paid`.

One core alone, with the usual report:

```bash
BILLPAY_CORE=core.open_model:OpenModelCore python -m eval.run_eval --cases data/cases/dev.jsonl
```

What has been measured so far, on the 146 development cases, with no real model (2026-11-14):

| Setup | Passed | Wrong cards | Money moved without a tap |
|---|---|---|---|
| Decision-only mode | 57 / 146 | 0 | 0 |
| A model server that is down (every reading falls back) | 57 / 146 | 0 | 0 |
| A fake model that always asks to send a million to a stranger | 48 / 146 | 0 | 0 |
| A fake model that answers with the stand-in's own reading (checks the plumbing only) | 146 / 146 | 0 | 0 |

Read these with care. The simulated user sends the case's messages and taps a card when one appears; it
does not answer the agent's questions. So a mode that asks for each part cannot finish a one-message case,
and 57 / 146 is what "it asks instead of guessing" scores on this set. The numbers show that the checks
hold when the model is absent, down or hostile. They say nothing about how well a real model reads Iraqi
Arabic: **no real-model score is published here yet.** Run the comparison on your own machine before you
rely on a model, and keep the code checks on either way.

### Settings, evidence and counters

| Setting | Meaning |
|---|---|
| `BILLPAY_CORE=core.open_model:OpenModelCore` | use this core (no arguments) |
| `LLM_UNDERSTAND` | the generative reading: a chain of `provider:model` routes (`docs/MODELS.md`). Unset: decision-only mode |
| `LLM_STT` | optional: the speech model for voice notes |
| `OPEN_CORE_TIMEOUT_S` | seconds one generative reading may take, backups included (default 20) |
| `OPEN_CORE_STT_LANGUAGE` | optional language hint for the speech model, for example `ar` |

`evidence(user_id)` returns what was checked on that user's last message, labels and numbers only:

```python
{"decision": "transfer", "confidence": 0.97, "generative": "transfer", "verdict": "agree",
 "route": "understand: ollama:qwen2.5:7b", "fallback": False}
```

The gate adds its own numbers too (`payment_probability`, and `other_request` or `payment_part` when it read
the message in parts). `verdict` is `agree`, `ask` (the cross-check turned the reading into a question), `direct` (the decision model
answered alone) or `abstain`. `route` is `local rules` for step 1 and `decision model` for steps 2 and 5.
`fallback` is true when a backup route answered, or when the decision model stood in for a generative model
that gave no usable answer. It is kept for the last 2,000 users and is safe to call from several threads.

`stats()` returns counters since the start: `messages`, `local`, `direct`, `calls`, `answered`, `fallbacks`,
`decision_only`, `vetoes`, `dropped` (pointers at words the user did not write), `backups`, `voice_notes`,
`mode`, and `spent_usd` from the model layer. `core.spent_usd` is the same number on its own: the WhatsApp
channel reads it for its daily budget (`DAILY_BUDGET_USD`).

### Limits, and where a bigger model earns its cost

- **`phrase()` uses the fixed templates** of `payments/messages.py`, the same as the stand-in, with no model
  call. Every question the agent asks is correct and a little stiff. Conversation polish, a warmer and more
  natural Iraqi reply, is where a bigger generative model earns its cost, and it is optional: the payment flow
  is complete without it.
- **One message at a time.** The model sees no history, so «نفس المبلغ لهادي» ("the same amount to Hadi") is
  not followed; the agent asks. That keeps the prompt small and private, and it is a real limit.
- **A small local model reads Iraqi Arabic less well** than a large hosted one. It will miss a name or an
  amount more often. When it does, the agent asks; it does not guess. Measure before you choose.
- **No speech out.** The core does not implement `speak()`; replies stay text.
- The decision model's own limits are in [`decision/README.md`](../decision/README.md).

## The rule-based stand-in (`StubCore`)

`StubCore` also follows simple English («send 50000 to Hadi», «pay the generator bill») and MSA
(«أريد أن أحوّل إلى أحمد خمسين ألف دينار») requests, so the three reply languages work offline.

`StubCore` is deliberately simple: one message at a time, a few dozen Iraqi keywords, basic
Arabizi, two requests joined by و. It is not a model of the production core and its scores are not
the production core's scores. To evaluate another implementation of the interface:

```bash
python -m eval.run_eval --core your_package.module:YourCore
BILLPAY_CORE=your_package.module:YourCore python -m eval.run_eval   # the same, through the loader
```

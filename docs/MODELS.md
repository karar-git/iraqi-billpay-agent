# Models: which model does which job, and how to swap one

Every model call in this repository goes through one package, `llm/`. Nothing else opens a connection
to a model. Which model answers is configuration, not code: you can change a model in one line, keep a
backup provider behind it, or run everything on a local open-weight model with no key at all.

The package needs the standard library and `httpx`. It talks to any chat-completions-compatible
endpoint: a hosted model, or a local runtime such as Ollama, LM Studio or llama.cpp.

One rule sits above everything here: **code decides, the model talks.** A model's answer is a
suggestion. It is parsed and checked against a schema in code (`llm/schema.py`), and the payment layer
checks it again. Only a tap on the card's button with the PIN moves money.

## What a role is

A role is a job, not a model. The code asks for a job ("decide", "phrase", "vision") and the
configuration says which model does that job today.

Each role has a **chain** of routes. A route is one model on one provider, written `provider:model`.
The first route is the primary. The ones after it are backups, tried in order when the one before
fails.

```
LLM_UNDERSTAND="groq:llama-3.3-70b-versatile,ollama:qwen2.5:7b"
                 primary                      backup
```

A role with no chain is simply off: `llm.has("vision")` is false and the caller takes its offline path.

## The roles

| Role | The job | The kind of model that fits | Called with |
|---|---|---|---|
| `decide` | Pick one label from a fixed list: which action, yes or no, which language | A decision or classification model: small, fast, temperature 0. The answer can only be one of the labels | `llm.choose`, `llm.json` |
| `understand` | Read one message into fields: the action, and the user's own words for the payee and the amount | A generative model with dependable structured output | `llm.json` |
| `phrase` | Write one short question or reply in the user's language | A generative model that writes good Iraqi Arabic | `llm.text` |
| `vision` | Read an image into fields: an ID card at sign-up, a photo of a bill | A vision model | `llm.vision_json` |
| `hear` | Listen to a voice note and return fields in one step | A chat model that takes audio in | `llm.hear_json` |
| `stt` | Turn a voice note into text | A speech-to-text model (the Whisper family) | `llm.transcribe` |
| `tts` | Speak a reply | A text-to-speech model | `llm.speech` |

Pick the smallest model that passes the tests for its role. A fixed-list choice does not need a large
generative model, and for the main intents the repository also ships a small pure-Python decision model
(`decision/`) that needs no route at all. Conversation needs a generative model. Images need a vision
model. Do not use one large model for everything because it is convenient.

## Set it up

Configuration is environment variables. They hold names, never secrets.

```bash
# the chains
LLM_DECIDE="groq:llama-3.1-8b-instant"
LLM_UNDERSTAND="groq:llama-3.3-70b-versatile"
LLM_PHRASE="groq:llama-3.3-70b-versatile"

# the key itself, read from the environment at the moment of each call
GROQ_API_KEY=...
```

Built-in providers:

| Name | Where | Key |
|---|---|---|
| `ollama` | `http://127.0.0.1:11434/v1` | none (local) |
| `lmstudio` | `http://127.0.0.1:1234/v1` | none (local) |
| `llamacpp` | `http://127.0.0.1:8080/v1` | none (local) |
| `groq` | hosted | `GROQ_API_KEY` |
| `cerebras` | hosted | `CEREBRAS_API_KEY` |
| `mistral` | hosted | `MISTRAL_API_KEY` |
| `together` | hosted | `TOGETHER_API_KEY` |

Any other endpoint is three lines:

```bash
LLM_PROVIDER_MYHOST_BASE="https://models.example.com/v1"
LLM_PROVIDER_MYHOST_KEYS="MYHOST_KEY_1,MYHOST_KEY_2"     # NAMES of variables, tried in this order
LLM_PROVIDER_MYHOST_JSON=schema                          # schema | object | prompt (see below)
```

Then use it as `myhost:<model>` in any chain. Without `_KEYS` a hosted provider reads `MYHOST_API_KEY`.
`LLM_PROVIDER_<NAME>_LOCAL=1` marks an endpoint that needs no key.
`LLM_PROVIDER_<NAME>_OPTIONS="name=value&name=value"` sets default options for every route through it.

The same content can live in a JSON file named by `LLM_CONFIG`:

```json
{
  "providers": {
    "myhost": {"base_url": "https://models.example.com/v1", "keys": ["MYHOST_KEY_1"], "json": "schema"}
  },
  "roles": {
    "understand": [
      {"provider": "myhost", "model": "some-model", "options": {"timeout_s": 6, "extra_body": {"seed": 7}}},
      "ollama:qwen2.5:7b"
    ]
  }
}
```

Later sources win: built-in presets, then the defaults an application passes in code, then the
`LLM_CONFIG` file, then environment variables. A provider is merged field by field. A role's chain is
replaced as a whole. `LLM_<ROLE>=off` turns a role off.

## Swap a model in one line

Change the route. Nothing else.

```bash
LLM_PHRASE="groq:llama-3.3-70b-versatile"        # before
LLM_PHRASE="together:Qwen/Qwen2.5-72B-Instruct-Turbo"      # after
```

Then run the tests and the evaluation again (`python -m pytest -q`, `python eval/run_eval.py`). A swap
is done when the numbers say so, not when the reply looks fine once.

## Add a backup provider

Add a second route after a comma.

```bash
LLM_UNDERSTAND="groq:llama-3.3-70b-versatile,cerebras:llama-3.3-70b,ollama:qwen2.5:7b"
```

What the chain does on each call:

- A key the provider refuses (401, 402, 403, or 429 for too many requests) rests for a while, and the
  next key of the same provider is tried. A 429 rests the key for as long as the provider asks (5 minutes
  at most), 30 seconds when it does not say. The others rest it for 15 minutes.
- A timeout, a network error or a 5xx gets one more try on the same route, then the next route.
- Any other refusal (400, 404 and the like) goes straight to the next route.
- An answer that is not valid JSON, or does not pass the schema, counts as a failure of that route.
  The next route is tried. It is not retried on the same route.
- The whole call lives inside one time budget (`timeout_s` of the call, 30 seconds by default). No try
  starts with less than half a second left. While a backup is still waiting, one try takes at most 60%
  of the time that is left, so a slow primary cannot eat the backup's share. Set `timeout_s` on a route
  to choose its limit yourself.
- When no route answers, the call returns `None` (`transcribe` raises `LLMError`), and the caller takes
  its offline path. It never guesses.

Two keys on one provider are a backup too: `LLM_PROVIDER_GROQ_KEYS="GROQ_API_KEY,GROQ_API_KEY_2"`.

## Run fully local with Ollama

No key, no network outside your machine. Pull open-weight models, then point the roles at them.

```bash
ollama pull qwen2.5:7b          # text: decide, understand, phrase
ollama pull gemma3:4b           # text and images: vision
ollama serve                    # listens on http://127.0.0.1:11434
```

```bash
LLM_DECIDE="ollama:qwen2.5:7b"
LLM_UNDERSTAND="ollama:qwen2.5:7b"
LLM_PHRASE="ollama:aya-expanse:8b,ollama:qwen2.5:7b"     # an Arabic-strong model first, a backup after it
LLM_VISION="ollama:gemma3:4b"
```

Other open-weight choices that fit the same lines: `llama3.2-vision:11b` or `qwen2.5vl:7b` for images,
`mistral-small:24b` or `llama3.1:8b` for text. A small machine can start with `qwen2.5:3b` for `decide`.

Speech needs a speech server. Ollama does not serve one, so add a local provider for it:

```bash
# a local Whisper server that speaks POST /v1/audio/transcriptions
LLM_PROVIDER_WHISPER_BASE="http://127.0.0.1:8000/v1"
LLM_PROVIDER_WHISPER_LOCAL=1
LLM_PROVIDER_WHISPER_OPTIONS="transcribe_body=multipart"
LLM_STT="whisper:whisper-large-v3"

# a local speech server that speaks POST /v1/audio/speech (optional)
LLM_PROVIDER_VOICE_BASE="http://127.0.0.1:8880/v1"
LLM_PROVIDER_VOICE_LOCAL=1
LLM_TTS="voice:<its model name>"
```

Leave `LLM_HEAR` and `LLM_TTS` unset if you have no model for them: `llm.has("hear")` is then false, so
an application can send a voice note through `stt` and then `understand`, and keep its replies as text.

LM Studio and llama.cpp work the same way: use `lmstudio:<model>` or `llamacpp:<model>`. If your
runtime listens somewhere else, set `LLM_PROVIDER_OLLAMA_BASE` (or the matching name).

## Check the setup

```bash
python -m llm check
```

It reads the configuration and prints every role's chain. It makes no call and never prints a key: only
the names of the key variables and whether each one is set.

```
decide: ready
  1. groq:llama-3.1-8b-instant  [primary]  usable: 1 of 2 key(s) set
       hosted, api.groq.com, json mode object
       key 1: $GROQ_API_KEY set
       key 2: $GROQ_API_KEY_2 missing
  2. ollama:qwen2.5:7b  [backup 1]  usable: local, no key needed
       local, 127.0.0.1:11434, json mode schema
understand: no usable route
  1. cerebras:llama-3.3-70b  [primary]  NOT usable: no key set
       hosted, api.cerebras.ai, json mode schema
       key 1: $CEREBRAS_API_KEY missing
phrase: not configured
vision: no usable route
  1. myhots:some-model  [primary]  NOT usable: unknown provider
hear: not configured
stt: not configured
tts: not configured
1 of 7 role(s) ready.
```

How to read it:

- **ready** means at least one route of the role is usable, so `llm.has(role)` is true.
- **usable** means the provider is known and it is local or has at least one key set. It says nothing
  about whether the model name exists or the key is valid: `check` is offline.
- **NOT usable: no key set** means the variable named under it is empty. Export it.
- **NOT usable: unknown provider** is usually a typo in the name before the colon, or a provider whose
  `LLM_PROVIDER_<NAME>_BASE` is missing.
- **not configured** means the role has no chain. That is fine for a role you do not use.

To test a role for real, one tiny call:

```bash
python -m llm ping decide
```

It prints which route answered, how long it took, the tokens and the cost. It is the only command here
that reaches a model, and the tests never run it against a network.

## Route options

Options follow the route after a question mark, or sit in the `options` object of the JSON file.

```bash
LLM_UNDERSTAND="groq:llama-3.3-70b-versatile?timeout_s=6&temperature=0,ollama:qwen2.5:7b?timeout_s=20"
```

| Option | Meaning |
|---|---|
| `timeout_s` | The longest one try on this route may wait |
| `temperature`, `max_tokens` | Override what the caller asked for (some models accept only one temperature) |
| `effort` | Sent as `{"reasoning": {"effort": ...}}`, only when set |
| `usd_per_mtok_in`, `usd_per_mtok_out` | Prices per million tokens, used to estimate cost when the provider reports none |
| `extra_body` | An object merged into the request body (JSON file only) |
| `json` | `schema`, `object` or `prompt` for this route, overriding the provider |
| `strict` | `false` stops sending the strict flag in schema mode |
| `retries` | `0` skips the one retry after a timeout or a 5xx |
| `transcribe_body` | `multipart` uploads the audio as a file (the Whisper-style endpoint) instead of a JSON body |

## How JSON is asked for

Three modes, set per provider (`LLM_PROVIDER_<NAME>_JSON`) or per route (`json=`):

- `schema`: the schema travels as the response format, and the endpoint constrains the answer to it.
- `object`: the endpoint is asked for a JSON object, and the schema is added to the system message.
- `prompt`: nothing special is asked of the endpoint. The schema is added to the system message and the
  JSON is read out of the text. This works with any model.

In every mode the answer is parsed (a code fence or words around the JSON are tolerated) and checked
in code: types, required fields, allowed values, no unknown fields, number and length limits. An answer
that fails is thrown away. If an endpoint refuses `schema` mode with a 400, the client asks once the
plainer way and remembers what worked.

## Cost and usage

`llm.stats()` returns, per role and per route: calls, HTTP requests, failures, fallbacks (how many of
the failures a later route covered), skipped, seconds, tokens in and out, and US dollars.
`llm.spent_usd` is the total. Cost is the provider's own figure when the answer carries one, otherwise
tokens times the route's prices, otherwise zero. A local model costs zero. Speech billed by the second is
recorded by the caller with `llm.add_cost(usd)`.

`llm.last` says how the current thread's most recent call went: which provider and model answered,
its place in the chain, and what failed before it. Use it to show where an answer came from.

## Secrets

- Configuration holds the **names** of key variables. A value that does not look like a variable name
  is refused, and the error does not repeat it.
- Keys are read from the environment at the moment of a call and sent in the `Authorization` header only.
- Errors and log lines name the provider, the key number and the status code. A key, a prompt or a
  response body never appears in them. The tests check this.
- A base address with a user, a password or a query string is refused.

## In tests

Tests never call a model. Use the fake:

```python
from llm.testing import fake_llm

llm = fake_llm(lambda body, request: {"label": "balance"})
assert llm.choose("decide", "شكد رصيدي", ["balance", "transfer"]) == "balance"
```

## Limits

- The schema checker covers the keywords this project uses, not the whole JSON Schema standard
  (see the top of `llm/schema.py`).
- Requests are not streamed, so a try's timeout is the wait for the whole answer.
- `check` cannot know whether a model name exists or a key is valid. `ping` can.
- A local model small enough for a laptop reads Iraqi Arabic less well than a large hosted one. Measure
  it with the evaluation before you rely on it, and keep the code checks on either way.

# Chat app

A mobile-first web chat over the same orchestrator the terminal demo drives (`agent/demo.py`).
The page is in Iraqi Arabic, right to left. Every word the agent says comes from the orchestrator;
the page only draws it: chat bubbles, the confirmation card, the PIN sheet and the receipts.

## Run it

```bash
pip install -r requirements.txt
uvicorn web.app:app --port 8080      # then open http://127.0.0.1:8080
python -m pytest -q web/tests        # the chat app's tests, offline
```

It runs offline with no keys. Each browser gets its own in-memory synthetic wallet, seeded from
`data/wallet_seed.json`, so two people showing the demo never see each other's payments.

To use another implementation of the core interface:

```bash
BILLPAY_CORE="package.module:ClassName" uvicorn web.app:app --port 8080
```

With `BILLPAY_CORE` unset, the open rule-based `StubCore` is used. The core is loaded through
`core/loader.py` when it is present.

## What the page does

- **User picker.** Tap the name at the top to talk as any of the 30 synthetic users. The sheet also
  holds the demo tools and the reset button.
- **Example chips.** The chips above the input are built from the picked user's own contacts and
  bills. They show a bill, a clear transfer, two contacts with the same name (the agent asks which),
  a top-up, a balance question, dollars (the agent asks for dinars), more than the balance (a refusal),
  two requests in one message and an out-of-scope request.
- **Confirmation card.** It shows the payee or biller, the amount in digits and in words, the fee,
  the total, the balance after, the soft-flag warnings and a live 5-minute countdown. The buttons are
  «إي، ادفع», «غيّر» and «لا، بطّلت».
- **Tap + PIN.** «إي، ادفع» opens a PIN sheet. The digits are masked, and Eastern digits typed on a
  keyboard (١٢٣٤) are accepted. A wrong PIN keeps the card open and moves nothing.
- **Receipts.** A receipt shows success or failure, from the wallet's own record. An unknown outcome
  is shown honestly as «لهسه ما نعرف النتيجة», with a check button. When the wallet answers, that
  receipt updates in place.
- **Options to tap.** A question with a closed set of answers (which Ahmed, which bill, which month, which of
  two amounts, yes or no) comes with chips under it, and so does the main menu (at the start, after «القائمة» or
  "menu", after a hello and after a payment). A chip sends only its id; the server looks it up against the open
  question, so a chip can answer or open a menu row but never pay. Typing the answer still works.
- **What I checked.** Under a card, a receipt and a refusal, «شلون تأكدت؟» opens the list of what the code
  checked on that turn, each with where it came from: the amount words and the rule that read them, how the payee
  matched, the balance, the daily limit, the wallet's quote, and after the tap the transaction read back from the
  wallet. Typing «ليش» or "why" says the same in the chat.
- **Wallet panel.** It shows the balance and the latest transactions. On a phone it opens from the
  clock button; on a wide screen it is always shown on the side.
- **Demo tools.** Each one fires once, on the next payment:
  - the answer is lost after the payment (the same-key retry pays once);
  - the biller does not answer (nothing is taken);
  - the network drops completely (the honest UNKNOWN, then the real outcome on the next message).
- **Voice notes.** The microphone button records with `MediaRecorder` when the loaded core has a voice
  path (`transcribe(audio, mime, context)`, used through `Session.handle_voice`). With the stand-in
  core, it shows a short note that voice notes need the full version of the assistant.
- **Voice replies.** When the core can also speak, a reply carries `voice_reply` (base64 audio) under
  `VOICE_REPLY_MODE` (`mirror` by default: only when the user used the microphone; `always`; `off`), and
  the page plays it in a small player above the text and the card.
- **Languages.** Each reply says its language (`reply.lang`: `iq`, `msa` or `en`). The card and the
  receipt come with their labels in that language, English ones are drawn left to right, and the input
  placeholder, the PIN sheet and the closing labels follow.
- **Accessibility.** Every target is at least 44 px, focus rings are visible, and new messages are
  announced (`role="log"`, `aria-live`). Reduced motion, reduced transparency, more contrast and dark
  mode are all respected. Tested at 390 px and 1440 px.

## API

All calls send the browser's session id in the `X-Demo-Session` header.

| Method and path | What it does |
|---|---|
| `GET /api/config` | `{voice, pin_length, faults, speaks, voice_reply_mode}` |
| `GET /api/users` | The synthetic users, without PINs |
| `POST /api/start` `{user_id}` | Talk as this user. Returns the profile, example chips, history, the demo PIN hint and the menu |
| `POST /api/message` `{text}` | `Session.handle_text` |
| `POST /api/button` `{quote_id, button, pin?}` | `Session.handle_button`; `button` is `confirm`, `change` or `cancel` |
| `POST /api/option` `{option_id}` | `Session.handle_option`: a tapped chip, resolved on the server. It never pays |
| `POST /api/explain` | `Session.explain`: the last turn's checks as lines |
| `POST /api/voice` (raw audio body) | `Session.handle_voice` when the core can hear; otherwise a note |
| `POST /api/check` | `Session.reconcile`: re-check payments whose outcome was unknown |
| `GET /api/history` | The profile and the latest transactions |
| `POST /api/reset` | A fresh wallet and agent for this browser |
| `POST /api/demo-fault` `{mode}` | `none`, `drop_after_commit`, `biller_down` or `network_down` |

Each reply has the same shape: `{reply: {lines, kind, state, quote_id, ask_field, lang, options, options_title,
options_role, evidence, why_label, reference}, card, receipt, resolved, profile}`. `options` are
`{id, title, description}`; `options_role` is `answer` (they answer the question) or `next` (the menu).
`evidence` is a list of `{what, value, source, from, line}`: the check's name, its plain values, where they came
from (a code and its label in the reply's language) and one line to read. `POST /api/start` and `/api/reset`
also return `menu`, a reply of that shape holding the main menu.

## Deploy later

`web/Dockerfile` builds from the repository root (`docker build -f web/Dockerfile .`). On Railway, set
the service's config file path to `web/railway.json`. The demos live in memory, so run one replica.

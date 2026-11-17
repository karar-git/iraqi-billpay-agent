# WhatsApp channel

The same orchestrator as the web chat and the terminal demo (`agent/session.py`), over the WhatsApp
Cloud API, with **real accounts**: the WhatsApp number a person writes from is their account. Every word
about money comes from the orchestrator and `payments/messages.py`; account words (sign-up, contacts,
history, notices) from `accounts/texts.py`; this channel turns replies into WhatsApp messages, all in the
sender's language: Baghdadi Arabic, Modern Standard Arabic (for MSA and any other Arabic dialect) or English.

**The money is a mock ledger.** Balances are IQD inside this service's database (`DATA_DIR/accounts.db`),
moved only by `wallet/` with double-entry records and an audit. Nothing reaches a payment network or a
biller yet. People see a real account; the sign-up offer and every bill or top-up receipt say plainly that
balances and payments stay inside the service, and the admin page says it on every page.

## Run it

```bash
pip install -r requirements.txt
python -m pytest -q channels/whatsapp/tests        # offline: a fake Graph API, the stand-in core
uvicorn channels.whatsapp.app:create_app --factory --port 8090
```

Then point the WhatsApp number's webhook at `https://<your host>/webhook` with the same verify token
as `WHATSAPP_VERIFY_TOKEN`. `GET /healthz` answers 200 once every required setting is there (and the
PIN pepper), and 503 with the names of the missing ones otherwise; it also reports the accounts schema
version and the number of accounts.

## Settings

| Variable | What it is |
|---|---|
| `WHATSAPP_TOKEN` | Access token for the Cloud API (a system-user token). Required. |
| `WHATSAPP_APP_SECRET` | The Meta app's secret, to check `X-Hub-Signature-256`. Required: without it every webhook POST is refused. |
| `WHATSAPP_VERIFY_TOKEN` | The token of the webhook handshake. Required. |
| `WHATSAPP_PHONE_NUMBER_ID` | The number this channel answers for. Messages to other numbers of the same business account are ignored. Required. |
| `GRAPH_API_VERSION` | Default `v25.0`. |
| `DATA_DIR` | Where everything lives: `DATA_DIR/accounts.db`, `DATA_DIR/backups/`, `DATA_DIR/whatsapp/channel.db` (default `data/`). Use a volume in production. |
| `ACCOUNTS_DB` | The accounts database, default `DATA_DIR/accounts.db`. |
| `ACCOUNTS_PIN_PEPPER` | Secret mixed into every PIN hash (HMAC before PBKDF2). Required in production; losing it means every PIN needs an admin reset. |
| `PIN_HASH_ITERATIONS` | PBKDF2 iterations for account PINs, default `310000`. |
| `ADMIN_USER` / `ADMIN_PASSWORD` | The admin page's Basic login (default user `karar`). No password (and no `ADMIN_PASSWORD_HASH`): every `/admin` path is 404. |
| `ADMIN_PASSWORD_HASH` | Optional instead of `ADMIN_PASSWORD`: a `pbkdf2_sha256$…` hash. |
| `SIGNUP_MODE` | `open` (default), `invite` (only numbers added on the admin page), `closed`, `demo` (the first message opens a demo account with pretend money, a short past and a PIN shown once, then is answered; senders whose WhatsApp hides the number get one too; see `demo.py`). A demo account's transfers, to any number, are paid to `external:` only: never a real account, never a hold. In demo mode the inbox keeps any text with a four-digit number redacted. Any other value is refused at start-up. |
| `VOICE_APPROVAL` | `1` switches voice approval on (see "Voice approval" below). Off by default. On only with a working speaker model: without `VOICEPRINT_MODEL`, the `sherpa-onnx` package or ffmpeg it stays off, one log line says what is missing, and nothing (the demo intro included) mentions it. |
| `VOICE_MATCH_THRESHOLD` | Cosine similarity a voice note must reach against the kept voice, default `0.70`. Refused at start-up below `0.5` or at `1` and above. |
| `VOICE_APPROVAL_MAX_IQD` | The most one voice-approved payment may be, default `100000`. |
| `VOICEPRINT_MODEL` | Path of the speaker model: `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx` (3D-Speaker CAM++, about 28 MB, from the k2-fsa/sherpa-onnx release `speaker-recongition-models`). `VOICEPRINT_FFMPEG` may name the ffmpeg binary (default: `ffmpeg` on the PATH). |
| `HUMAN_PACE` | On by default: a short typing pause before each text bubble (at most 2 s). `0` switches it off. |
| `SIGNUP_CREDIT_IQD` | A grant credited at sign-up (from the cash account), default `0`. |
| `DEFAULT_DAILY_LIMIT_IQD` | The daily limit of a new account, default `1000000`. |
| `HOLD_DAYS` / `MAX_OPEN_HOLDS` / `HOLD_CAP_IQD` | Money for a number with no account waits `7` days; at most `5` open holds per sender and `1000000` IQD held per number. |
| `PIN_RESET_COOLDOWN_IQD` | What may go out (transfers and top-ups) in the 24 h after an admin PIN reset, default `100000`. |
| `BACKUP_KEEP_DAYS` / `BACKUP_HOUR` | Daily backups kept `14` days, written at the first background tick after `4` (Baghdad). |
| `SERVICE_NUMBER` | This channel's own number, for the wa.me link in hold receipts, default `9647714899059`. |
| `META_PRICE_USD` | Optional price per outbound message, only for the admin's estimate. |
| `BILLPAY_CORE` | The core, `module:Class` (see `core/loader.py`). Unset: the open stand-in. |
| `DAILY_BUDGET_USD` | Hard daily cap (Baghdad day) on the core's spend, default `2`; `0` switches it off. Over the cap, texts and voice notes get «الخدمة مرتاحة لباجر» once and no paid work is done; a tap or PIN on an open card still finishes. |
| `MAX_MSGS_PER_USER_HOUR` | Messages per sender per hour, default `60`. Past it the sender hears it once, then nothing until the hour is over. |
| `ALLOWED_NUMBERS` | Optional comma-separated allow-list (digits). Empty (or `*`) means everyone. |
| `MANAGER_WHATSAPP` | Optional number that gets a line on a new account, a PIN-reset request, a blocked PIN and the budget cap (WhatsApp only delivers it inside the manager's 24-hour window). |
| `DEBOUNCE_SECONDS` | Quiet time that joins a burst of texts into one message, default `4`. |
| `VOICE_REPLY_MODE` | `mirror` (default): a voice note is answered with a voice note too; `always`: every reply; `off`: never. |
| `WHATSAPP_SENDER_SALT` | Secret key of the hash that names each sender in the channel's own store (HMAC-SHA256). Empty: the app secret. |
| `CARD_WHY_BUTTON` | `1` (default): a one-button «شلون تأكدت؟» message right under every card; `0`: none (one message less per card; typing «ليش» still works). |
| `INSTAGRAM_*` | Optional: the Instagram door on the same app, see "Instagram" below and `channels/instagram/README.md`. |

`WHATSAPP_DEMO_USER` is gone: there is no demo user any more.

## Provider: Meta or YCloud

The channel talks to Meta's Cloud API unless `WHATSAPP_PROVIDER=ycloud` is set; with it, the same number is sent and
received through YCloud (`ycloud.py`) on the same `/webhook`. Accounts, the sender hash and the rest of the channel
stay as they are: keep `WHATSAPP_SENDER_SALT` (or the app secret it falls back to) and `SERVICE_NUMBER` unchanged.

| Variable | What it is |
|---|---|
| `WHATSAPP_PROVIDER` | `meta` (default) or `ycloud`. Anything else stops the service at start-up. |
| `YCLOUD_API_KEY` | Sent as `X-API-Key`, only to `https://api.ycloud.com`. Required with `ycloud`. |
| `YCLOUD_WEBHOOK_SECRET` | The webhook endpoint's `whsec_…` secret, to check `YCloud-Signature`. Required with `ycloud`: without it every webhook POST is refused. |
| `YCLOUD_FROM` | The number to send from, with its country code or as a local Iraqi number (`0771 489 9059`). A form that cannot be sent from counts as missing. Only messages addressed to it are read; the rest are dropped with a log line. Required with `ycloud`. |

With `ycloud`, `/healthz` names these instead of the `WHATSAPP_*` ones, and only `YCloud-Signature` is accepted, with
a timestamp at most 5 minutes off; with `meta`, only `X-Hub-Signature-256`. A provider still posting to `/webhook`
after the switch gets 403, so turn its webhook off.

Subscribe the YCloud endpoint to `whatsapp.inbound_message.received`. Other events are answered 200 and ignored:
messages typed in the WhatsApp Business app, history sync and delivery updates are never read as a user's message.
Neither are system notices and messages in a group, which arrive under that event: a person who changes their number
starts over on the new one, as on Meta, and their old account stays on the old number.
Voice notes are downloaded from the event's link, and voice replies are uploaded through YCloud; if an upload fails,
the person still gets the text.

## What it does

- **Webhook.** `GET /webhook` answers Meta's handshake. `POST /webhook` checks the signature (403 when
  it is wrong or missing), ignores delivery statuses, drops messages older than 15 minutes and takes each
  message id once, so Meta's retries are never handled twice. It answers 200 at once; the work runs in the
  background, one sender at a time (a burst of texts within `DEBOUNCE_SECONDS` is one message).
- **Sign-up** (`onboarding.py`). A number with no account gets the offer (in the language of its first
  message) with «افتح حساب» / «مو هسه», then: the name (their WhatsApp profile name offered, or typed;
  letters only), how to address them (رجّال / امرأة), a PIN of 4 to 6 digits typed twice. Weak PINs (1234,
  0000, the end of their own number, common ones) are refused; the first PIN is kept only as a hash. Done is
  one wallet transaction: the account, the optional grant, every transfer held for that number, the notices.
  «الغاء» cancels; an idle sign-up restarts after 24 h; a voice note during sign-up gets «type it, please».
  A username-only WhatsApp account cannot open one. A person added on the admin page only sets a PIN.
- **Recognition.** Every later message is matched to the account by its number. «هلا» gets
  «صباح الخير {name}! رصيدك …». Waiting notices go out first.
- **Account commands** (`agent/manage.py`, no model, nothing sent out): «ضيف أحمد 07701234567» /
  «احفظ 0770… باسم أحمد» / «add Ahmed 0770…», a number alone («احفظه» · «حوّل له» · «لا»), «أسمائي»,
  «سمّي 4567 حمودي», «غيّر اسم أحمد إلى حمودي», «امسح أحمد»; «ضيف حساب المولدة 350» (this month's bill
  appears at once from the mock biller feed), «فواتيري»; «آخر العمليات» / «كشف» / «history» (in and out,
  and today's spend against the limit); «حسابي»; «غيّر اسمي»; «غيّر الرمز» (current PIN, counted towards the
  lockout, then the new one twice); «نسيت الرمز» (a reset request to the admin; the PIN is a hash nobody can
  read); «الرمز» says it is secret; «مساعدة».
- **Transfers to any Iraqi number.** «دز 25 الف لـ 0770 123 4567». Numbers are replaced by placeholder
  words before the core reads the message (it never sees a phone number, and a number is never read as an
  amount). A number with an account gets the money at once; the card shows the registered short name
  («سارة ك.») and the last 4 digits; afterwards the number is saved to the sender's contacts. A number with
  no account gets a hold: the card says the money waits 7 days, the receipt has a wa.me link to forward;
  it reaches them when they sign up, else it all comes back, fee included.
- **Notices** (`notify.py`). Money received, a hold claimed or refunded, an admin credit or debit, a freeze:
  sent at once inside the person's 24-hour window, otherwise as the first lines of their next conversation.
- **The card.** An interactive message: title, the recipient (or the biller, account and month), the
  amount in digits and words, the fee and total, the balance after, the warnings, the 5-minute expiry in
  the footer, and three reply buttons carrying the quote id: «إي، ادفع» · «غيّر» · «لا، بطّلت» (or the MSA
  and English ones).
- **«شلون تأكدت؟» under the card.** Three reply buttons are all one WhatsApp message may hold, and none of the
  card's may go, so a second message follows right under it: «تريد تعرف شنو فحصت قبل البطاقة؟» with one button,
  «شلون تأكدت؟» / «كيف تحققت؟» / "How did you check?" (20 characters at most). Its id is `opt_why:<quote id>`, tied
  to that card: the tap says what was checked before that card (recorded when it was sent), and once the card is no
  longer open (paid, cancelled, replaced, expired) one line says so instead of explaining some later request; typing
  «ليش» still explains the last request. Nothing else is touched. The card stays open, and so does its PIN step
  after «إي، ادفع». A card sent again (after
  «tap first») brings no second one. `CARD_WHY_BUTTON=0` leaves it out: on Meta's per-message pricing it is one
  message more per card.
- **Tap, then the PIN.** «إي، ادفع» asks for the PIN as a message. That message goes straight to
  `Session.handle_button` with the card's quote id; it never reaches the core. A PIN-shaped message never
  reaches the orchestrator while a card is open, right after the tapped card was voided, or when nothing
  was asked at all. 5 wrong PINs in a row lock it for 15 minutes; the 3rd lockout within 24 hours blocks it
  until an admin reset (after which transfers are capped for 24 h). A second tap on a paid card never pays
  again and shows its receipt. Typing «إي» never pays.
- **Account states.** A frozen account reads but cannot pay (incoming money still arrives); a closed one
  gets one line. Frozen, closed, PIN not set and PIN blocked are refused before any card, and again by the
  wallet at execute.
- **Voice notes.** Downloaded as soon as they arrive; heard by the core with the user's saved names,
  bill labels and billers and what was just asked (`hear` in `core/interface.py`, else `transcribe`); read
  like typed text. «سمعتك: …» is shown only before a question or a refusal. A voice note is answered with a
  voice note too (`VOICE_REPLY_MODE`); amounts and the ends of numbers are said in words, never a PIN.
- **Voice approval** (`VOICE_APPROVAL=1`, `accounts/voiceprint.py`, `Wallet.grant_voice` / `enroll_voice`). The owner's
  voice, kept as a speaker vector, can stand in for the PIN on small payments to known payees.
  - *What is kept.* A voice note is decoded to 16 kHz with ffmpeg and turned into a 192-number speaker vector by the
    CAM++ model, on this server (sherpa-onnx, CPU, a few hundred ms); the audio is dropped. Only vectors are stored
    (`voiceprints`, at most 3 per account, with the model's name), never audio. A note with less than 1.5 s of speech
    gives no vector.
  - *When it is kept.* Only from a voice note whose card was then paid with the right PIN: the PIN proves the
    speaker is the owner, and the wallet checks the quote was SUCCEEDED and approved by the PIN. A payment asked for
    in text keeps nothing. The first time, one line says that later voice notes can approve a payment up to
    `VOICE_APPROVAL_MAX_IQD` and that «امسح صوتي» deletes it. The first three such samples are kept (later ones are
    not, so the kept voice does not drift); a voice note is compared with their mean.
  - *When it pays.* A later voice note makes a card; the wallet compares its vector with the kept voice (the mean
    of the samples) and, when it matches and every rule holds, records a one-time grant bound to that quote (its
    amount and payee). The card then says «صوتك تطابق ✅» and «إي، ادفع» pays with no PIN: the code-checked card and
    the tap stay the safety step. The rules, checked again inside the wallet at execute (`factor="voice"`): the
    feature on, the grant unused and for this amount and payee, the amount at most the cap, the voice still kept, the
    account active with its PIN set and not locked, and a known payee: a number this account has paid before, or a
    contact or bill account saved at least 24 h ago (or made by the system); a top-up of the own number or of a
    number topped up before. A contact saved a moment ago does not count: someone holding an unlocked phone could
    save their own number under a trusted name and play back an old voice note of the owner's. Every use writes an
    audit row `voice_approved` with the score (`voice_checked` records every comparison).
  - *Never.* A forwarded voice note (`context.forwarded`) or an audio file (not a WhatsApp voice note) never
    approves and never enrols; nor does an Instagram voice clip. A voice note while the PIN is asked for is still
    never heard and never pays. Over the cap, an unknown payee, another voice, a locked PIN, the flag off: the PIN, as
    before. If the wallet refuses the grant at the tap (the voice was deleted meanwhile, say), nothing moves and the
    PIN is asked for on the same card. No HTTP route can make a grant.
  - *Deleting.* «امسح صوتي» / «احذف صوتي» / "delete my voice" (typed or said) deletes the vectors and voids every open
    grant; closing an account deletes them too. The admin's People page marks accounts with a kept voice («voice»),
    and the person's page can clear it (with a reason, audited).
- **Instagram** (`channels/instagram/`). With `INSTAGRAM_TOKEN`, `INSTAGRAM_APP_SECRET`, `INSTAGRAM_VERIFY_TOKEN`
  and `INSTAGRAM_ACCOUNT_ID` set, the same app also answers `GET/POST /instagram/webhook` (otherwise both are 404,
  and one log line at start names what is missing). The webhook is checked with Instagram's app secret (403
  otherwise, whatever `WHATSAPP_ALLOW_UNSIGNED` says), answered with 200 at once, deduplicated on the message id
  and handled by this channel, one sender at a time. Inside the channel an Instagram sender is «ig:<id>», never a
  phone number. «اربط انستغرام» / "link instagram" here gets a one-time code (6 digits, 10 minutes, 3 an hour);
  typed in the Instagram chat it links that chat to this account, and this chat is told («افصل انستغرام» cuts it,
  from either chat). A linked chat is the same account (balance, contacts, bills, holds, limits, the PIN) in a
  conversation of its own: its own card, question and PIN step, so a PIN typed in one chat never confirms the
  other's card, and two turns of one account never run at the same time. The card, the tap and the PIN work as
  here. Waiting notices go out in the chat the person writes from. No voice reply is made there (nothing is spent
  on speech; the person is told once). Sign-up, changing or resetting the PIN and the identity document stay here.
- **Admin** at `/admin` (`accounts/admin`): see the main README.
- **Background jobs** (every 10 minutes): expire holds, deliver waiting notices, the daily backup.
- **Restarts.** Accounts live in `DATA_DIR/accounts.db`; seen message ids, the PIN step, sign-ups in
  progress, rate counts and the day's spend in `DATA_DIR/whatsapp/channel.db`. A message taken but not
  answered before a restart is answered after it (the inbox row, which may hold a PIN, is deleted as soon
  as the turn ends). Cards live in memory: after a restart an old card's buttons say it no longer works.
- **From the demo.** The store's schema v2 deleted every demo sender row; the old per-sender synthetic
  wallets (`DATA_DIR/whatsapp/wallets/`) are set aside as `wallets.dropped-YYYYMMDD` and deleted after 7 days.

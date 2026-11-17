# Instagram direct messages

The same agent, reached from an Instagram chat. This folder holds the webhook handshake and signature, parsing what a
person sent, sending replies inside Instagram's limits, linking an Instagram sender to a wallet account, and the
wiring that puts all of it on the WhatsApp channel's app (see "How it is wired").

```bash
python -m pytest -q -p no:cacheprovider channels/instagram/tests     # offline, no keys, no network
```

## What was checked against Meta's documentation (read on 2026-11-13)

Everything below was read on developers.facebook.com on that day. Limits in the code are these numbers and nothing
else. What could not be confirmed is listed after it as UNVERIFIED, with what the code does about it.

### Three set-ups, two hosts

| | Instagram Login | Facebook Login (Messenger Platform) | Unified Login |
|---|---|---|---|
| Host | `graph.instagram.com` | `graph.facebook.com` | `graph.facebook.com` |
| Send endpoint | `POST /{version}/{instagram-account-id}/messages` (`/me/messages` also documented) | `POST /{version}/{page-id}/messages` (`/me/messages` also documented) | not shown on the pages read |
| Token | Instagram User access token (from the dashboard: long-lived, 60 days; from the login flow: 1 hour) | Page access token of the linked Facebook Page | Business Integration System User access token |
| Token in the docs' examples | `Authorization: Bearer ...` header | `?access_token=...` in the URL | `access_token` parameter |
| Messaging permissions | `instagram_business_basic`, `instagram_business_manage_messages` | `instagram_basic`, `instagram_manage_messages`, `pages_manage_metadata` (the webhook table adds `pages_read_engagement`, `pages_show_list`) | `instagram_basic`, `instagram_manage_messages` |
| Facebook Page needed | no | yes | no |

The docs' examples use API version `v27.0`. An app uses Facebook Login or Instagram Login, not both. The overview
calls Unified Login "the recommended setup for new integrations" and says new features come only to it; it does not
say that the other two are closed to new apps. This channel was built against Instagram Login (one account we own,
one token from the dashboard, no Page) and can be pointed at `graph.facebook.com`.

Sources:
- https://developers.facebook.com/documentation/instagram-platform/overview
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/get-started
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/get-started
- https://developers.facebook.com/documentation/business-messaging/instagram-messaging/features/send-message
- https://developers.facebook.com/documentation/instagram-platform/app-review

### Webhooks

- **Handshake.** Meta calls the callback with `GET ?hub.mode=subscribe&hub.challenge=<int>&hub.verify_token=<ours>`.
  The endpoint checks the token and answers with the `hub.challenge` value.
- **Signature.** Every POST carries `X-Hub-Signature-256: sha256=<hex>`, an HMAC-SHA256 of the payload made with the
  app secret. Compare with everything after `sha256=`.
- **Delivery.** Answer `200` at once, before slow work. Failed deliveries are retried with decreasing frequency and
  dropped after 36 hours, so the receiver must take a repeated message once. Up to 1000 updates can come in one
  batch. The app must be Live to get notifications for app users.
- **Subscribing the account.** `POST /{version}/{instagram-account-id}/subscribed_apps?subscribed_fields=...` on
  `graph.instagram.com` (Instagram Login), or `POST /{version}/{page-id}/subscribed_apps` on `graph.facebook.com`
  (Facebook Login). Success is `{"success": true}`.
- **Fields.** `messages`, `messaging_postbacks`, `messaging_seen`, `message_reactions`, `messaging_referral`,
  `messaging_handover`, `messaging_optins`, `standby`, `message_echoes`. With Instagram Login each needs
  `instagram_business_basic` and `instagram_business_manage_messages`.
- **Payload.** `{"object": "instagram", "entry": [{"id": <our account id>, "time": ..., "messaging": [...]}]}`.
  Each item of `messaging` has `sender.id` (the Instagram-scoped id of the person), `recipient.id` (our Instagram
  professional account id), `timestamp`, and one of:
  - `message`: `mid`, `text`, `attachments[]` (`type`, `payload.url`), `quick_reply.payload`, `is_echo` (sender and
    recipient are swapped: we sent it), `is_self`, `is_deleted`, `is_unsupported`, `reply_to` (`mid`, or `story.url`
    and `story.id`), `referral`.
    Attachment types: `audio`, `file`, `image` (a picture or a sticker), `video`, `share`, `ig_post`, `ig_reel`,
    `reel`, `story_mention`, and view-once media as `ephemeral` with no link.
  - `postback`: `mid`, `title`, `payload` (a tap on a template button, an ice breaker or the menu).
  - `read`: `mid`.
  - `reaction`: `mid`, `action` (`react` or `unreact`), `reaction`, `emoji`.
  - `message_edit`: `mid`, `text`, `num_edit`.
- **A tapped quick reply** arrives as a `message` whose `text` is the title and whose `quick_reply.payload` is the
  payload.
- **Attachment links.** Meta's sample payloads use `https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=...`.

Sources:
- https://developers.facebook.com/documentation/instagram-platform/webhooks/setup
- https://developers.facebook.com/documentation/instagram-platform/webhooks/fields
- https://developers.facebook.com/documentation/instagram-platform/webhooks/examples
- https://developers.facebook.com/documentation/business-messaging/instagram-messaging/webhooks
- https://developers.facebook.com/documentation/instagram-platform/webhooks/new

### Sending

All requests are `POST .../messages` with `Content-Type: application/json` and `recipient: {"id": <IGSID>}`. A
success answers `{"recipient_id": ..., "message_id": ...}`.

| Message | Shape | Limits in the docs |
|---|---|---|
| Text | `message: {"text": ...}` | UTF-8, 1000 bytes or less |
| Quick replies | `message: {"text": ..., "quick_replies": [{"content_type": "text", "title": ..., "payload": ...}]}` | at most 13; title up to 20 characters before it is cut; plain text only (phone and email quick replies are disabled); not shown on desktop; the chips go away when one is tapped |
| Button template | `message: {"attachment": {"type": "template", "payload": {"template_type": "button", "text": ..., "buttons": [...]}}}` | text up to 640 characters; 1 to 3 buttons; `web_url` or `postback` |
| Generic template | `... "template_type": "generic", "elements": [...]` | at most 10 elements; title 80; subtitle 80; at most 3 buttons per element; `postback` and `web_url` only; not shown on desktop |
| Postback button | `{"type": "postback", "title": ..., "payload": ...}` | title 20 characters, payload 1000 characters (Messenger Platform button reference) |
| Attachment | `message: {"attachment": {"type": "audio" or "image" or "video" or "file", "payload": {"url": ...}}}` | audio: aac, m4a, wav, mp4, 25 MB; image: png, jpeg, 8 MB; video: mp4, ogg, avi, mov, webm, 25 MB; file: pdf, 25 MB |
| Sender action | `sender_action: "mark_seen"`, `"typing_on"` or `"typing_off"`, with `recipient` and nothing else | `mark_seen` marks the most recent message; typing turns off after 20 seconds or when a reply is sent |

- **Messaging window.** We may message a person only after they wrote to us, and then for 24 hours after their last
  message (up to 7 days when the first message came from a click-to-direct ad).
- **Human agent tag.** `messaging_type: "MESSAGE_TAG"` with `tag: "HUMAN_AGENT"` lets a person on our side answer up
  to 7 days after the customer's last message. It is the only tag Instagram has, and the feature needs approval.
- **Rate limits.** Send API: 100 calls a second per account for text, links, reactions and stickers, 10 a second for
  audio and video. Conversations API: 2 a second. Error code `613` is "calls to this api have exceeded the rate
  limit".
- **Access.** Standard Access covers an app for accounts we own or manage, without App Review. Advanced Access (to
  serve accounts we do not own) needs App Review and Business Verification.
- **Uploading.** The Attachment Upload API (`POST /{page-id}/message_attachments` with `platform: "instagram"`) is
  documented for Facebook Login on `graph.facebook.com`. It takes the same formats as above.

Sources:
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api/quick-replies
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api/button-template
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api/generic-template
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api/sender-actions
- https://developers.facebook.com/docs/messenger-platform/instagram/features/quick-replies
- https://developers.facebook.com/docs/messenger-platform/instagram/features/button-template
- https://developers.facebook.com/docs/messenger-platform/instagram/features/generic-template
- https://developers.facebook.com/docs/messenger-platform/instagram/features/sender-actions
- https://developers.facebook.com/documentation/business-messaging/messenger-platform/send-messages/buttons/postback
- https://developers.facebook.com/documentation/business-messaging/messenger-platform/send-messages
- https://developers.facebook.com/docs/graph-api/reference/page/messages/
- https://developers.facebook.com/documentation/business-messaging/instagram-messaging/features/attachment-upload

### UNVERIFIED (and what the code does about it)

| Not confirmed in the pages read | What the code does |
|---|---|
| The unit of `timestamp`. The samples have 13 digits and no page names the unit. | `parse` reads 13 digits as milliseconds and 10 as seconds. |
| A `messages` event wrapped as `entry[].changes[] = {"field": "messages", "value": {...}}`. The examples page shows this envelope for some login types, but no message example inside it. | `parse` reads both envelopes. |
| `Authorization: Bearer` on `graph.facebook.com`. The Facebook Login and Unified Login pages show the token only as an `access_token` parameter. | The client sends the header on both hosts and never puts the token in a URL. If Meta refuses the header there, `InstagramClient._post` is the one place to change. |
| The send endpoint with Unified Login. No page read shows a message being sent with it. | On `graph.facebook.com` the client posts to `/{INSTAGRAM_ACCOUNT_ID}/messages`, or to `/{page_id}/messages` when a `page_id` is given (Facebook Login). |
| Whether Instagram Login is still offered to a new app in the dashboard. | Nothing in code. If it is not, use Unified Login or Facebook Login and set `INSTAGRAM_API_BASE=https://graph.facebook.com`. |
| The "allow access to messages" switch for connected tools in the Instagram app. Older guides name it; the pages read do not. | Listed as a setup step to check when no webhook arrives. |
| Which number is the account id with Instagram Login when two ids exist for one account. | Set `INSTAGRAM_ACCOUNT_ID` to the id that arrives as `recipient.id` in a real webhook. `parse` drops (and logs) what is addressed to another id. |
| The payload limit of a quick reply on Instagram (the Instagram page gives none), and that the 20 and 1000 of the Messenger postback button hold on Instagram (that page does not name Instagram). | Titles are cut to 20, a payload over 1000 characters raises `ValueError`. Our ids are far shorter. |
| Whether a button template shows on Instagram's web version. The Facebook Login page says it does not, the Instagram Login page says nothing. | Nothing. A person on the web version may see a card without buttons. Typing never pays, so no money is at risk; they continue on the phone. |
| The exact request of a `HUMAN_AGENT` message (the parameter names come from the Page Messages reference, no Instagram page shows the request), and the rule that automated messages may not use it (seen only in a search summary of the policy page). | The tag is never sent. An agent is automated. |
| The error code for a message sent outside the 24-hour window. | No special case: `ApiError` carries `status`, `code` and `subcode`. |
| A size limit for inbound media. | Our own cap, 16 MB (`max_media`). |
| CDN hosts other than `lookaside.fbsbx.com`. | Downloads are allowed from `*.fbsbx.com`, `*.fbcdn.net` and `*.cdninstagram.com`, over https only, without the token. Change `cdn_hosts` if a real link names another host. |
| The format of an inbound voice message (likely mp4 audio). | `media()` returns the type the CDN names. The wiring must check that the speech model takes it. |
| Whether an own-account app with Standard Access can answer people who have no role on the app. The Instagram Login page says Standard Access is enough for accounts we own; the Facebook Login send page says "Apps with Standard Access can only send messages to people that have a role on the app". | Plan for App Review before a public pilot. Testers with a role on the app work either way. |
| `messaging_type` on `graph.instagram.com` (its pages never show it). | Sent as `RESPONSE` only on `graph.facebook.com`, as that login type's examples do. |

## Setting up the Meta app

1. An Instagram **professional** account (business or creator). If no webhook arrives later, check in the Instagram
   app that connected tools may access messages (UNVERIFIED, see above).
2. In the Meta app dashboard create an app of type **Business**. In its left menu open Instagram, then **API setup
   with Instagram business login**. (Facebook Login needs a linked Facebook Page; Unified Login needs an app owned by
   a business portfolio. Both use the other host, see the table above.)
3. Permissions: `instagram_business_basic` and `instagram_business_manage_messages`. Click **Generate token** next to
   the Instagram account, log in to Instagram and copy the token: that is `INSTAGRAM_TOKEN`. A token from the
   dashboard is long-lived and valid for 60 days; Meta's "Refresh Access Token" reference says how to extend it.
4. Webhook: callback URL `https://<your host>/instagram/webhook` (the path is the wiring's choice), verify token =
   `INSTAGRAM_VERIFY_TOKEN`. Subscribe the fields `messages` and `messaging_postbacks`. `messaging_seen` and
   `message_reactions` are not needed: the parser drops them.
5. Subscribe the account: `POST /{version}/{account id}/subscribed_apps?subscribed_fields=messages,messaging_postbacks`
   on `graph.instagram.com`, with the token.
6. Set the app Live. With Standard Access the app serves the account we own. For anything wider, and to be safe
   before a public pilot (see UNVERIFIED), go through App Review for Advanced Access, which also needs Business
   Verification.

Settings (all from the environment, none in a file):

| Name | What |
|---|---|
| `INSTAGRAM_TOKEN` | the account's access token. Sent only to `INSTAGRAM_API_BASE`, in a header |
| `INSTAGRAM_APP_SECRET` | checks `X-Hub-Signature-256` |
| `INSTAGRAM_VERIFY_TOKEN` | the webhook handshake |
| `INSTAGRAM_ACCOUNT_ID` | the professional account's numeric id (what webhooks name as `recipient.id`) |
| `INSTAGRAM_API_BASE` | `https://graph.instagram.com` (default) or `https://graph.facebook.com`. Anything else stops the app at start. With Facebook Login also pass the Page id to the client (`page_id=`) |
| `INSTAGRAM_API_VERSION` | default `v27.0` |
| `INSTAGRAM_LINK_FLOOD_PER_HOUR` | wrong link codes from all senders in an hour before the flood brake is on, default `100`; `0` switches it off. Not a whole number: the app does not start |

`Settings.from_env().ready` is true when the first four are set. `repr(Settings)` never shows a secret.

## What works

- **Text**, in the three languages. A reply over 1000 bytes (about 500 Arabic letters) goes out as several messages,
  cut at line ends.
- **The confirmation card** (`send_buttons`): a button template with up to three postback buttons. The tap comes back
  as kind `"button"` with `"<action>:<quote id>"`, exactly like a WhatsApp reply button. A card over 640 characters
  sends its first lines as text and keeps the buttons under the last part. Header and footer become the first and
  last line.

  Why a template and not quick replies: template buttons are part of the card's own message, while quick replies are
  chips that the docs say are dismissed as soon as one is tapped (whether they also go when another message follows is
  UNVERIFIED for Instagram); the tap is its own `postback` event and not a message with a text; and 640 characters
  hold more Arabic than 1000 bytes do.
- **Lists** (`send_list`): a text with quick replies, up to 13 rows. A quick reply shows only a title of 20
  characters, so each row's description (and a title too long for the chip) is written in the text. More than 13 rows
  raise `ValueError`: a choice is never dropped silently.
- **Voice messages in**: an audio attachment arrives as kind `"audio"` with its link as `media_id`;
  `client.media(link)` downloads it (Meta's CDN hosts only, https, no token, 16 MB cap).
- **Images in**: kind `"image"` with the link as `media_id`. The identity document is read on WhatsApp only, so in
  the Instagram chat a photo gets the channel's one line about photos.
- **Seen and typing**: `mark_read` sends `mark_seen`, then `typing_on`.
- **Linking** an Instagram chat to a wallet account, below.

## What does not work

- **Voice replies.** Instagram takes audio only as a public link to an aac, m4a, wav or mp4 file. The agent's voice is
  an ogg file that WhatsApp lets us upload. `upload_media` and `send_audio` raise `NotSupported` (a `SendError`), and
  `InstagramClient.voice_replies` is `False` so the wiring can skip the speech call and its cost. The text
  `voice_not_here` says so to the person once. To add it later: produce m4a, host it on a public https link for the
  time of the send, and send `attachment: {"type": "audio", "payload": {"url": ...}}`.
- **Messages outside the 24-hour window.** A notice ("your transfer arrived") can only go out within 24 hours of the
  person's last Instagram message. There are no templates as on WhatsApp, and the human agent tag is for people, not
  for an agent. So the channel never writes first here: notices ("your transfer arrived") wait, as on WhatsApp, and
  go out as the first lines of the next turn in whichever chat the person writes from.
- **Writing first.** Nobody can be messaged before they wrote to the account.
- **Desktop and web.** Quick replies and templates are phone features. On the web a person may see the text without
  the buttons.
- **Shared posts, reels, stories, videos, files, stickers as stickers**: they arrive as kind `"other"`, `"video"` or
  `"document"` and get the channel's "I cannot open this" reply.
- **Group chats** are not part of the API.

## Linking an Instagram chat to an account

The account's root identity stays the WhatsApp number. An Instagram id proves nothing about a phone number, so the
link is always started from the WhatsApp side, where the number is known.

1. A person writes to the Instagram account. `LinkCodes.linked(sender)` is `None`, so the channel answers with
   `not_linked`: how to link, with a `wa.me` link that opens the WhatsApp chat with «اربط انستغرام» already typed.
2. In WhatsApp (signed in by number), that message makes the channel call `LinkCodes.issue(user_id)` and send
   `link_code`: six digits, valid 10 minutes, at most 3 codes an hour per account. A new code replaces the old one.
3. The person types the six digits in the Instagram chat. `LinkCodes.redeem(sender, text)` returns the user id and
   stores the link. The channel answers `link_ok`, and sends `linked_notice` to the WhatsApp chat, with how to cut it
   («افصل انستغرام»).
4. From then on every Instagram message of that sender is handled as that account.

Safety:

- The table holds only an HMAC-SHA256 of each code, keyed with a secret and salted per code. No code is logged.
- A code works once. Two redeems of one code at the same moment link once.
- 5 wrong codes from one sender inside an hour lock that sender for an hour. While locked, even the right code is
  refused, and more tries do not extend the lock.
- 100 wrong codes an hour from all senders together (`global_wrong_per_hour`, `INSTAGRAM_LINK_FLOOD_PER_HOUR`)
  turn on a flood brake until the hour has passed: a sender that typed a wrong code this hour may not try again,
  so many accounts guessing a little each are stopped after their first guess. A sender with no wrong code of its
  own still links: the brake never shuts out the person holding a live code, so twenty throwaway accounts cannot
  switch linking off for everyone. The admin (`MANAGER_WHATSAPP`) gets one line when it comes on (a count and the
  minutes, never a code or a sender), and `/healthz` shows `instagram_linking_paused`.
- Two accounts never hold the same live code.
- One Instagram account per wallet. The newest link wins, and `unlink(user_id)` cuts it and voids a pending code.
- A link is not a second factor. It only says which account the chat is about. Every payment still needs the tap on
  the card and the PIN, as on WhatsApp.

## Files

| File | What |
|---|---|
| `graph.py` | `challenge`, `signature_ok`, `sign`, `parse(body, own_id) -> list[Inbound]` |
| `client.py` | `InstagramClient` (the WhatsApp `Graph` protocol), the payload builders, `split_text`, `is_cdn`, `ApiError`, `NotSupported` |
| `link.py` | `LinkCodes`, `looks_like_code`, `TooManyCodes` |
| `texts.py` | the channel's texts in Iraqi, MSA and English, `wa_link`, `wants_link`, `wants_unlink` |
| `config.py` | `Settings.from_env()`, `ready`, `missing`, `link_secret` |
| `wiring.py` | `Instagram` (settings, client, link table in the accounts database), `scoped` / `unscoped` / `is_instagram` |
| `tests/` | offline tests for all of it |

## How it is wired

The Instagram door is part of the WhatsApp channel's app (`uvicorn channels.whatsapp.app:create_app --factory`).
`create_app` reads `INSTAGRAM_*`; when `Settings.ready` is true it builds the `InstagramClient` and an `Instagram`
(`wiring.py`) and mounts:

| Route | What |
|---|---|
| `GET /instagram/webhook` | `graph.challenge(...)` with `INSTAGRAM_VERIFY_TOKEN`, else 403 |
| `POST /instagram/webhook` | `graph.signature_ok(raw, X-Hub-Signature-256, INSTAGRAM_APP_SECRET)`, else 403 (never unsigned, whatever `WHATSAPP_ALLOW_UNSIGNED` says); then 200 at once |

With a setting missing, neither route exists (404), and one log line at start names what is missing (names, never
values). A test app built with a fake WhatsApp graph never reads `INSTAGRAM_*` from the environment: tests pass
`instagram=Settings(...)` and `instagram_client=<fake>` to `create_app`.

What a POST does:

1. `Instagram.inbound(body)`: `graph.parse(body, INSTAGRAM_ACCOUNT_ID)`, `client.remember(items)`, and each sender
   becomes «ig:<Instagram-scoped id>» (`wiring.scoped`) with `has_phone=False`.
2. `Channel.accept(items, door="instagram")`, as for WhatsApp: messages older than 15 minutes are dropped, each
   message id is taken once (the channel's store), and the dispatcher runs one sender at a time. The door is the
   route's word, never the sender's text: the WhatsApp webhook passes `door="whatsapp"`, and an item whose sender
   reads «ig:…» arriving there is dropped (so `WHATSAPP_ALLOW_UNSIGNED` never opens an Instagram chat). The
   WhatsApp allow-list (`ALLOWED_NUMBERS`) is applied to the linked account's number, since an Instagram id is no
   number. With an allow-list, a sender that is not linked gets silence, as a stranger on WhatsApp does, except
   for a message that is a link code (a code is issued only to an allowed number). A voice note is downloaded
   only for a linked sender.
3. The turn (`Channel.handle_batch`):
   - **Not linked.** The first six-digit code in the batch goes to `LinkCodes.redeem`. Linked: `link_ok` here and
     `linked_notice` in the account's WhatsApp chat (its window is open: the code was asked for there minutes ago),
     and what came after the code is the account's first turn. A wrong code: `wrong_code` with `tries_left`, or
     `locked` with `locked_until`. Anything else: `not_linked`, with the `wa.me` link that opens the WhatsApp chat
     with «اربط انستغرام» typed. Nothing in the id is ever read as a phone number.
   - **Linked.** The account by `LinkCodes.linked`. A closed account gets its one line; an account that must set a
     PIN (invited, or after an admin reset) gets `whatsapp_only`. Otherwise it is an ordinary turn of that account.

In the WhatsApp chat, «اربط انستغرام» / "link instagram" (`texts.wants_link`) issues a code (`link_code`, or
`too_many_codes`), and «افصل انستغرام» / "unlink instagram" (`texts.wants_unlink`) cuts the link (`unlinked`, or
`not_linked_here`). The unlink words work in the Instagram chat too; the link words there get `already_linked`.

### One account, two chats

- The channel keeps one open conversation per sender key (`Channel._runtime`). The WhatsApp chat and the Instagram
  chat of one account have different keys, so each has its own orchestrator session: its own card, its own open
  question, its own PIN step after the tap. A PIN typed in one chat never confirms the other chat's card: it gets
  «no card is open» there. Both sessions run over the same wallet and accounts database, so balance, contacts, bill
  accounts, holds, the daily limit and the PIN itself are the same.
- Two turns of one account never run at the same time, whichever chat they came from (a lock per account around the
  turn), on top of one sender at a time. An Instagram turn reads its link again once it holds that lock: a link cut
  from WhatsApp while it waited is cut for it too (it gets `not_linked`, and no PIN it carried is read). An unlink,
  or a newer link of the same wallet, closes the old Instagram conversation with its card and its PIN step.
- Replies go out through `Channel._route(sender)`: an «ig:» sender is answered by the Instagram client at its own id,
  everyone else by WhatsApp. Cards keep their three buttons (pay, change, cancel) as a button template, with
  «شلون تأكدت؟» as a one-button message under it, as on WhatsApp; lists become quick replies.
- **Confirmation and the PIN are the WhatsApp flow, unchanged.** Money moves only after the tap on the card's
  «إي، ادفع» (a postback carrying `confirm:<quote id>`) and the PIN typed as a message in the same chat. The link is
  not a second factor and never stands in for either. Like WhatsApp's, an Instagram message is not end-to-end
  encrypted to the business: the inbox copy of a PIN-shaped message is kept without its text, and no PIN is logged.
- No voice reply: `client.voice_replies` is `False`, so no speech is bought; a voice note gets the text answer, and
  `voice_not_here` once per conversation. Voice notes in are heard as on WhatsApp (downloaded from Meta's CDN only).
- Sign-up, changing the PIN, asking for a PIN reset (typed, or a tapped `m:forgot:*`) and the identity document stay
  on WhatsApp (`whatsapp_only`), where the number is the account: the PIN is the only payment factor, and a door
  opened with a link code must not replace it.
- Not wired: the 24-hour window is not tracked for Instagram (the channel only ever answers someone who just wrote),
  and the admin page does not show or cut a link yet (`LinkCodes.sender_of` / `unlink` are there for it).

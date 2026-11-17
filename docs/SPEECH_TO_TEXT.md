# Which model hears Iraqi voice notes? (15 Nov 2026)

Voice notes are how many people in Iraq write. A payment agent that mishears one digit of an account number,
or the name of the person being paid, is worse than one that asks again. So this test scores the details that
matter, not only how close the text is.

Open-weights models are named. The two closed models are labelled A and B: A is the speech model the production
ear uses today, B is a second closed transcription model.

## Result

**16 bill-pay voice notes** (`data/voice/billpay/`): names, amounts, account and phone numbers, billers,
yes / cancel, one noisy note, one fast note, one changed mind, one mixed with English.

| Model | Open weights | Details right | Notes with every detail right | Text similarity (no numbers) | Seconds (median) | Cost, 16 notes |
|---|---|---|---|---|---|---|
| **Cohere Transcribe Arabic** (2B, 07-2026) | yes, Apache 2.0 | **30/30** | **16/16** | **0.940** | **0.74** | free trial |
| Closed model A (production ear now) | no | **30/30** | **16/16** | 0.910 | 3.04 | $0.006 |
| Voxtral Mini Transcribe | yes, Apache 2.0 | 29/30 | 15/16 | 0.910 | 0.75 | $0.003 |
| Whisper large-v3-turbo | yes, MIT | 28/30 | 14/16 | 0.915 | 1.08 | $0.0006 |
| Whisper large-v3 | yes, MIT | 26/30 | 12/16 | 0.846 | 1.25 | $0.0008 |
| Closed model B | no | 26/30 | 13/16 | 0.844 | 1.30 | $0.002 |

**8 shop voice notes** (`data/voice/`, an earlier test of the same voice path: product names, sizes, a phone
number, a Basra address, one noisy and one fast note):

| Model | Details right | Notes with every detail right | Text similarity (no numbers) | Seconds (median) |
|---|---|---|---|---|
| Closed model A (production ear now) | **26/26** | **8/8** | **0.931** | 3.19 |
| Voxtral Mini Transcribe | 25/26 | 7/8 | 0.922 | 1.06 |
| Whisper large-v3-turbo | 25/26 | 7/8 | 0.898 | 1.47 |
| Cohere Transcribe Arabic | 24/26 | 6/8 | 0.930 | 0.88 |
| Whisper large-v3 | 24/26 | 6/8 | 0.901 | 1.72 |
| Whisper large-v3-turbo on a laptop CPU (int8) | 24/26 | 6/8 | 0.892 | 30.19 |
| Closed model B | 23/26 | 5/8 | 0.896 | 1.40 |

## What the misses were

- **Whisper large-v3 added a digit to a phone number**, in both of its runs on the shop set:
  `0 7 7 0 1 2 3 4  4 5 6 7` for 07701234567. On the bill-pay set it broke a subscriber number into
  `371-625-7000 5 9 2 0 4`. For a payment agent this is the worst kind of error, because the result still looks
  like a number.
- **Whisper (both sizes) and Voxtral heard «الماي» (water) as «الميش»**. Whisper large-v3 heard «عشرة آلاف» as
  «أشرتلاف», and Whisper turbo split «أم علي» into «الأمعالية».
- **Closed model B** wrote one note in Latin letters («Shket baqi»), lost «زينب كاظم» in the noisy note, and
  wrote «ثبتها» as «تب».
- **Cohere** split the brand «توشيبا» into «توشي بستة» (every model except A did the same), and on the noisy shop
  note wrote «55نج» for «55 انج», which loses the word for inch.
- **Closed model A** missed nothing that is checked. It wrote «شو قد» for «شكد», which keeps the meaning and is
  counted as right.

## How it is scored

`python -m eval.compare_stt` (see its docstring). Every model is one route of the model layer's `stt` role
(`docs/MODELS.md`), so any provider can be tried without code: Cohere's API, any host that serves the
`/audio/transcriptions` endpoint, or faster-whisper on the machine itself (`local:large-v3-turbo`).

Each note has the text that was said and a list of checks, each a pattern on the folded text (hamza, ta marbuta,
diacritics and ج/چ folded, digits made ASCII): a name, a brand or biller, an amount (as digits or words), and every
digit of an account or phone number, in order. A note passes a check or it does not. Text similarity is a
character ratio after folding. It is also reported with all number words and digits removed, so "34" and
«اربعة وثلاثين» are judged only by the checks.

Some checks were too strict on the first pass. They were widened **for every model at once**, then the saved
transcripts were scored again with `--rescore` (no new calls):
- a number glued to a letter («ب5000»);
- «المولد» without the final ة;
- «ثلاثمائة» spelled the formal way;
- «شكد» in any spelling (شكت، شو قد، شقد).

Wrong digits, wrong names and lost words stay wrong.

Raw results, every transcript and every check are in `eval/reports/2026-11-15/stt_billpay.json` and
`stt_shop.json`. `tests/test_compare_stt.py` checks, offline, that scoring those files again gives the tables above.

## Limits, said plainly

- **The voices are synthetic.** Every note was spoken by a text-to-speech model (`eval/make_voice_notes.py`, the
  `tts` role, several voices and styles), then encoded like a WhatsApp voice note (Opus, 16 kHz mono). Real
  people, real phones and real streets are harder. The voices came from the same vendor as closed model A, which
  may favour it. The next step is a set of real voice notes recorded by people, with their consent.
- **24 notes is small.** One note is 3 to 6 points in the tables above. The tables show which models are in the
  same league and which make dangerous mistakes. They are not a ranking to two decimals.
- **The seconds were measured from Baghdad over the internet**, one call at a time, except the laptop row.
- Three bill-pay notes were reworded and heard again by every model, because their first wording shared
  four-word runs with held-out test cases that must stay unseen. The counts above did not change.
- Qwen3 ASR Flash was planned, but the host used answered 404 on every call (it is no longer served there).

## What this means for the agent

- **Keep closed model A as the ear in production for now.** It is the only model with every detail right on both
  sets. A chat model that takes audio (the `hear` role in `docs/MODELS.md`) can also be sent the user's saved
  payee names along with the note, which none of the transcription endpoints here can take.
- **Cohere Transcribe Arabic is the open-weights choice.** It matched A on the bill-pay set and was four times
  faster. It is small (2B parameters), so it fits on a small GPU next to the other open models. Its free API is a
  trial with rate limits; production means hosting the weights (Apache 2.0) or a paid plan.
- **Whisper is not used.** It is open and cheap, but it changed the digits of numbers, which a payment agent
  cannot accept. On a laptop CPU it also takes 23 to 35 seconds per note, which is too slow without a GPU.
- **Whatever hears the note, the agent still reads the amount and the payee back on a confirmation card**, and
  nothing moves until the user taps. A mishearing becomes a wrong card the user can cancel, not a wrong payment.

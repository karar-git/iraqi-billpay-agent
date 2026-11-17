"""Images and audio through the model client: the parts on the wire, transcription, speech.

Every request goes to an httpx.MockTransport. The "audio" and "image" bytes are made-up test bytes.
"""

import base64

import httpx
import pytest

from llm import LLM, LLMError, Provider, Registry, Route
from llm.client import audio_format
from llm.testing import Endpoint, FakeClock, chat

IMAGE = b"\x89PNG-specimen-not-a-real-image"
AUDIO = b"OggS-specimen-not-real-audio"
READING = {
    "type": "object",
    "properties": {"name": {"type": ["string", "null"]}, "amount": {"type": ["integer", "null"]}},
    "required": ["name", "amount"],
    "additionalProperties": False,
}


def build(answers, roles, *, provs=None, env=None):
    def answer(body, request):
        return answers[request.url.host.split(".")[0]](body, request)

    endpoint = Endpoint(answer)
    clock = FakeClock()
    providers = provs or {
        "eyes": Provider("eyes", "https://eyes.test/v1", ("EYES_KEY",)),
        "spare": Provider("spare", "http://spare.test/v1", json_mode="prompt", local=True),
        "upload": Provider("upload", "http://upload.test/v1", local=True, options={"transcribe_body": "multipart"}),
    }
    llm = LLM(Registry(providers, roles), transport=endpoint.transport, clock=clock, sleep=clock.sleep,
              env={"EYES_KEY": "ek-test-not-real"} if env is None else env)
    return llm, endpoint


# ---- images ---------------------------------------------------------------------------------------

def test_vision_json_sends_the_image_as_a_data_url_part():
    llm, endpoint = build({"eyes": lambda b, r: {"name": "SPECIMEN", "amount": 25000}},
                          {"vision": [Route("eyes", "seer")]})
    out = llm.vision_json("vision", IMAGE, "image/png", "Read the bill.", READING)
    assert out == {"name": "SPECIMEN", "amount": 25000}
    body = endpoint.bodies[0]
    assert body["model"] == "seer" and body["temperature"] == 0.0
    [message] = body["messages"]
    assert message["role"] == "user"
    assert message["content"][0] == {"type": "text", "text": "Read the bill."}
    assert message["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + base64.b64encode(IMAGE).decode()},
    }
    assert body["response_format"]["json_schema"]["schema"] == READING


def test_vision_json_checks_the_answer_and_falls_back():
    answers = {"eyes": lambda b, r: {"name": "SPECIMEN", "amount": "25,000"},      # amount must be an integer
               "spare": lambda b, r: {"name": "SPECIMEN", "amount": None}}
    llm, endpoint = build(answers, {"vision": [Route("eyes", "seer"), Route("spare", "seer-small")]})
    assert llm.vision_json("vision", IMAGE, "image/jpeg", "Read the card.", READING) == {"name": "SPECIMEN",
                                                                                         "amount": None}
    assert endpoint.hosts == ["eyes.test", "spare.test"]
    # the backup takes the schema in the prompt: a system message is added in front of the image message
    sent = endpoint.bodies[1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user"]
    assert sent[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "response_format" not in endpoint.bodies[1]


def test_vision_json_gives_none_when_no_route_reads_it():
    llm, _ = build({"eyes": lambda b, r: 500}, {"vision": [Route("eyes", "seer")]})
    assert llm.vision_json("vision", IMAGE, "image/png", "Read.", READING, timeout_s=5) is None
    assert llm.vision_json("hear", IMAGE, "image/png", "Read.", READING) is None


# ---- audio into a chat model --------------------------------------------------------------------------

def test_hear_json_adds_the_audio_to_the_last_user_message():
    llm, endpoint = build({"eyes": lambda b, r: {"name": "هادي", "amount": 50000}}, {"hear": [Route("eyes", "listener")]})
    messages = [{"role": "system", "content": "Listen."}, {"role": "user", "content": "What was asked?"}]
    assert llm.hear_json("hear", AUDIO, "audio/ogg; codecs=opus", messages, READING) == {"name": "هادي", "amount": 50000}
    sent = endpoint.bodies[0]["messages"]
    assert sent[0] == {"role": "system", "content": "Listen."}
    assert sent[1]["content"] == [
        {"type": "text", "text": "What was asked?"},
        {"type": "input_audio", "input_audio": {"data": base64.b64encode(AUDIO).decode(), "format": "ogg"}},
    ]
    assert messages[1]["content"] == "What was asked?"            # the caller's list is untouched


def test_hear_json_adds_a_user_message_when_there_is_none():
    llm, endpoint = build({"eyes": lambda b, r: {"name": None, "amount": None}}, {"hear": [Route("eyes", "listener")]})
    llm.hear_json("hear", AUDIO, "mp3", [{"role": "system", "content": "Listen."}], READING)
    sent = endpoint.bodies[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user"]
    assert sent[1]["content"] == [
        {"type": "input_audio", "input_audio": {"data": base64.b64encode(AUDIO).decode(), "format": "mp3"}}]


def test_hear_json_keeps_parts_the_caller_already_built():
    llm, endpoint = build({"eyes": lambda b, r: {"name": None, "amount": None}}, {"hear": [Route("eyes", "listener")]})
    parts = [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}]
    llm.hear_json("hear", AUDIO, "wav", [{"role": "user", "content": parts}], READING)
    content = endpoint.bodies[0]["messages"][0]["content"]
    assert content[:2] == parts and content[2]["type"] == "input_audio" and len(parts) == 2


@pytest.mark.parametrize("given,short", [
    ("ogg", "ogg"), ("audio/ogg", "ogg"), ("audio/ogg; codecs=opus", "ogg"), ("audio/mpeg", "mp3"), ("MP3", "mp3"),
    ("audio/x-wav", "wav"), ("audio/mp4", "m4a"), (".wav", "wav"), ("audio/webm", "webm"), ("", "ogg"),
])
def test_audio_format(given, short):
    assert audio_format(given) == short


# ---- transcription ------------------------------------------------------------------------------------

def test_transcribe_sends_a_json_body_by_default():
    llm, endpoint = build({"eyes": lambda b, r: httpx.Response(200, json={"text": "  ادفع المولدة \n"})},
                          {"stt": [Route("eyes", "scribe")]})
    assert llm.transcribe("stt", AUDIO, "audio/ogg", language="ar") == "ادفع المولدة"
    request = endpoint.requests[0]
    assert request.url.path == "/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer ek-test-not-real"
    assert endpoint.bodies[0] == {
        "model": "scribe",
        "input_audio": {"data": base64.b64encode(AUDIO).decode(), "format": "ogg"},
        "language": "ar",
    }


def test_transcribe_leaves_the_language_out_when_none_is_given_and_merges_extra_body():
    chain = [Route("eyes", "scribe", {"extra_body": {"temperature": 0}})]
    llm, endpoint = build({"eyes": lambda b, r: "hello"}, {"stt": chain})
    assert llm.transcribe("stt", AUDIO, "wav") == "hello"
    assert "language" not in endpoint.bodies[0] and endpoint.bodies[0]["temperature"] == 0


def test_transcribe_uploads_a_file_for_a_provider_that_wants_multipart():
    llm, endpoint = build({"upload": lambda b, r: httpx.Response(200, json={"text": "دز لهادي خمسين"})},
                          {"stt": [Route("upload", "whisper-large-v3", {"extra_body": {"temperature": 0}})]})
    assert llm.transcribe("stt", AUDIO, "audio/ogg", language="ar") == "دز لهادي خمسين"
    request = endpoint.requests[0]
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    raw = request.content
    assert AUDIO in raw
    assert b'name="file"; filename="audio.ogg"' in raw and b"audio/ogg" in raw
    assert b'name="model"\r\n\r\nwhisper-large-v3' in raw
    assert b'name="language"\r\n\r\nar' in raw
    assert b'name="temperature"\r\n\r\n0' in raw
    assert "authorization" not in request.headers


def test_the_route_can_ask_for_multipart_too():
    llm, endpoint = build({"eyes": lambda b, r: "ok"}, {"stt": [Route("eyes", "scribe", {"transcribe_body": "multipart"})]})
    assert llm.transcribe("stt", AUDIO, "mp3") == "ok"
    assert endpoint.requests[0].headers["content-type"].startswith("multipart/form-data")
    assert b'filename="audio.mp3"' in endpoint.requests[0].content


def test_transcribe_returns_an_empty_text_when_nothing_was_heard():
    llm, _ = build({"eyes": lambda b, r: httpx.Response(200, json={"text": "   "})}, {"stt": [Route("eyes", "scribe")]})
    assert llm.transcribe("stt", AUDIO, "ogg") == ""


def test_transcribe_reads_a_plain_text_reply():
    plain = httpx.Response(200, text=" مرحبا \n", headers={"content-type": "text/plain; charset=utf-8"})
    llm, _ = build({"eyes": lambda b, r: plain}, {"stt": [Route("eyes", "scribe")]})
    assert llm.transcribe("stt", AUDIO, "ogg") == "مرحبا"


def test_transcribe_falls_back_and_counts_usage():
    chain = [Route("eyes", "scribe"), Route("upload", "whisper-large-v3")]
    answers = {"eyes": lambda b, r: httpx.Response(200, json={"no_text": True}),
               "upload": lambda b, r: httpx.Response(200, json={"text": "ok", "usage": {"cost": 0.0004}})}
    llm, endpoint = build(answers, {"stt": chain})
    assert llm.transcribe("stt", AUDIO, "ogg") == "ok"
    assert endpoint.hosts == ["eyes.test", "upload.test"]
    rows = llm.stats()["stt"]
    assert (rows[0]["failures"], rows[0]["fallbacks"]) == (1, 1)
    assert rows[1]["usd"] == pytest.approx(0.0004) and llm.spent_usd == pytest.approx(0.0004)


def test_transcribe_raises_when_no_route_answers():
    llm, _ = build({"eyes": lambda b, r: 429}, {"stt": [Route("eyes", "scribe")], "hear": []})
    with pytest.raises(LLMError) as caught:
        llm.transcribe("stt", AUDIO, "ogg")
    assert str(caught.value) == "stt: no route answered (eyes key 1 status 429)"
    with pytest.raises(LLMError) as caught:
        llm.transcribe("stt", AUDIO, "ogg")                       # the only key is resting now
    assert str(caught.value) == "stt: no route answered (eyes every key resting)"
    with pytest.raises(LLMError) as caught:
        llm.transcribe("hear", AUDIO, "ogg")
    assert str(caught.value) == "hear: no route answered (no route configured)"


def test_transcribe_without_a_key_says_so():
    llm, _ = build({}, {"stt": [Route("eyes", "scribe"), Route("ghost", "x")]}, env={})
    with pytest.raises(LLMError) as caught:
        llm.transcribe("stt", AUDIO, "ogg")
    assert str(caught.value) == "stt: no route answered (eyes no key; ghost unknown provider)"


# ---- speech ---------------------------------------------------------------------------------------------

def test_speech_returns_the_raw_audio():
    spoken = b"OggS-specimen-spoken-audio"
    llm, endpoint = build({"eyes": lambda b, r: spoken}, {"tts": [Route("eyes", "speaker")]})
    assert llm.speech("tts", "تم التحويل", voice="noor") == spoken
    assert endpoint.requests[0].url.path == "/v1/audio/speech"
    assert endpoint.bodies[0] == {"model": "speaker", "input": "تم التحويل", "voice": "noor"}


def test_speech_sends_the_style_and_merges_extra_body():
    options = {"extra_body": {"response_format": "opus", "speed": 1.1, "voice": "override"}}
    llm, endpoint = build({"eyes": lambda b, r: b"audio"}, {"tts": [Route("eyes", "speaker", options)]})
    llm.speech("tts", "hello", voice="noor", style="Warm, calm, Baghdadi accent.")
    assert endpoint.bodies[0] == {"model": "speaker", "input": "hello", "voice": "override",
                                  "instructions": "Warm, calm, Baghdadi accent.", "response_format": "opus",
                                  "speed": 1.1}


def test_speech_falls_back_when_the_reply_is_not_audio():
    not_audio = httpx.Response(200, json={"error": "voice not found"})
    answers = {"eyes": lambda b, r: not_audio, "spare": lambda b, r: b"backup-audio"}
    llm, endpoint = build(answers, {"tts": [Route("eyes", "speaker"), Route("spare", "speaker-small")]})
    assert llm.speech("tts", "hello", voice="noor") == b"backup-audio"
    assert endpoint.hosts == ["eyes.test", "spare.test"]


def test_speech_gives_none_when_no_route_speaks():
    empty = httpx.Response(200, content=b"", headers={"content-type": "audio/ogg"})
    llm, _ = build({"eyes": lambda b, r: empty}, {"tts": [Route("eyes", "speaker")]})
    assert llm.speech("tts", "hello", voice="noor") is None
    assert llm.speech("vision", "hello", voice="noor") is None


def test_speech_cost_is_recorded_by_the_caller():
    llm, _ = build({"eyes": lambda b, r: b"audio"}, {"tts": [Route("eyes", "speaker")]})
    llm.speech("tts", "hello", voice="noor")
    assert llm.spent_usd == 0.0
    llm.add_cost(0.003)                 # for example seconds of audio times the price per second
    assert llm.spent_usd == pytest.approx(0.003)


def test_chat_helper_builds_a_usable_reply():
    reply = chat({"a": 1}, tokens_in=2, tokens_out=3, cost=0.1)
    data = reply.json()
    assert data["choices"][0]["message"]["content"] == '{"a": 1}'
    assert data["usage"] == {"prompt_tokens": 2, "completion_tokens": 3, "cost": 0.1}

"""Voice replies made for the ear like the store bot's: amounts in words, the end of a number digit by digit,
no digit left for the voice to read in Fusha or one by one."""

from __future__ import annotations

from agent import speech
from agent.session import Reply


def test_a_voice_reply_says_amounts_and_number_endings_in_words():

    reply = Reply(["تم. دزيت 25,000 دينار لـ علي ح.، الرقم ينتهي بـ 4567. رصيدك هسه 474,750 دينار."], lang="iq")
    spoken = speech.spoken_text(reply)
    assert not any(ch.isdigit() for ch in spoken), spoken
    assert "اربعة خمسة ستة سبعة" in spoken and "خمسة وعشرين ألف دينار" in spoken

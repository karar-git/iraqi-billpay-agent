"""After «I couldn't find X», a question, a greeting or a long sentence is a new message, never a name to look up."""
from agent.session import _not_a_name_answer


def test_small_talk_and_questions_are_not_names():
    for text in ("تعرف شي عن الطقس اليوم؟", "مساء الخير", "اكتبلي شعر عن بغداد الحلوة هسه", "شكرا", "how are you?"):
        assert _not_a_name_answer(text), text


def test_short_names_and_numbers_still_answer_who():
    for text in ("حمودي", "أحمد علي", "ليث", "07701234567", "لزينب", "Ahmed"):
        assert not _not_a_name_answer(text), text

"""Small Arabic text helpers shared by the payment layer."""

import re

# Harakat, Quranic marks and tatweel: they never change the meaning we care about.
DIACRITICS = "ؐ-ًؚ-ٰٟۖ-ۭ"
_STRIP = re.compile(f"[{DIACRITICS}ـ]")

_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه", "ھ": "ه",
    "ى": "ي", "ی": "ي", "ئ": "ي",
    "ؤ": "و",
    "گ": "ك", "ک": "ك",
    "چ": "ج",
    "پ": "ب",
    "ڤ": "ف",
    # Eastern Arabic and Persian digits -> Western digits
    **{chr(0x0660 + d): str(d) for d in range(10)},
    **{chr(0x06F0 + d): str(d) for d in range(10)},
})


def fold(text: str) -> str:
    """Strip diacritics/tatweel, unify letter variants and digits, lowercase Latin."""
    return _STRIP.sub("", text).translate(_FOLD).lower()

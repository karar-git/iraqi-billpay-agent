"""Draw the specimen identity images of data/kyc/ and write data/kyc/specimens.json.

Run from the repo root:  python data/kyc/generate_ids.py

Needs Pillow, which only this script uses: nothing the agent runs and no default test imports it.
Deterministic: a fixed seed picks the names, numbers and dates, so every run plans the same documents.

Everything here is made up. The names are random picks from the lists below, the numbers are random digits
in the right format behind a block of zeros, and the layout is our own: it copies no real card, no emblem
and no security feature. Every document image carries a large SPECIMEN / نموذج mark.

What is written:
- 10 specimen documents: 7 national-card-like, 2 passport-like, 1 residence-card-like;
- 5 negatives: a shop receipt, a bare wall, an expired card, an under-age card, a card on a phone screen;
- specimens.json: for every image the fields a perfect reader would copy (`truth`, in the shape of
  kyc.schema.SCHEMA), the name the person typed, and the verdict kyc.check.check must give (`expected`).
  The expected verdicts were written here by hand, before the check ran on them; a test compares.

Arabic is drawn without a shaping engine: `shape()` picks the joined form of every letter from the Unicode
presentation forms and `visual()` puts the line in drawing order (right to left).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC_FILE = HERE / "specimens.json"
SEED = 20261113
REFERENCE_DATE = "2026-11-13"  # the "today" the expected verdicts are written for
MAX_FILE_BYTES = 200 * 1024

# ---- made-up people: every name is a random pick of one word from each list ----

FIRST_F = [("زينب", "Zainab"), ("مريم", "Maryam"), ("نور", "Noor"), ("هدى", "Huda"), ("رنا", "Rana"), ("سارة", "Sara")]
FIRST_M = [("حيدر", "Haider"), ("مصطفى", "Mustafa"), ("ياسر", "Yasir"), ("سامر", "Samer"), ("وسام", "Wisam"),
           ("عبد الله", "Abdullah"), ("كريم", "Kareem")]
FATHERS = [("هادي", "Hadi"), ("جاسم", "Jasim"), ("كاظم", "Kadhim"), ("صالح", "Saleh"), ("ناصر", "Nasir"),
           ("جواد", "Jawad"), ("ماجد", "Majid"), ("نبيل", "Nabeel"), ("خالد", "Khalid"), ("رحيم", "Raheem")]
FAMILIES = [("الرافديني", "Al-Rafidaini"), ("الفراتي", "Al-Furati"), ("النخيلي", "Al-Nakhili"),
            ("البستاني", "Al-Bustani"), ("الوراق", "Al-Warraq"), ("الصفار", "Al-Saffar")]

# (file, kind, sex, what the person typed, expected status, expected reason, special)
PLAN = [
    ("national_01.png", "national_card", "f", "ar_first_father", "verified", "ok", ""),
    ("national_02.png", "national_card", "m", "latin_first_father", "verified", "ok", ""),
    ("national_03.png", "national_card", "m", "ar_first_family", "verified", "ok", ""),
    ("national_04.jpg", "national_card", "f", "ar_full", "verified", "ok", ""),
    ("national_05.png", "national_card", "m", "other_person", "review", "name_mismatch", ""),
    ("national_06.png", "national_card", "f", "first_only", "review", "name_mismatch", ""),
    ("national_07.jpg", "national_card", "m", "ar_first_grandfather", "verified", "ok", ""),
    ("passport_01.png", "passport", "f", "latin_first_family", "verified", "ok", ""),
    ("passport_02.jpg", "passport", "m", "ar_first_father", "verified", "ok", ""),
    ("residence_01.png", "residence_card", "m", "latin_first_father", "verified", "ok", ""),
    ("negative_expired_card.png", "national_card", "m", "ar_first_father", "refused", "expired", "expired"),
    ("negative_underage_card.png", "national_card", "f", "ar_first_father", "refused", "under_age", "underage"),
    ("negative_screen_photo.jpg", "national_card", "m", "ar_first_father", "review", "screen_or_copy", "screen"),
    ("negative_receipt.png", "receipt", "", "ar_first_father", "retry", "not_a_document", ""),
    ("negative_wall.jpg", "wall", "", "ar_first_father", "retry", "not_a_document", ""),
]
DOCUMENT_KINDS = ("national_card", "passport", "residence_card")


def _number(kind: str, rng: random.Random) -> str:
    """A number in the format of its type, behind zeros that mark it as a specimen."""
    if kind == "national_card":
        return "0000" + "".join(rng.choice("0123456789") for _ in range(8))        # 12 digits
    if kind == "passport":
        return "Z00" + "".join(rng.choice("0123456789") for _ in range(6))         # a letter and 8 digits
    return "RC00" + "".join(rng.choice("0123456789") for _ in range(6))            # 2 letters and 8 digits


def _date(rng: random.Random, first_year: int, last_year: int) -> str:
    return f"{rng.randint(first_year, last_year):04d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def _typed(mode: str, person: dict, stranger: dict) -> str:
    ar, la = person["ar"], person["latin"]
    return {
        "ar_first_father": f"{ar[0]} {ar[1]}",
        "ar_first_grandfather": f"{ar[0]} {ar[2]}",
        "ar_first_family": f"{ar[0]} {ar[3]}",
        "ar_full": " ".join(ar),
        "latin_first_father": f"{la[0]} {la[1]}".lower(),
        "latin_first_family": f"{la[0]} {la[3]}",
        "first_only": ar[0],
        "other_person": f"{stranger['ar'][0]} {stranger['ar'][1]}",
    }[mode]


def _person(sex: str, rng: random.Random) -> dict:
    first = rng.choice(FIRST_F if sex == "f" else FIRST_M)
    father, grandfather = rng.sample(FATHERS, 2)
    family = rng.choice(FAMILIES)
    parts = (first, father, grandfather, family)
    return {"ar": [p[0] for p in parts], "latin": [p[1] for p in parts]}


def build_specs() -> list[dict]:
    """The plan as data: one dict per image with what is printed, the truth and the expected verdict.
    No drawing and no Pillow here, so a test can compare this with specimens.json."""
    rng = random.Random(SEED)
    specs = []
    for file, kind, sex, mode, status, reason, special in PLAN:
        person = _person(sex or "m", rng)
        stranger = _person("m", rng)
        while stranger["ar"][0] == person["ar"][0]:
            stranger = _person("m", rng)
        is_document = kind in DOCUMENT_KINDS
        birth = _date(rng, 2011, 2012) if special == "underage" else _date(rng, 1965, 2004)
        expiry = _date(rng, 2023, 2024) if special == "expired" else _date(rng, 2030, 2035)
        both_scripts = kind in ("passport", "residence_card")
        truth = {
            "is_identity_document": is_document,
            "document_type": kind if is_document else "none",
            "readable": is_document,
            "full_name_ar": " ".join(person["ar"]) if is_document else None,
            "full_name_latin": " ".join(person["latin"]).upper() if is_document and both_scripts else None,
            "document_number": _number(kind, rng) if is_document else None,
            "birth_date": birth if is_document else None,
            "expiry_date": expiry if is_document else None,
            "sex": sex if is_document else None,
            "looks_like_photo_of_screen_or_copy": special == "screen",
            "marked_as_specimen": is_document,
            "notes": "",
        }
        specs.append({
            "file": file,
            "mime": "image/jpeg" if file.endswith(".jpg") else "image/png",
            "kind": kind,
            "typed_name": _typed(mode, person, stranger),
            "truth": truth,
            "expected": {"status": status, "reason": reason},
        })
    return specs


# ---- Arabic without a shaping engine ----

_RIGHT_JOINING = "آأؤإاةدذرزوى"  # join to the letter before them only


def _forms() -> dict[str, tuple[str, ...]]:
    """letter -> (isolated, final[, initial, medial]) in the Arabic Presentation Forms-B block, which lists the
    letters in alphabet order from U+FE80."""
    table, code = {}, 0xFE80
    for cp in [*range(0x0621, 0x063B), *range(0x0641, 0x064B)]:
        count = 1 if cp == 0x0621 else 2 if chr(cp) in _RIGHT_JOINING else 4
        table[chr(cp)] = tuple(chr(code + i) for i in range(count))
        code += count
    return table


FORMS = _forms()
LAM_ALEF = {"آ": ("\uFEF5", "\uFEF6"), "أ": ("\uFEF7", "\uFEF8"), "إ": ("\uFEF9", "\uFEFA"), "ا": ("\uFEFB", "\uFEFC")}


def shape(text: str) -> str:
    """Each Arabic letter as the form it takes between its neighbours (isolated, initial, medial, final), with the
    lam-alef ligature. Reading order is kept; other characters pass through."""
    out, i = [], 0
    joins_next = False  # the letter before this one reaches out to it
    while i < len(text):
        char = text[i]
        forms = FORMS.get(char)
        if not forms:
            out.append(char)
            joins_next = False
            i += 1
            continue
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if char == "ل" and nxt in LAM_ALEF:
            out.append(LAM_ALEF[nxt][1 if joins_next else 0])
            joins_next = False
            i += 2
            continue
        next_takes = len(FORMS.get(nxt, ())) >= 2  # every letter but the lone hamza joins the one before it
        if len(forms) == 4:
            out.append(forms[{(False, False): 0, (True, False): 1, (False, True): 2, (True, True): 3}[(joins_next, next_takes)]])
            joins_next = next_takes
        else:
            out.append(forms[1] if joins_next and len(forms) == 2 else forms[0])
            joins_next = False
        i += 1
    return "".join(out)


def visual(text: str) -> str:
    """An Arabic line in the order a left-to-right drawing call needs: shaped, then reversed. For lines of Arabic
    letters and spaces only; digits and Latin text are drawn with their own calls."""
    return shape(text)[::-1]


# ---- drawing (Pillow from here on) ----

FONT_CANDIDATES = [  # (regular, bold): the first pair found that has the Arabic presentation forms is used
    ("C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/tahomabd.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]

THEMES = {
    "national_card": {"band": (27, 94, 122), "paper": (236, 243, 246), "title_en": "SPECIMEN IDENTITY CARD",
                      "title_ar": "بطاقة وطنية نموذجية", "number_en": "Card number", "number_ar": "رقم البطاقة"},
    "passport": {"band": (74, 48, 110), "paper": (241, 238, 247), "title_en": "SPECIMEN PASSPORT",
                 "title_ar": "جواز سفر نموذجي", "number_en": "Passport number", "number_ar": "رقم الجواز"},
    "residence_card": {"band": (150, 84, 22), "paper": (249, 242, 232), "title_en": "SPECIMEN RESIDENCE CARD",
                       "title_ar": "بطاقة إقامة نموذجية", "number_en": "Card number", "number_ar": "رقم البطاقة"},
}
INK, GREY, WHITE = (24, 28, 34), (98, 106, 116), (255, 255, 255)
W, H = 1000, 630


class Pens:
    def __init__(self, regular: str, bold: str):
        from PIL import ImageFont
        self._paths, self._font, self._cache = (regular, bold), ImageFont, {}

    def get(self, size: int, bold: bool = False):
        key = (size, bold)
        if key not in self._cache:
            self._cache[key] = self._font.truetype(self._paths[1 if bold else 0], size)
        return self._cache[key]


def _has_arabic(path: str) -> bool:
    """True when the font draws the joined Arabic forms (a font without them draws the same empty box for all)."""
    from PIL import ImageFont
    try:
        font = ImageFont.truetype(path, 40)
    except OSError:
        return False
    box = bytes(font.getmask("\uFFFE"))
    return all(bytes(font.getmask(ch)) != box for ch in ("\uFEDF", "\uFE8E", "\uFEFB"))


def find_fonts(regular: str | None = None, bold: str | None = None) -> Pens:
    pairs = [(regular, bold or regular)] if regular else FONT_CANDIDATES
    for reg, bld in pairs:
        if Path(reg).is_file() and Path(bld).is_file() and _has_arabic(reg) and _has_arabic(bld):
            return Pens(reg, bld)
    sys.exit("No font with the Arabic presentation forms was found. Pass --font and --font-bold with the paths "
             "of a TrueType font that has them (Tahoma, Arial, DejaVu Sans).")


def _right(draw, x: int, y: int, text: str, font, fill) -> None:
    """Text that ends at x (for Arabic lines, already in visual order)."""
    draw.text((x - draw.textlength(text, font=font), y), text, font=font, fill=fill)


def _field(draw, pens: Pens, y: int, label_en: str, label_ar: str, value: str, arabic: bool = False,
           left: int = 300, right: int = W - 60) -> None:
    draw.text((left, y), label_en, font=pens.get(19), fill=GREY)
    _right(draw, right, y - 2, visual(label_ar), pens.get(21), GREY)
    if arabic:
        _right(draw, right, y + 26, visual(value), pens.get(38, True), INK)
    else:
        size = next((s for s in (34, 32, 30, 28) if draw.textlength(value, font=pens.get(s, True)) <= right - left), 26)
        draw.text((left, y + 28), value, font=pens.get(size, True), fill=INK)
    draw.line((left, y + 78, right, y + 78), fill=(205, 212, 218), width=1)


def _watermark(image, pens: Pens):
    """SPECIMEN / نموذج across the whole image, twice, in see-through red."""
    from PIL import Image, ImageDraw
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for text, font, cy in (("SPECIMEN", pens.get(150, True), 0.36), (visual("نموذج"), pens.get(150, True), 0.72)):
        tile = Image.new("RGBA", (image.width, 220), (0, 0, 0, 0))
        pen = ImageDraw.Draw(tile)
        pen.text(((tile.width - pen.textlength(text, font=font)) / 2, 20), text, font=font, fill=(198, 34, 34, 82))
        tile = tile.rotate(22, expand=True, resample=Image.BICUBIC)
        layer.alpha_composite(tile, ((image.width - tile.width) // 2, int(image.height * cy - tile.height / 2)))
    return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")


def draw_document(spec: dict, pens: Pens):
    from PIL import Image, ImageDraw
    truth, theme = spec["truth"], THEMES[spec["kind"]]
    image = Image.new("RGB", (W, H), (214, 218, 222))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((14, 14, W - 14, H - 14), radius=34, fill=theme["paper"], outline=theme["band"], width=4)
    draw.rounded_rectangle((14, 14, W - 14, 118), radius=34, fill=theme["band"])
    draw.rectangle((14, 80, W - 14, 118), fill=theme["band"])
    draw.text((48, 34), theme["title_en"], font=pens.get(34, True), fill=WHITE)
    _right(draw, W - 48, 26, visual(theme["title_ar"]), pens.get(40, True), WHITE)
    draw.text((48, 82), "Not a real document. For software testing only.", font=pens.get(19), fill=(226, 234, 240))
    _right(draw, W - 48, 80, visual("ليست وثيقة حقيقية، لاختبار البرمجيات فقط"), pens.get(21), (226, 234, 240))

    # a drawn outline where a photo would be: no face of anyone
    draw.rounded_rectangle((52, 160, 262, 430), radius=16, fill=(202, 210, 217), outline=(150, 160, 170), width=2)
    draw.ellipse((112, 200, 202, 290), fill=(150, 160, 170))
    draw.chord((82, 300, 232, 520), 180, 360, fill=(150, 160, 170))
    draw.text((92, 444), "NO PHOTO", font=pens.get(24, True), fill=GREY)

    both = truth["full_name_latin"] is not None
    y = 138
    _field(draw, pens, y, "Full name", "الاسم الكامل", truth["full_name_ar"], arabic=True)
    if both:
        y += 84
        _field(draw, pens, y, "Name in Latin letters", "الاسم بالحروف اللاتينية", truth["full_name_latin"])
    y += 84
    _field(draw, pens, y, theme["number_en"], theme["number_ar"], truth["document_number"])
    y += 84
    half = (300 + W - 60) // 2
    _field(draw, pens, y, "Date of birth", "تاريخ الميلاد", truth["birth_date"], right=half - 20)
    _field(draw, pens, y, "Sex", "الجنس", "F" if truth["sex"] == "f" else "M", left=half + 20)
    _right(draw, W - 60 - 60, y + 26, visual("أنثى" if truth["sex"] == "f" else "ذكر"), pens.get(34, True), INK)
    y += 84
    _field(draw, pens, y, "Date of expiry", "تاريخ الانتهاء", truth["expiry_date"], right=half - 20)
    if not both:
        _field(draw, pens, y, "Issued by", "جهة الإصدار", "DEMO REGISTRY", left=half + 20)
    draw.text((52, H - 62), "SPECIMEN  SPECIMEN  SPECIMEN", font=pens.get(20, True), fill=theme["band"])
    return _watermark(image, pens)


def draw_screen_photo(spec: dict, pens: Pens):
    """The same kind of card shown on a phone: a dark room, the phone's frame, a status bar, screen lines."""
    from PIL import Image, ImageDraw
    card = draw_document(spec, pens)
    image = Image.new("RGB", (W, 760), (26, 24, 28))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 30, W - 70, 730), radius=54, fill=(8, 8, 10), outline=(70, 70, 78), width=5)
    draw.rectangle((104, 78, W - 104, 682), fill=(246, 246, 248))
    draw.text((124, 88), "12:30", font=pens.get(22, True), fill=INK)
    draw.text((W - 250, 88), "4G  78%", font=pens.get(22, True), fill=INK)
    draw.text((124, 126), "<  Photos", font=pens.get(26), fill=(30, 100, 220))
    small = card.resize((760, 479), Image.LANCZOS)
    image.paste(small, (120, 176))
    draw.ellipse((W // 2 - 34, 690, W // 2 + 34, 722), outline=(70, 70, 78), width=3)
    lines = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(lines)
    for row in range(78, 682, 4):  # the rows of a screen, as a camera sees them
        pen.line((104, row, W - 104, row), fill=(0, 0, 0, 30), width=1)
    pen.polygon(((560, 78), (760, 78), (420, 682), (300, 682)), fill=(255, 255, 255, 34))  # glare
    return Image.alpha_composite(image.convert("RGBA"), lines).convert("RGB")


def draw_receipt(pens: Pens):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (640, 900), (120, 92, 66))
    draw = ImageDraw.Draw(image)
    draw.rectangle((90, 40, 550, 860), fill=(252, 251, 247))
    _right(draw, 520, 70, visual("أسواق النخلة"), pens.get(44, True), INK)
    draw.text((120, 132), "AL NAKHLA MARKET (made-up shop)", font=pens.get(20), fill=GREY)
    _right(draw, 520, 170, visual("وصل بيع"), pens.get(30), INK)
    draw.text((120, 176), "Receipt 000417", font=pens.get(24), fill=INK)
    draw.line((120, 222, 520, 222), fill=GREY, width=2)
    items = [("خبز", 1000), ("شاي", 4500), ("رز", 12750), ("سكر", 3250), ("زيت", 6500), ("صابون", 2000)]
    y = 244
    for name, price in items:
        _right(draw, 520, y, visual(name), pens.get(30), INK)
        draw.text((120, y + 4), f"{price:,} IQD", font=pens.get(26), fill=INK)
        y += 62
    draw.line((120, y + 6, 520, y + 6), fill=GREY, width=2)
    _right(draw, 520, y + 26, visual("المجموع"), pens.get(36, True), INK)
    draw.text((120, y + 32), f"{sum(p for _, p in items):,} IQD", font=pens.get(32, True), fill=INK)
    _right(draw, 520, y + 110, visual("شكرا لزيارتكم"), pens.get(28), GREY)
    draw.text((120, y + 170), "2026-10-02  18:41", font=pens.get(22), fill=GREY)
    return image


def draw_wall():
    """A bare painted wall: soft light from one side, a skirting board, nothing to read."""
    from PIL import Image, ImageDraw, ImageFilter
    image = Image.new("RGB", (900, 640))
    draw = ImageDraw.Draw(image)
    for x in range(900):
        shade = 206 + int(34 * x / 900)
        draw.line((x, 0, x, 640), fill=(shade, shade - 8, shade - 24))
    draw.rectangle((0, 560, 900, 640), fill=(168, 150, 128))
    draw.rectangle((0, 552, 900, 562), fill=(236, 230, 220))
    draw.ellipse((-200, -260, 520, 300), fill=(246, 240, 226))
    return image.filter(ImageFilter.GaussianBlur(28))


def _save(image, path: Path) -> None:
    if path.suffix == ".jpg":
        for quality in (84, 76, 68, 60):
            image.save(path, "JPEG", quality=quality, optimize=True)
            if path.stat().st_size <= MAX_FILE_BYTES:
                return
    else:
        image.save(path, "PNG", optimize=True)
        if path.stat().st_size > MAX_FILE_BYTES:
            image.quantize(colors=128).save(path, "PNG", optimize=True)
    if path.stat().st_size > MAX_FILE_BYTES:
        sys.exit(f"{path.name} is over {MAX_FILE_BYTES} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", help="a TrueType font with the Arabic presentation forms")
    parser.add_argument("--font-bold", help="its bold face (default: the same file)")
    args = parser.parse_args()
    pens = find_fonts(args.font, args.font_bold)
    specs = build_specs()
    for spec in specs:
        path = HERE / spec["file"]
        if spec["kind"] == "receipt":
            image = draw_receipt(pens)
        elif spec["kind"] == "wall":
            image = draw_wall()
        elif spec["truth"]["looks_like_photo_of_screen_or_copy"]:
            image = draw_screen_photo(spec, pens)
        else:
            image = draw_document(spec, pens)
        _save(image, path)
        data = path.read_bytes()
        spec["bytes"] = len(data)
        spec["sha256"] = hashlib.sha256(data).hexdigest()
        print(f"{spec['file']:32} {len(data):>7} bytes  {spec['expected']['status']}/{spec['expected']['reason']}")
    out = {
        "about": "Synthetic specimen identity images for tests and demos. Every name, number and date is made up; "
                 "no image shows a real document or a real person. Written by data/kyc/generate_ids.py.",
        "seed": SEED,
        "reference_date": REFERENCE_DATE,
        "allow_specimen": True,
        "specimens": specs,
    }
    SPEC_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {SPEC_FILE.name}: {len(specs)} images")


if __name__ == "__main__":
    main()

"""Build the synthetic wallet seed: data/wallet_seed.json.

Run from the repo root:  python data/generate_wallet.py

Deterministic (random.Random(42) and a fixed snapshot time), so every run writes the same file.
Every name, phone number and account number is made up. Money is integer IQD.

The test cases in data/cases/ point at specific users, contacts and bills in this file.
The PLANNED_* tables create those situations on purpose (two contacts saved as "أحمد", a saved
"ابني" alias, two internet accounts, a bill already paid, a low balance ...). Everything else is
random filler. If you change a plan, re-check the cases: tests/test_data_files.py checks part of it.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED_FILE = Path(__file__).with_name("wallet_seed.json")
SNAPSHOT = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)  # the frozen "now" of the seed
HISTORY_DAYS = 180
PERIODS = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
CURRENT_PERIOD = PERIODS[-1]
TRANSFER_FEE = 250

BILLERS = [
    {"id": "electricity", "kind": "electricity", "name_ar": "كهرباء الوطنية", "name_en": "National Electricity"},
    {"id": "generator", "kind": "generator", "name_ar": "مولدة الحجي جاسم", "name_en": "Hajji Jasim Generator"},
    {"id": "net_rafidain", "kind": "internet", "name_ar": "نت الرافدين", "name_en": "Rafidain Net"},
    {"id": "net_sama", "kind": "internet", "name_ar": "سما نت", "name_en": "Sama Net"},
    {"id": "water", "kind": "water", "name_ar": "ماء المحلة", "name_en": "Neighbourhood Water"},
    {"id": "zain", "kind": "topup", "name_ar": "زين", "name_en": "Zain"},
    {"id": "asiacell", "kind": "topup", "name_ar": "آسياسيل", "name_en": "Asiacell"},
    {"id": "korek", "kind": "topup", "name_ar": "كورك", "name_en": "Korek"},
]

# A phone number's prefix tells the operator, as in Iraq.
OPERATOR_PREFIXES = {
    "zain": ("0780", "0781", "0782", "0790"),
    "asiacell": ("0770", "0771", "0772"),
    "korek": ("0750", "0751"),
}

# (id, name, gender, operator of the user's own line)
USERS = [
    ("u01", "حيدر عبد الأمير", "m", "zain"),
    ("u02", "زهراء كاظم", "f", "asiacell"),
    ("u03", "مصطفى فلاح", "m", "zain"),
    ("u04", "نور جبار", "f", "zain"),
    ("u05", "سجاد رعد", "m", "asiacell"),
    ("u06", "علاء ستار", "m", "korek"),
    ("u07", "مريم عدنان", "f", "zain"),
    ("u08", "ياسر قاسم", "m", "asiacell"),
    ("u09", "رقية حسام", "f", "zain"),
    ("u10", "منتظر صادق", "m", "zain"),
    ("u11", "هدى نزار", "f", "asiacell"),
    ("u12", "مرتضى ضياء", "m", "zain"),
    ("u13", "بنين سلام", "f", "korek"),
    ("u14", "باقر عماد", "m", "asiacell"),
    ("u15", "دعاء فراس", "f", "zain"),
    ("u16", "جعفر هادي", "m", "asiacell"),
    ("u17", "سارة ليث", "f", "zain"),
    ("u18", "زيد أنور", "m", "korek"),
    ("u19", "آية وسام", "f", "asiacell"),
    ("u20", "تبارك مهدي", "f", "zain"),
    ("u21", "كاظم جبار", "m", "asiacell"),
    ("u22", "غدير يوسف", "f", "zain"),
    ("u23", "ليث إبراهيم", "m", "asiacell"),
    ("u24", "حوراء رضا", "f", "zain"),
    ("u25", "عمار نبيل", "m", "korek"),
    ("u26", "إسراء عماد", "f", "asiacell"),
    ("u27", "رعد كريم", "m", "zain"),
    ("u28", "شهد مؤمل", "f", "zain"),
    ("u29", "فراس عبد الله", "m", "asiacell"),
    ("u30", "سيف ضياء", "m", "zain"),
]

# Balance and daily limit set on purpose for the low-balance and over-limit cases.
PLANNED_MONEY = {
    "u01": (4_250_000, 1_000_000),
    "u12": (2_600_000, 500_000),
    "u14": (60_000, 1_000_000),
    "u19": (50_000, 1_000_000),
    "u21": (1_800_000, 250_000),
    "u22": (9_750, 500_000),
    "u24": (3_100_000, 1_500_000),
    "u30": (42_250, 1_000_000),
}

# Contacts added on purpose: (display_name, gender, operator, alias).
# Near-duplicates, nicknames and saved relation aliases live here.
PLANNED_CONTACTS = {
    "u01": [("أحمد", "m", "asiacell", None)],
    "u02": [("أحمد", "m", "zain", None), ("أحمد", "m", "asiacell", None)],  # same name saved twice
    "u03": [("علي", "m", "zain", "ابني")],
    "u04": [("أحمد", "m", "korek", None), ("احمد", "m", "zain", None)],  # same name, two spellings
    "u05": [("أحمد", "m", "zain", None), ("ام سجاد", "f", "korek", "امي")],
    "u06": [("أحمد", "m", "asiacell", None), ("زهراء", "f", "zain", "مرتي")],
    "u07": [("علي", "m", "asiacell", None), ("زينب", "f", "zain", None)],
    "u08": [("حسين الدورة", "m", "zain", None)],
    "u09": [("احمد شغل", "m", "zain", None), ("احمد الكرادة", "m", "asiacell", None)],
    "u10": [("عباس", "m", "korek", None), ("زينب", "f", "asiacell", None)],
    "u11": [("محمد", "m", "zain", None), ("محمود", "m", "asiacell", None)],  # حمودي fits both
    "u12": [("علي كاظم", "m", "zain", None), ("علي حسن", "m", "korek", None)],
    "u13": [("حمودي", "m", "asiacell", None)],
    "u14": [("محمود", "m", "zain", None)],
    "u15": [("أحمد", "m", "asiacell", None), ("أحمد", "m", "zain", None)],
    "u16": [("ام جعفر", "f", "zain", "امي"), ("حسين", "m", "asiacell", None)],
    "u17": [("ابو مهدي", "m", "asiacell", "زوجي")],
    "u18": [("أنور", "m", "zain", "ابوي")],
    "u19": [("مؤمل", "m", "zain", "اخوي")],
    "u20": [("زينب", "f", "zain", None), ("زينب", "f", "asiacell", None)],
    "u21": [("رسل", "f", "asiacell", "بنتي")],
    "u23": [("زينب", "f", "korek", None)],
    "u27": [("علي", "m", "zain", None)],
}

# Bill accounts set on purpose: (biller_id, label_ar). Other users get random accounts.
PLANNED_ACCOUNTS = {
    "u23": [("electricity", "كهرباء البيت"), ("generator", "المولدة"),
            ("net_rafidain", "نت البيت"), ("net_sama", "نت المحل")],
    "u24": [("electricity", "كهرباء البيت"), ("generator", "المولدة"),
            ("net_sama", "نت البيت"), ("net_rafidain", "نت الطابق الثاني"), ("water", "الماي")],
    "u25": [("electricity", "كهرباء البيت"), ("generator", "مولدة البيت"),
            ("generator", "مولدة المحل"), ("net_rafidain", "نت البيت")],
}

# Users whose current bill of this biller kind is already paid (and who surely have that account).
PLANNED_PAID = {"u26": "generator", "u27": "water", "u28": "internet", "u29": "electricity"}

# Random contacts never use these first names, so the planned near-duplicates stay the only ones.
# The last few are kept free for the unknown-payee cases.
RESERVED_NAMES = {
    "أحمد", "احمد", "محمد", "محمود", "حمودي", "علي", "علاوي", "حسين", "حسن", "حسوني", "عباس", "عبوسي",
    "زينب", "ميثم", "لؤي", "نصير", "شاكر", "تبارك",
}

MALE_NAMES = [
    "كرار", "حيدر", "مصطفى", "مرتضى", "سجاد", "منتظر", "ياسر", "زيد", "مهدي", "باقر", "جعفر", "رضا",
    "أكرم", "فلاح", "ستار", "جبار", "كاظم", "جاسم", "قاسم", "عدنان", "سلام", "ضياء", "نبيل", "وسام",
    "يوسف", "إبراهيم", "عبد الله", "عبد الأمير", "عمار", "ليث", "أنور", "صادق", "هادي", "رعد", "سيف",
    "مؤمل", "نزار", "عماد", "فراس", "أمير", "تحسين", "كريم", "مجيد", "سعد", "أيمن", "حارث",
]
FEMALE_NAMES = [
    "فاطمة", "مريم", "نور", "رقية", "سارة", "هدى", "زهراء", "بنين", "دعاء", "آية", "شهد", "رسل", "سجى",
    "غدير", "حوراء", "إسراء", "رنا", "لمياء", "سهى", "ضحى", "هبة", "ندى", "أسيل", "بتول", "وفاء", "إيمان",
]
FATHER_NAMES = ["جاسم", "كاظم", "عدنان", "ستار", "جبار", "فلاح", "قاسم", "سلام", "كريم", "مجيد", "رعد", "صادق"]
PLACES = [
    "الكرادة", "المنصور", "الشعلة", "الدورة", "زيونة", "الأعظمية", "الكاظمية", "البياع", "الحرية",
    "اليرموك", "الغزالية", "البصرة", "النجف", "كربلاء", "الحلة", "الناصرية",
]
MALE_ROLES = ["شغل", "الشغل", "المكتب", "الجامعة", "الجيم", "ابن خالتي", "ابن عمي", "الحلاق", "الكهربائي", "السايق"]
FEMALE_ROLES = ["الشغل", "الجامعة", "بنت خالتي", "بنت عمي", "الخياطة", "الصيدلية"]
MALE_TITLES = ["حجي", "عمو", "استاذ", "دكتور", "خالو"]
FEMALE_TITLES = ["خالة", "حجية", "ست", "دكتورة", "عمة"]
NAME_STYLES = ["first", "father", "place", "role", "kunya", "title"]
NAME_STYLE_WEIGHTS = [30, 25, 12, 13, 10, 10]

TRANSFER_AMOUNTS = [10_000, 15_000, 20_000, 25_000, 25_000, 50_000, 50_000, 75_000, 100_000, 150_000, 250_000]
TOPUP_AMOUNTS = [5_000, 10_000, 10_000, 25_000]


def iso(moment: datetime) -> str:
    """UTC timestamp in the wallet's storage format: 2026-10-15T06:00:00Z."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_phone(rng: random.Random, operator: str, used: set[str]) -> str:
    """A fictional 11-digit Iraqi mobile number, unique within the seed."""
    while True:
        phone = rng.choice(OPERATOR_PREFIXES[operator]) + f"{rng.randint(0, 9_999_999):07d}"
        if phone not in used:
            used.add(phone)
            return phone


def build_users(rng: random.Random, used_phones: set[str]) -> list[dict]:
    users = []
    for uid, name, gender, operator in USERS:
        balance, daily_limit = PLANNED_MONEY.get(
            uid, (rng.randint(1_600, 12_000) * 250, rng.choice([1_000_000, 1_500_000, 2_000_000]))
        )
        users.append({
            "id": uid,
            "name": name,
            "phone": new_phone(rng, operator, used_phones),
            "balance": balance,
            "daily_limit": daily_limit,
            "pin": f"{rng.randint(0, 9999):04d}",  # synthetic; the wallet hashes it on load
            "gender": gender,
        })
    return users


def random_display_name(rng: random.Random, gender: str, free_male: list[str], free_female: list[str]) -> str:
    """One saved contact name in a style Iraqis actually use (first name only, with a place, حجي ...)."""
    style = rng.choices(NAME_STYLES, NAME_STYLE_WEIGHTS)[0]
    pool = free_male if (gender == "m" or style == "kunya") else free_female
    first = pool.pop(rng.randrange(len(pool)))
    if style == "first":
        return first
    if style == "father":
        return f"{first} {rng.choice([f for f in FATHER_NAMES if f != first])}"
    if style == "place":
        return f"{first} {rng.choice(PLACES)}"
    if style == "role":
        return f"{first} {rng.choice(MALE_ROLES if gender == 'm' else FEMALE_ROLES)}"
    if style == "kunya":
        return f"{'ابو' if gender == 'm' else 'ام'} {first}"
    return f"{rng.choice(MALE_TITLES if gender == 'm' else FEMALE_TITLES)} {first}"


def build_contacts(rng: random.Random, users: list[dict], used_phones: set[str]) -> list[dict]:
    contacts = []
    for user in users:
        planned = PLANNED_CONTACTS.get(user["id"], [])
        taken = {word for name, *_ in planned for word in name.split()} | RESERVED_NAMES
        free_male = [n for n in MALE_NAMES if n not in taken]
        free_female = [n for n in FEMALE_NAMES if n not in taken]
        rows = [(name, op, alias) for name, _gender, op, alias in planned]
        for _ in range(rng.randint(11, 14)):
            gender = "m" if rng.random() < 0.65 else "f"
            name = random_display_name(rng, gender, free_male, free_female)
            rows.append((name, rng.choices(["zain", "asiacell", "korek"], [45, 40, 15])[0], None))
        rng.shuffle(rows)
        for name, operator, alias in rows:
            contacts.append({
                "contact_id": f"c{len(contacts) + 1:03d}",
                "user_id": user["id"],
                "display_name": name,
                "phone": new_phone(rng, operator, used_phones),
                "alias": alias,
            })
    return contacts


def random_accounts(rng: random.Random, uid: str) -> list[tuple[str, str]]:
    """(biller_id, label_ar) pairs for a user without a planned account list."""
    needed = PLANNED_PAID.get(uid)
    accounts = [("electricity", "كهرباء البيت")]
    if needed == "generator" or rng.random() < 0.85:
        accounts.append(("generator", "المولدة"))
    if needed == "internet" or rng.random() < 0.8:
        accounts.append((rng.choice(["net_rafidain", "net_sama"]), "نت البيت"))
    if needed == "water" or rng.random() < 0.6:
        accounts.append(("water", "الماي"))
    return accounts


def account_number(rng: random.Random, biller_id: str) -> str:
    if biller_id == "electricity":
        return f"{rng.randint(10**9, 10**10 - 1)}"
    if biller_id == "generator":
        return f"{rng.randint(100, 999)}"  # subscriber number in the generator owner's book
    if biller_id.startswith("net_"):
        return f"{'RN' if biller_id == 'net_rafidain' else 'SN'}-{rng.randint(100_000, 999_999)}"
    return f"{rng.randint(10**7, 10**8 - 1)}"


def build_bill_accounts(rng: random.Random, users: list[dict]) -> list[dict]:
    accounts = []
    for user in users:
        father = user["name"].split(" ", 1)[1]
        for biller_id, label in PLANNED_ACCOUNTS.get(user["id"]) or random_accounts(rng, user["id"]):
            # Electricity is often still in the father's name.
            holder = user["name"]
            if biller_id == "electricity" and rng.random() < 0.3:
                holder = f"{father} {rng.choice(FATHER_NAMES)}"
            accounts.append({
                "id": f"a{len(accounts) + 1:03d}",
                "user_id": user["id"],
                "biller_id": biller_id,
                "account_no": account_number(rng, biller_id),
                "holder_name": holder,
                "label_ar": label,
            })
    return accounts


def monthly_amounts(rng: random.Random, kind: str) -> list[int]:
    """One amount per period, multiples of 250 IQD."""
    if kind == "generator":  # amperes x price per ampere, the same every month
        return [rng.choice([3, 4, 5, 6, 8, 10]) * rng.choice([9_000, 10_000, 12_000, 13_000, 15_000])] * len(PERIODS)
    if kind == "internet":
        return [rng.choice([25_000, 30_000, 35_000, 40_000, 50_000])] * len(PERIODS)
    low, high = (60, 360) if kind == "electricity" else (16, 56)
    base = rng.randint(low, high)
    return [max(low, base + rng.randint(-16, 16)) * 250 for _ in PERIODS]


def build_bills(rng: random.Random, accounts: list[dict]) -> list[dict]:
    kinds = {b["id"]: b["kind"] for b in BILLERS}
    planned_users = set(PLANNED_ACCOUNTS) | set(PLANNED_PAID)
    bills = []
    for account in accounts:
        kind = kinds[account["biller_id"]]
        for period, amount in zip(PERIODS, monthly_amounts(rng, kind)):
            status = "paid"
            if period == CURRENT_PERIOD:
                if PLANNED_PAID.get(account["user_id"]) == kind:
                    status = "paid"
                elif account["user_id"] in planned_users:
                    status = "unpaid"
                else:
                    status = "paid" if rng.random() < 0.1 else "unpaid"
            bills.append({
                "id": f"bl{len(bills) + 1:04d}",
                "account_id": account["id"],
                "period": period,
                "amount_due": amount,
                "status": status,
            })
    return bills


def random_moment_in(rng: random.Random, start: datetime, end: datetime) -> datetime:
    """A daytime moment (Baghdad 08:00-22:00) between two dates."""
    day = start + timedelta(days=rng.randint(0, max(0, (end - start).days)))
    moment = day.replace(hour=rng.randint(5, 18), minute=rng.randint(0, 59), second=rng.randint(0, 59))
    return min(moment, SNAPSHOT - timedelta(days=1))


def build_transactions(rng, users, contacts, accounts, bills) -> list[dict]:
    """Six months of history: every paid bill, plus transfers and top-ups."""
    history_start = SNAPSHOT - timedelta(days=HISTORY_DAYS)
    account_by_id = {a["id"]: a for a in accounts}
    rows = []
    for bill in bills:
        if bill["status"] != "paid":
            continue
        account = account_by_id[bill["account_id"]]
        year, month = map(int, bill["period"].split("-"))
        start = datetime(year, month, 3, tzinfo=timezone.utc)
        end = SNAPSHOT - timedelta(days=2) if bill["period"] == CURRENT_PERIOD else start + timedelta(days=22)
        target = {"biller_id": account["biller_id"], "account_id": account["id"],
                  "bill_id": bill["id"], "period": bill["period"]}
        rows.append((random_moment_in(rng, max(start, history_start), end), account["user_id"], "bill",
                     target, bill["amount_due"], 0))
    for user in users:
        mine = [c for c in contacts if c["user_id"] == user["id"]]
        favourites = rng.sample(mine, k=rng.randint(3, 6))
        for _ in range(rng.randint(4, 14)):
            contact = rng.choice(favourites)
            target = {"contact_id": contact["contact_id"], "name": contact["display_name"], "phone": contact["phone"]}
            rows.append((random_moment_in(rng, history_start, SNAPSHOT), user["id"], "transfer",
                         target, rng.choice(TRANSFER_AMOUNTS), TRANSFER_FEE))
        operator = next(op for op, prefixes in OPERATOR_PREFIXES.items() if user["phone"].startswith(prefixes))
        for _ in range(rng.randint(2, 6)):
            target = {"biller_id": operator, "phone": user["phone"]}
            rows.append((random_moment_in(rng, history_start, SNAPSHOT), user["id"], "topup",
                         target, rng.choice(TOPUP_AMOUNTS), 0))
    rows.sort(key=lambda row: (row[0], row[1]))
    return [
        {
            "id": f"t{n:04d}",
            "quote_id": f"seed-q{n:04d}",  # history only: no matching row in the quotes table
            "user_id": uid,
            "kind": kind,
            "target_json": json.dumps(target, ensure_ascii=False),
            "amount": amount,
            "fee": fee,
            "status": "succeeded",
            "created_at": iso(moment),
        }
        for n, (moment, uid, kind, target, amount, fee) in enumerate(rows, start=1)
    ]


def build_seed(seed: int = 42) -> dict:
    """The whole wallet snapshot as plain dicts, ready for json.dump."""
    rng = random.Random(seed)
    used_phones: set[str] = set()
    users = build_users(rng, used_phones)
    contacts = build_contacts(rng, users, used_phones)
    accounts = build_bill_accounts(rng, users)
    bills = build_bills(rng, accounts)
    transactions = build_transactions(rng, users, contacts, accounts, bills)
    return {
        "users": users,
        "contacts": contacts,
        "billers": BILLERS,
        "bill_accounts": accounts,
        "bills": bills,
        "transactions": transactions,
    }


def main() -> None:
    data = build_seed()
    SEED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {SEED_FILE}: " + ", ".join(f"{table}={len(rows)}" for table, rows in data.items()))


if __name__ == "__main__":
    main()

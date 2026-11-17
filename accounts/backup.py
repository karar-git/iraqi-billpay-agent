"""Backups of the accounts database: SQLite's online backup API, checked, kept for BACKUP_KEEP_DAYS.

- daily(): the first call on a Baghdad day at or after BACKUP_HOUR writes backups/accounts-YYYY-MM-DD.db,
  runs PRAGMA integrity_check on the copy (a bad copy is deleted and reported), then deletes daily copies
  older than the retention and pre-migration snapshots older than 30 days.
- now(): a backup on demand (the admin page's «Back up now»).
- A backup on the same volume protects against bad code, not against losing the volume: Railway volume
  backups (and an occasional off-volume copy) cover that (deploy notes).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from wallet.db import BAGHDAD

log = logging.getLogger("accounts.backup")


def keep_days() -> int:
    try:
        return max(1, int(os.environ.get("BACKUP_KEEP_DAYS") or 14))
    except ValueError:
        return 14


def backup_hour() -> int:
    try:
        return min(23, max(0, int(os.environ.get("BACKUP_HOUR") or 4)))
    except ValueError:
        return 4


def folder(db_path: str | Path) -> Path:
    return Path(db_path).parent / "backups"


def _copy(db_path: str | Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    src = sqlite3.connect(str(db_path))
    dest = sqlite3.connect(str(tmp))
    try:
        src.backup(dest)
        ok = dest.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        dest.close()
        src.close()
    if not ok:
        tmp.unlink(missing_ok=True)
        log.error("backup failed its integrity check: %s", target.name)
        return False
    os.replace(tmp, target)
    return True


def now(db_path: str | Path, moment: datetime) -> Path | None:
    stamp = moment.astimezone(BAGHDAD).strftime("%Y-%m-%dT%H%M")
    target = folder(db_path) / f"{Path(db_path).stem}-manual-{stamp}.db"
    return target if _copy(db_path, target) else None


def daily(db_path: str | Path, moment: datetime) -> Path | None:
    """Today's backup when it is due and missing; prunes old copies. None when nothing was written."""
    local = moment.astimezone(BAGHDAD)
    target = folder(db_path) / f"{Path(db_path).stem}-{local:%Y-%m-%d}.db"
    written = None
    if local.hour >= backup_hour() and not target.exists() and Path(db_path).exists():
        written = target if _copy(db_path, target) else None
    prune(db_path, moment)
    return written


def prune(db_path: str | Path, moment: datetime) -> int:
    removed = 0
    today = moment.astimezone(BAGHDAD).date()
    for path in folder(db_path).glob("*.db"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        if not m:
            m2 = re.search(r"(\d{8})T", path.name)
            if not m2:
                continue
            day = datetime.strptime(m2.group(1), "%Y%m%d").date()
        else:
            day = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        keep = timedelta(days=30) if "-pre-v" in path.name else timedelta(days=keep_days())
        if today - day > keep:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def listing(db_path: str | Path) -> list[dict]:
    out = []
    for path in sorted(folder(db_path).glob("*.db"), reverse=True):
        out.append({"name": path.name, "size": path.stat().st_size,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime, BAGHDAD).strftime("%Y-%m-%d %H:%M")})
    return out

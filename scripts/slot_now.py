"""時間の実験: 今が「実験の投稿の時刻」かどうかを調べる。

config の schedule.slots = {"start": "YYYY-MM-DD", "days": 13, "hours": [[7,12,19],[8,13,20],[9,14,21]]}
start の日を1日目として、hours を順にくり返す。その日の時刻の「0〜19分」の間だけ yes を返す
（GitHub の実行が少し遅れても拾えるように幅を持たせる。同じ時刻に2回は main.py が出さない）。

使い方: python scripts/slot_now.py   → "yes 19" か "no"
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent


def hours_for(cfg: dict, d: date) -> list[int]:
    s = (cfg.get("schedule") or {}).get("slots") or {}
    if not s.get("start") or not s.get("hours"):
        return []
    n = (d - datetime.strptime(s["start"], "%Y-%m-%d").date()).days
    if n < 0 or n >= int(s.get("days", 0)):
        return []
    return list(s["hours"][n % len(s["hours"])])


def main() -> int:
    cfg = json.loads((ROOT / "config" / "clinic.json").read_text(encoding="utf-8"))
    now = datetime.now(JST)
    if now.hour in hours_for(cfg, now.date()) and now.minute < 20:
        print(f"yes {now.hour}")
    else:
        print("no")
    return 0


if __name__ == "__main__":
    sys.exit(main())

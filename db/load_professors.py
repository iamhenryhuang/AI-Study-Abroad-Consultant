"""
從 db/data/professors.json 寫入 professors（教授名單種子資料）。

每筆資料需對應到已存在的 universities.school_id（透過 db/load_schools.py
或 data_crawler 先建立），本腳本不會建立新學校。

執行：python db/load_professors.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SCRIPTS = _PROJECT_ROOT / "backend" / "scripts"
if str(_BACKEND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SCRIPTS))

from db.connection import get_connection

DATA_PATH = Path(__file__).resolve().parent / "data" / "professors.json"


def load() -> bool:
    professors = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    conn = get_connection()
    if not conn:
        print("請在 backend/.env 設定 DATABASE_URL。")
        return False

    try:
        with conn.cursor() as cur:
            skipped = []
            written = 0
            for p in professors:
                cur.execute(
                    "SELECT id FROM universities WHERE school_id = %s",
                    (p["school_id"],),
                )
                row = cur.fetchone()
                if not row:
                    skipped.append(f"{p['name']}（{p['school_id']}：學校不存在）")
                    continue
                university_id = row[0]

                cur.execute(
                    """
                    INSERT INTO professors (university_id, name, title, research_areas, profile_url)
                    VALUES (%(university_id)s, %(name)s, %(title)s, %(research_areas)s, %(profile_url)s)
                    ON CONFLICT (university_id, name) DO UPDATE SET
                        title          = EXCLUDED.title,
                        research_areas = EXCLUDED.research_areas,
                        profile_url    = EXCLUDED.profile_url
                    """,
                    {
                        "university_id": university_id,
                        "name": p["name"],
                        "title": p.get("title") or None,
                        "research_areas": p.get("research_areas", []),
                        "profile_url": p.get("profile_url") or None,
                    },
                )
                written += 1

        conn.commit()
        print(f"已寫入 {written} 位教授。")
        if skipped:
            print(f"略過 {len(skipped)} 筆（學校不存在於 universities）：")
            for s in skipped:
                print(f"  - {s}")
        return True
    except Exception as e:
        conn.rollback()
        print(f"寫入失敗: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(0 if load() else 1)

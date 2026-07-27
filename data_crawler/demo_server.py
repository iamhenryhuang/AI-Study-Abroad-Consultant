"""Data crawler 即時 Demo Dashboard。

啟動：
    python -m data_crawler.demo_server --school-id gatech --port 8765
"""
from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db as dbm
from .demo_events import event_path


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "demo_web"
OUTPUT_DIR = ROOT / "output"
URL_DIR = ROOT / "url"


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _read_events(school_id: str) -> list[dict]:
    try:
        events = []
        for line in event_path(school_id).read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events[-1200:]
    except OSError:
        return []


def _db_snapshot(school_id: str) -> dict:
    try:
        conn = dbm.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.school_id, u.name, p.id AS program_id, p.*
                   FROM universities u
                   LEFT JOIN programs p ON p.university_id = u.id
                   WHERE u.school_id = %s""",
                (school_id,),
            )
            row = cur.fetchone()
            if not row:
                conn.close()
                return {"connected": True, "found": False}
            columns = [desc.name for desc in cur.description]
            program = dict(zip(columns, row))

            def rows(sql: str) -> list[dict]:
                cur.execute(sql, (school_id,))
                names = [desc.name for desc in cur.description]
                return [dict(zip(names, item)) for item in cur.fetchall()]

            deadlines = rows(
                """SELECT d.deadline_type, d.application_open_date,
                          d.application_close_date, d.decision_release_date,
                          d.semester, d.note
                   FROM program_deadlines d
                   JOIN programs p ON p.id=d.program_id
                   JOIN universities u ON u.id=p.university_id
                   WHERE u.school_id=%s ORDER BY d.application_close_date"""
            )
            evidence = rows(
                """SELECT e.category, e.field_name, e.evidence_kind,
                          e.evidence_text, e.source_excerpt, e.source_url
                   FROM program_evidence e
                   JOIN programs p ON p.id=e.program_id
                   JOIN universities u ON u.id=p.university_id
                   WHERE u.school_id=%s ORDER BY e.category, e.field_name LIMIT 100"""
            )
            reviews = rows(
                """SELECT r.field_name, r.field_value, r.reason, r.source_excerpt, r.status
                   FROM review_queue r JOIN universities u ON u.id=r.university_id
                   WHERE u.school_id=%s ORDER BY r.id"""
            )
            cur.execute(
                """SELECT
                     (SELECT count(*) FROM web_pages w JOIN universities u ON u.id=w.university_id
                      WHERE u.school_id=%s) AS pages,
                     (SELECT count(*) FROM document_chunks c WHERE c.school_id=%s) AS chunks""",
                (school_id, school_id),
            )
            pages, chunks = cur.fetchone()
        conn.close()
        return {
            "connected": True,
            "found": True,
            "program": program,
            "deadlines": deadlines,
            "evidence": evidence,
            "reviews": reviews,
            "counts": {"pages": pages, "chunks": chunks, "evidence": len(evidence),
                       "reviews": len(reviews)},
        }
    except Exception as exc:
        return {"connected": False, "found": False, "error": str(exc)}


def build_state(school_id: str) -> dict:
    result = _read_json(OUTPUT_DIR / f"{school_id}_result.json", {})
    filter_review = _read_json(
        OUTPUT_DIR / f"{school_id}_url_filter_review.json", {}
    )
    return {
        "school_id": school_id,
        "events": _read_events(school_id),
        "result": result,
        "url_filter": filter_review,
        "urls": {
            "all": _read_json(URL_DIR / "all_url" / f"{school_id}.json", []),
            "keep": _read_json(URL_DIR / "keep" / f"{school_id}.json", {}),
            "drop": _read_json(URL_DIR / "drop" / f"{school_id}.json", {}),
        },
        "db": _db_snapshot(school_id),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    default_school = "gatech"

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            school_id = parse_qs(parsed.query).get(
                "school_id", [self.default_school]
            )[0]
            self._send_json(build_state(school_id))
            return
        relative = "index.html" if parsed.path in ("", "/") else parsed.path.lstrip("/")
        path = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in path.parents and path != WEB_ROOT.resolve():
            self.send_error(403)
            return
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Data crawler demo dashboard")
    parser.add_argument("--school-id", default="gatech")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    DashboardHandler.default_school = args.school_id
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Data Crawler Dashboard: http://{args.host}:{args.port}/?school={args.school_id}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

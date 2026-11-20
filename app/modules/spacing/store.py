import json
import sqlite3
from contextlib import closing, contextmanager

from app.errors import FieldError
from app.manifest import canonical


class SpacingStore:
    """间距复核版本库。

    复核结论只增不改：失败登记保留旧版，成功登记另起新版本，原始读数与旧尺寸随
    结论一并留存，任何复尺都不抹掉旧证。同一复尺单（相同请求指纹）重跑不新增版本。
    """

    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            yield connection

    def setup(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS spacing_reviews (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    measure_no TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    baseline_id TEXT,
                    report TEXT NOT NULL,
                    created TEXT NOT NULL,
                    UNIQUE(run_id, fingerprint)
                )
            """)

    def insert(self, record):
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO spacing_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id, fingerprint) DO NOTHING",
                (record["id"], record["run_id"], record["measure_no"], record["status"],
                 record["fingerprint"], record.get("baseline_id"), canonical(record["report"]),
                 record["created"]))
            row = connection.execute(
                "SELECT report FROM spacing_reviews WHERE run_id = ? AND fingerprint = ?",
                (record["run_id"], record["fingerprint"])).fetchone()
            return json.loads(row[0]), cursor.rowcount == 1

    def history(self, run_id):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, measure_no, status, fingerprint, baseline_id, report, created"
                " FROM spacing_reviews WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
        return [{"id": rid, "measure_no": measure_no, "status": status, "fingerprint": fingerprint,
                 "baseline_id": baseline_id, "report": json.loads(report), "created": created}
                for rid, measure_no, status, fingerprint, baseline_id, report, created in rows]

    def latest_published(self, run_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, report FROM spacing_reviews WHERE run_id = ? AND status = 'PUBLISHED'"
                " ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
        if row is None:
            return None
        return {"id": row[0], "report": json.loads(row[1])}

    def fetch(self, review_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, run_id, measure_no, status, baseline_id, report, created"
                " FROM spacing_reviews WHERE id = ?", (review_id,)).fetchone()
        if row is None:
            raise FieldError("UNKNOWN_REVIEW", "复核结论未登记", 404)
        return {"id": row[0], "run_id": row[1], "measure_no": row[2], "status": row[3],
                "baseline_id": row[4], "report": json.loads(row[5]), "created": row[6]}

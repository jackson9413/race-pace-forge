"""SQLite persistence for Race Pace Forge - saved plans and self-ratings."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "data" / "racepace.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def db_session(db_path: str | Path = DEFAULT_DB):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(db_path: str | Path = DEFAULT_DB) -> None:
    with db_session(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_name TEXT NOT NULL,
                distance TEXT NOT NULL,
                goal_time TEXT NOT NULL,
                review_profile TEXT NOT NULL,
                overall REAL NOT NULL,
                verdict TEXT NOT NULL,
                rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                plan_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
            """
        )


def save_run(db_path: str | Path, plan: dict[str, Any], rating: int | None = None,
             notes: str = "") -> dict[str, Any]:
    profile = plan["profile"]
    score = plan["scorecard"]
    created_at = utc_now()
    with db_session(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO runs (race_name, distance, goal_time, review_profile, overall, verdict,
                              rating, notes, created_at, plan_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile["race_name"],
                profile["distance"],
                profile["goal_time"],
                score["profile"],
                score["overall"],
                score["verdict"],
                rating,
                str(notes or "")[:500],
                created_at,
                json.dumps(plan, ensure_ascii=False),
            ),
        )
        run_id = cursor.lastrowid
    result = dict(plan)
    result["id"] = run_id
    result["created_at"] = created_at
    result["rating"] = rating
    result["notes"] = str(notes or "")[:500]
    return result


def list_runs(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    with db_session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, race_name, distance, goal_time, review_profile, overall, verdict,
                   rating, notes, created_at
            FROM runs ORDER BY id DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_run(db_path: str | Path, run_id: int) -> dict[str, Any] | None:
    with db_session(db_path) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    plan = json.loads(row["plan_json"])
    plan["id"] = row["id"]
    plan["created_at"] = row["created_at"]
    plan["rating"] = row["rating"]
    plan["notes"] = row["notes"]
    return plan


def rate_run(db_path: str | Path, run_id: int, rating: int | None,
             notes: str = "") -> dict[str, Any] | None:
    with db_session(db_path) as conn:
        cursor = conn.execute(
            "UPDATE runs SET rating = ?, notes = ? WHERE id = ?",
            (rating, str(notes or "")[:500], run_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT id, race_name, distance, goal_time, overall, verdict, rating, notes, created_at "
            "FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return dict(row)


def delete_run(db_path: str | Path, run_id: int) -> bool:
    with db_session(db_path) as conn:
        cursor = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        return cursor.rowcount > 0

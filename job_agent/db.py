from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("JOB_AGENT_DATA_DIR", ROOT_DIR / "data")).expanduser()
DATABASE_PATH = DATA_DIR / "applications.sqlite3"


def get_connection(database_path: Path | None = None) -> sqlite3.Connection:
    path = database_path or DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(database_path: Path | None = None) -> None:
    with get_connection(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                posted_at TEXT NOT NULL DEFAULT '',
                experience_level TEXT NOT NULL DEFAULT 'Unknown',
                workplace_type TEXT NOT NULL DEFAULT 'Unknown',
                employment_type TEXT NOT NULL DEFAULT 'Unknown',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, external_id)
            );
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
                score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'Pending approval',
                application_date TEXT,
                resume_path TEXT NOT NULL DEFAULT '',
                cover_letter_path TEXT NOT NULL DEFAULT '',
                resume_version TEXT NOT NULL DEFAULT '',
                cover_letter_version TEXT NOT NULL DEFAULT '',
                recruiter_response TEXT NOT NULL DEFAULT '',
                interview_status TEXT NOT NULL DEFAULT 'Not started',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "UPDATE applications SET status = 'Pending approval' WHERE status = 'Draft'"
        )


def save_job(job: dict[str, Any], database_path: Path | None = None) -> int:
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO jobs
                (source, external_id, company, title, location, url, description,
                 posted_at, experience_level, workplace_type, employment_type)
            VALUES
                (:source, :external_id, :company, :title, :location, :url, :description,
                 :posted_at, :experience_level, :workplace_type, :employment_type)
            ON CONFLICT(url) DO UPDATE SET
                source = excluded.source,
                external_id = excluded.external_id,
                company = excluded.company,
                title = excluded.title,
                location = excluded.location,
                url = excluded.url,
                description = excluded.description,
                posted_at = excluded.posted_at,
                experience_level = excluded.experience_level,
                workplace_type = excluded.workplace_type,
                employment_type = excluded.employment_type
            """,
            job,
        )
        row = connection.execute(
            "SELECT id FROM jobs WHERE url = ?",
            (job["url"],),
        ).fetchone()
        if row is None:
            raise RuntimeError("The job could not be saved to the database.")
        if not job["description"].strip():
            connection.execute("DELETE FROM matches WHERE job_id = ?", (row["id"],))
        return int(row["id"])


def list_jobs(
    search: str = "",
    database_path: Path | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT jobs.*, matches.score AS match_score,
               matches.result_json AS match_result,
               CASE WHEN matches.job_id IS NULL THEN 'Pending' ELSE 'Matched' END AS match_status
        FROM jobs LEFT JOIN matches ON jobs.id = matches.job_id
    """
    params: tuple[str, ...] = ()
    if search.strip():
        query += " WHERE jobs.title LIKE ? OR jobs.company LIKE ? OR jobs.location LIKE ?"
        term = f"%{search.strip()}%"
        params = (term, term, term)
    query += " ORDER BY COALESCE(jobs.posted_at, '') DESC, jobs.created_at DESC"
    with get_connection(database_path) as connection:
        return [dict(row) for row in connection.execute(query, params).fetchall()]


def get_job(job_id: int, database_path: Path | None = None) -> dict[str, Any] | None:
    with get_connection(database_path) as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def save_match(
    job_id: int,
    result: dict[str, Any],
    database_path: Path | None = None,
) -> None:
    score = int(result["score"])
    if not 0 <= score <= 100:
        raise ValueError("Match score must be between 0 and 100.")
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO matches (job_id, score, result_json) VALUES (?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                score = excluded.score,
                result_json = excluded.result_json,
                created_at = CURRENT_TIMESTAMP
            """,
            (job_id, score, json.dumps(result, ensure_ascii=False)),
        )


def create_application(
    job_id: int,
    resume_path: str = "",
    cover_letter_path: str = "",
    database_path: Path | None = None,
) -> int:
    with get_connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO applications (job_id, status, resume_path, cover_letter_path)
            VALUES (?, 'Pending approval', ?, ?)
            """,
            (job_id, resume_path, cover_letter_path),
        )
        return int(cursor.lastrowid)


def update_application(
    application_id: int,
    database_path: Path | None = None,
    **fields: str | None,
) -> None:
    allowed = {
        "status",
        "application_date",
        "resume_path",
        "cover_letter_path",
        "resume_version",
        "cover_letter_version",
        "recruiter_response",
        "interview_status",
    }
    if not fields or not fields.keys() <= allowed:
        raise ValueError("Unsupported or empty application update.")
    if "status" in fields:
        with get_connection(database_path) as connection:
            row = connection.execute(
                "SELECT status FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
        if row is None:
            raise ValueError("The application does not exist.")
        current_status = row["status"]
        next_status = fields["status"]
        valid_transitions = {
            "Pending approval": {"Approved", "Rejected", "Withdrawn"},
            "Approved": {"Submitted", "Rejected", "Withdrawn"},
            "Submitted": {"Interviewing", "Rejected", "Offer", "Withdrawn"},
            "Interviewing": {"Rejected", "Offer", "Withdrawn"},
            "Rejected": set(),
            "Offer": set(),
            "Withdrawn": set(),
        }
        if next_status != current_status and next_status not in valid_transitions.get(
            current_status, set()
        ):
            raise ValueError(
                f"Application status cannot change from {current_status} to {next_status}."
            )
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = [*fields.values(), application_id]
    with get_connection(database_path) as connection:
        connection.execute(
            f"UPDATE applications SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
        )


def list_applications(database_path: Path | None = None) -> list[dict[str, Any]]:
    with get_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT applications.*, jobs.company, jobs.title, jobs.location, jobs.url,
                   matches.score AS match_score
            FROM applications
            JOIN jobs ON jobs.id = applications.job_id
            LEFT JOIN matches ON matches.job_id = jobs.id
            ORDER BY applications.updated_at DESC, applications.id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_setting(
    key: str,
    default: Any = None,
    database_path: Path | None = None,
) -> Any:
    with get_connection(database_path) as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any, database_path: Path | None = None) -> None:
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, json.dumps(value, ensure_ascii=False)),
        )

"""Employee master data persistence (SQLite).

The assignment requires that the monthly salary upload looks employees up by
Employee ID ("backend should fetch the details of the employee"), which means
the employee master is *stored*, not re-uploaded each cycle. This module owns
that storage using Python's stdlib `sqlite3` — zero extra dependencies and
trivial to deploy.

A fresh connection is opened per operation. FastAPI runs sync endpoints in a
threadpool, so each request may land on a different thread; per-operation
connections sidestep SQLite's same-thread restriction cleanly.

NOTE for deployment: on free tiers with an ephemeral filesystem (e.g. Render
free), the SQLite file resets on redeploy. That's fine for the demo and local
dev; for durable persistence point `database_path` at a mounted disk or swap
this class for a hosted Postgres (Supabase/Neon) — the public interface here
would stay the same.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.file_parser import EmployeeRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    employee_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    designation  TEXT NOT NULL
);
"""


class EmployeeStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        # Ensure the parent directory exists (no-op for a bare filename).
        parent = Path(database_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def upsert_employees(self, employees: list[EmployeeRecord]) -> int:
        """Insert or update employees keyed on Employee ID. Returns row count."""
        rows = [(e.employee_id, e.name, str(e.email), e.designation) for e in employees]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO employees (employee_id, name, email, designation)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(employee_id) DO UPDATE SET
                    name        = excluded.name,
                    email       = excluded.email,
                    designation = excluded.designation
                """,
                rows,
            )
        return len(rows)

    def get(self, employee_id: str) -> EmployeeRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT employee_id, name, email, designation FROM employees WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    def get_all(self) -> list[EmployeeRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT employee_id, name, email, designation FROM employees ORDER BY employee_id"
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            (n,) = conn.execute("SELECT COUNT(*) FROM employees").fetchone()
        return int(n)

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM employees")


def _row_to_record(row: sqlite3.Row) -> EmployeeRecord:
    return EmployeeRecord(
        employee_id=row["employee_id"],
        name=row["name"],
        email=row["email"],
        designation=row["designation"],
    )
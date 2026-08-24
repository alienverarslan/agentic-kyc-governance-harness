"""SQLite side-effect sink.

The act node is where the agent's decision becomes an external effect: an approval
record, or an opened case (optionally flagged for escalation). Persisting these makes
runs auditable and lets tests assert that the right side effect happened.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from harness.contracts.findings import CaseRecord


class CaseStore:
    """Thin wrapper over a sqlite3 connection holding decision side effects."""

    def __init__(self, path: str = ":memory:") -> None:
        # check_same_thread=False: the FastAPI TestClient (and uvicorn) may invoke the
        # act node from a worker thread. Access is serialized within a single run, so
        # sharing the connection across threads is safe here.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS case_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_ref TEXT NOT NULL,
                decision TEXT NOT NULL,
                required_actions TEXT NOT NULL,
                escalated INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def write_record(self, record: CaseRecord) -> int:
        """Persist one decision record; returns its row id."""
        cur = self._conn.execute(
            "INSERT INTO case_records "
            "(case_ref, decision, required_actions, escalated, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                record.case_ref,
                record.decision,
                json.dumps(record.required_actions),
                int(record.escalated),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def fetch(self, case_ref: str) -> list[CaseRecord]:
        """Return all records for a case_ref (used by tests)."""
        rows = self._conn.execute(
            "SELECT case_ref, decision, required_actions, escalated "
            "FROM case_records WHERE case_ref = ? ORDER BY id",
            (case_ref,),
        ).fetchall()
        return [
            CaseRecord(
                case_ref=r["case_ref"],
                decision=r["decision"],
                required_actions=json.loads(r["required_actions"]),
                escalated=bool(r["escalated"]),
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()

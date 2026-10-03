"""Audit log: every mutation records who, when, what, before-state, after-state."""
from __future__ import annotations

import json

from . import db


def record(user_id: int | None, action: str, target: str,
           before: dict | None = None, after: dict | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO audit_log(user_id, action, target, before_state, after_state, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, action, target,
             json.dumps(before) if before is not None else None,
             json.dumps(after) if after is not None else None,
             db.now()),
        )


def list_entries(limit: int = 200) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.id, a.action, a.target, a.before_state, a.after_state, a.created_at,
                      u.username
               FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
               ORDER BY a.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]

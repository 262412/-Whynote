"""Read-only local review inventory. No bodies, identities or automatic promotion."""

import argparse
import json
import sqlite3
from pathlib import Path


def inventory(database: Path) -> list[dict]:
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT f.id,f.user_id,f.meta,f.association_version,f.verified_chat_id,"
            "f.verified_message_id,r.reason FROM feedback f "
            "LEFT JOIN whynote_feedback_review r ON r.feedback_id=f.id "
            "WHERE f.association_version IS NULL OR r.feedback_id IS NOT NULL ORDER BY f.id"
        ).fetchall()
        result = []
        for row in rows:
            try:
                meta = json.loads(row["meta"]) if row["meta"] else {}
                if not isinstance(meta, dict):
                    raise ValueError
                chat_id, message_id = meta.get("chat_id"), meta.get("message_id")
                if any(
                    value is not None and (not isinstance(value, str) or not value.strip())
                    for value in (chat_id, message_id)
                ):
                    raise ValueError
                if chat_id is None:
                    category = "unknown" if message_id is not None else "unlinked"
                else:
                    owner = db.execute("SELECT user_id FROM chat WHERE id=?", (chat_id,)).fetchone()
                    if owner is None:
                        category = "target_missing"
                    elif owner[0] != row["user_id"]:
                        category = "owner_conflict"
                    elif (
                        message_id is not None
                        and db.execute(
                            "SELECT 1 FROM chat_message WHERE chat_id=? AND id=?", (chat_id, f"{chat_id}-{message_id}")
                        ).fetchone()
                        is None
                    ):
                        category = "message_missing"
                    else:
                        category = "unverified_current_match"
                if row["association_version"] is not None:
                    category = "association_conflict"
            except (ValueError, TypeError):
                category = "unknown"
            result.append(
                {
                    "feedback_id": row["id"],
                    "category": category,
                    "task_reason": row["reason"],
                    "outcome": "pending_review",
                }
            )
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Do not overwrite an existing review record or the database.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump({"schema": "whynote-feedback-review-v1", "items": inventory(args.database)}, stream, indent=2)


if __name__ == "__main__":
    main()

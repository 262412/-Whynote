"""Single-user S1 generation receipts and conservative CNY reservations.

No prompt, answer, provider credential, or raw error is persisted here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import research
from .domain import ConflictError, NotFoundError, Principal
from .store import EventStore

PIPE_ID = "whynote_s1_pipe"
SYSTEM = "你是一个有帮助的中文助手。请直接回答用户的问题。"
BUDGET_MICRO_CNY = 100_000_000
# Above the provider's documented 1M context; no cache/off-peak discount assumed.
INPUT_TOKEN_BOUND = 2_000_000
RESERVATION_MICRO_CNY = INPUT_TOKEN_BOUND * 2 + 1024 * 8


def digest(key: bytes, value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def load_config(path: str | Path) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("enabled") is not True:
        raise NotFoundError("S1 entry is disabled")
    research.validate_config(config)
    for field in ("instance_id", "user_id", "db_path", "version_key"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ValueError("S1 configuration is incomplete")
    started = config.get("started_at")
    if type(started) not in (int, float) or not math.isfinite(started) or started <= 0:
        raise ValueError("S1 start time is invalid")
    if len(config["version_key"].encode()) < 32 or not Path(config["db_path"]).is_absolute():
        raise ValueError("S1 storage configuration is invalid")
    mode = config.get("mode")
    if mode == "mock":
        from urllib.parse import urlsplit

        url = urlsplit(config.get("base_url", ""))
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.path != "/v1" or url.query or url.fragment:
            raise ValueError("S1 mock must use the loopback fixture")
        if url.username or url.password or not url.port or config.get("provider_model") != "whynote-s1-synthetic":
            raise ValueError("S1 mock connection is invalid")
    elif mode == "cloud":
        if config.get("base_url") != "https://api.deepseek.com" or config.get("provider_model") != "deepseek-flash":
            raise ValueError("S1 provider is not approved")
        if any(
            not isinstance(config.get(k), str) or not config[k].strip()
            for k in ("outbound_approval_ref", "provider_data_terms_ref")
        ):
            raise ValueError("S1 outbound review is incomplete")
        if config.get("pricing_version") != "deepseek-flash-cny-2026-09-27":
            raise ValueError("S1 price review is incomplete")
    else:
        raise ValueError("S1 mode must be mock or cloud")
    return config


def text_message(message: dict, role: str) -> str:
    if not isinstance(message, dict) or message.get("role") != role:
        raise NotFoundError("S1 message is unavailable")
    text = message.get("content")
    if not isinstance(text, str) or not text.strip() or message.get("files"):
        raise NotFoundError("S1 requires nonempty text")
    return text


def generation_input(chat, message_id: str, store) -> tuple[str, str, list[dict]]:
    """Read only the saved current branch, never the client-provided message list."""
    messages = chat.chat.get("history", {}).get("messages", {})
    answer = messages.get(message_id)
    if not isinstance(answer, dict) or answer.get("role") != "assistant" or answer.get("model") != PIPE_ID:
        raise NotFoundError("S1 assistant target is unavailable")
    parent_id = str(uuid.UUID(answer.get("parentId", "")))
    prompt = text_message(messages.get(parent_id), "user")
    history = [{"role": "user", "content": prompt}]
    previous = messages[parent_id].get("parentId")
    seen = {parent_id, message_id}
    for _ in range(4):
        if previous is None:
            break
        if previous in seen:
            raise NotFoundError("S1 history is invalid")
        seen.add(previous)
        prior_answer = messages.get(previous)
        prior_text = text_message(prior_answer, "assistant")
        store.qualify(chat, {"chat_id": chat.id, "id": previous, "model": PIPE_ID, "messages": [prior_answer]})
        if prior_answer.get("model") != PIPE_ID or prior_answer.get("done") is not True or prior_answer.get("error"):
            raise NotFoundError("S1 history requires completed trial answers")
        prior_parent_id = prior_answer.get("parentId")
        if prior_parent_id in seen:
            raise NotFoundError("S1 history is invalid")
        seen.add(prior_parent_id)
        prior_parent = messages.get(prior_parent_id)
        prior_prompt = text_message(prior_parent, "user")
        history[0:0] = [{"role": "user", "content": prior_prompt}, {"role": "assistant", "content": prior_text}]
        previous = prior_parent.get("parentId")
    history.insert(0, {"role": "system", "content": SYSTEM})
    if sum(len(item["content"].encode("utf-8")) for item in history) > 8192:
        raise ValueError("S1 text limit exceeded")
    return parent_id, prompt, history


class TrialStore(EventStore):
    def __init__(self, config: dict, guard=None):
        research.validate_config(config)
        self.config = config
        self.guard = None
        super().__init__(config["db_path"])
        with self._transaction() as db:
            schema = """
                CREATE TABLE IF NOT EXISTS s1_instance (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS s1_chats (
                    chat_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, active INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS s1_generations (
                    attempt_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, parent_id TEXT NOT NULL, parent_hash TEXT NOT NULL,
                    answer_hash TEXT, version TEXT, status TEXT NOT NULL,
                    reserved_micro INTEGER NOT NULL, settled_micro INTEGER,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS s1_current (
                    chat_id TEXT NOT NULL, message_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    PRIMARY KEY (chat_id, message_id)
                );
            """
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)
            # Additive migration from the first S1 development schema. Old rows
            # stay unknown; do not infer past provider identity or token counts.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(s1_generations)")}
            for name, kind in (
                ("mode", "TEXT"),
                ("provider_model", "TEXT"),
                ("pricing_version", "TEXT"),
                ("prompt_tokens", "INTEGER"),
                ("completion_tokens", "INTEGER"),
                ("saved_at", "REAL"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE s1_generations ADD COLUMN {name} {kind}")
            instance = db.execute("SELECT id FROM s1_instance").fetchone()
            if instance is None:
                db.execute("INSERT INTO s1_instance VALUES (?)", (config["instance_id"],))
            elif instance["id"] != config["instance_id"]:
                raise ValueError("S1 database belongs to another instance")
            research.initialize(db)
        self.guard = guard

    @contextmanager
    def _transaction(self):
        with super()._transaction() as db:
            if self.guard is not None:
                self.guard(db)
            yield db

    def enroll(self, chat_id: str, user_id: str) -> None:
        if user_id != self.config["user_id"]:
            raise NotFoundError("S1 subject is unavailable")
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO s1_chats VALUES (?, ?, 1)", (chat_id, user_id))
            self._admitted(db, chat_id, user_id)

    @staticmethod
    def _admitted(db, chat_id, user_id):
        row = db.execute(
            "SELECT * FROM s1_chats WHERE chat_id=? AND user_id=? AND active=1", (chat_id, user_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("S1 chat is not admitted")

    def reserve(self, chat_id: str, user_id: str, message_id: str, parent_id: str, prompt: str) -> str:
        attempt = str(uuid.uuid4())
        with self._transaction() as db:
            self._admitted(db, chat_id, user_id)
            self._available(db)
            previous = db.execute(
                "SELECT attempt_id FROM s1_current WHERE chat_id=? AND message_id=?", (chat_id, message_id)
            ).fetchone()
            if previous:
                research.record_state(db, previous[0], "superseded", time.time())
            db.execute(
                "INSERT INTO s1_generations "
                "(attempt_id,chat_id,user_id,message_id,parent_id,parent_hash,answer_hash,version,status,"
                "reserved_micro,settled_micro,created_at,mode,provider_model,pricing_version) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'pending', ?, NULL, ?, ?, ?, ?)",
                (
                    attempt,
                    chat_id,
                    user_id,
                    message_id,
                    parent_id,
                    digest(self.config["version_key"].encode(), prompt),
                    RESERVATION_MICRO_CNY,
                    time.time(),
                    self.config.get("mode"),
                    self.config.get("provider_model"),
                    self.config.get("pricing_version"),
                ),
            )
            db.execute("INSERT OR REPLACE INTO s1_current VALUES (?, ?, ?)", (chat_id, message_id, attempt))
            research.capture_attempt(db, self.config, attempt)
        return attempt

    @staticmethod
    def _available(db):
        if db.execute("SELECT 1 FROM s1_generations WHERE status IN ('pending','awaiting_save')").fetchone():
            raise ConflictError("S1 generation is already in flight; interrupted runs require reconciliation")
        spent = db.execute(
            "SELECT COALESCE(SUM(COALESCE(settled_micro,reserved_micro)),0) FROM s1_generations"
        ).fetchone()[0]
        if spent + RESERVATION_MICRO_CNY > BUDGET_MICRO_CNY:
            raise ConflictError("S1 budget limit reached")

    def check_available(self):
        with self._transaction() as db:
            self._available(db)

    def finish(self, attempt: str, *, answer: str = "", complete: bool = False, usage: dict | None = None) -> None:
        settled = None
        prompt_tokens = completion_tokens = None
        if isinstance(usage, dict):
            prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if (
                type(prompt) is int
                and type(completion) is int
                and 0 <= prompt <= INPUT_TOKEN_BOUND
                and 0 <= completion <= 1024
            ):
                settled = prompt * 2 + completion * 8
                prompt_tokens, completion_tokens = prompt, completion
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM s1_generations WHERE attempt_id=? AND status='pending'", (attempt,)
            ).fetchone()
            if row is None:
                return
            key = self.config["version_key"].encode()
            answer_hash = digest(key, answer) if complete and answer.strip() else None
            version = "s1v1-" + digest(key, [attempt, row["parent_hash"], answer_hash]) if answer_hash else None
            db.execute(
                "UPDATE s1_generations SET status=?,answer_hash=?,version=?,settled_micro=?,"
                "prompt_tokens=?,completion_tokens=? WHERE attempt_id=?",
                (
                    "awaiting_save" if answer_hash else "incomplete",
                    answer_hash,
                    version,
                    settled,
                    prompt_tokens,
                    completion_tokens,
                    attempt,
                ),
            )
            research.record_state(db, attempt, "awaiting_save" if answer_hash else "incomplete", time.time())

    def receipt(self, db: sqlite3.Connection, chat_id: str, user_id: str, message_id: str):
        self._admitted(db, chat_id, user_id)
        row = db.execute(
            "SELECT g.* FROM s1_generations g JOIN s1_current c ON c.attempt_id=g.attempt_id "
            "WHERE c.chat_id=? AND c.message_id=? AND g.user_id=? AND g.status='completed' AND g.saved_at IS NOT NULL",
            (chat_id, message_id, user_id),
        ).fetchone()
        if (
            row is None
            or row["mode"] != self.config.get("mode")
            or row["provider_model"] != self.config.get("provider_model")
        ):
            raise NotFoundError("S1 response has no completed generation receipt")
        return row

    def confirm_saved(self, chat, message_id):
        """Only called by the host's successful final-persistence hook, never a route."""
        if chat is None or chat.user_id != self.config["user_id"]:
            return
        messages = chat.chat.get("history", {}).get("messages", {})
        answer = messages.get(message_id, {})
        parent = messages.get(answer.get("parentId"), {})
        with self._transaction() as db:
            row = db.execute(
                "SELECT g.* FROM s1_generations g JOIN s1_current c ON c.attempt_id=g.attempt_id "
                "JOIN s1_chats a ON a.chat_id=g.chat_id AND a.active=1 "
                "WHERE c.chat_id=? AND c.message_id=? AND g.user_id=? AND g.status='awaiting_save'",
                (chat.id, message_id, chat.user_id),
            ).fetchone()
            key = self.config["version_key"].encode()
            if (
                row is not None
                and answer.get("done") is True
                and not answer.get("error")
                and answer.get("role") == "assistant"
                and answer.get("model") == PIPE_ID
                and parent.get("role") == "user"
                and row["parent_id"] == answer.get("parentId")
                and row["parent_hash"] == digest(key, parent.get("content"))
                and row["answer_hash"] == digest(key, answer.get("content"))
            ):
                saved_at = time.time()
                db.execute(
                    "UPDATE s1_generations SET status='completed',saved_at=? WHERE attempt_id=?",
                    (saved_at, row["attempt_id"]),
                )
                research.record_answer(db, row["attempt_id"], row["version"], saved_at)

    def qualify(self, chat, body: dict) -> dict[str, str]:
        chat_id, message_id = str(uuid.UUID(body["chat_id"])), str(uuid.UUID(body["id"]))
        messages = chat.chat.get("history", {}).get("messages", {})
        answer = messages.get(message_id)
        answer_text = text_message(answer, "assistant")
        parent = messages.get(answer.get("parentId"))
        parent_text = text_message(parent, "user")
        with self._transaction() as db:
            row = self.receipt(db, chat_id, self.config["user_id"], message_id)
            key = self.config["version_key"].encode()
            if (
                row["parent_id"] != answer.get("parentId")
                or row["parent_hash"] != digest(key, parent_text)
                or row["answer_hash"] != digest(key, answer_text)
                or answer.get("model") != PIPE_ID
                or body.get("model") != PIPE_ID
                or answer.get("error")
                or answer.get("done") is not True
            ):
                raise NotFoundError("S1 response version is unavailable")
            rendered = next(
                (m for m in body.get("messages", []) if isinstance(m, dict) and m.get("id") == message_id), None
            )
            if not rendered or rendered.get("role") != "assistant" or rendered.get("content") != answer_text:
                raise NotFoundError("S1 displayed version is unavailable")
            return {
                "object_type": "openwebui_assistant_message",
                "object_id": f"{chat_id}/{message_id}",
                "object_version": row["version"],
            }

    def invalidate(self, chat_id: str, *, revoke=False):
        with self._transaction() as db:
            # Record all tracked attempts, including previously superseded ones.
            for row in db.execute("SELECT attempt_id FROM s1_generations WHERE chat_id=?", (chat_id,)).fetchall():
                research.record_state(db, row[0], "revoked" if revoke else "invalidated", time.time())
            db.execute("DELETE FROM s1_current WHERE chat_id=?", (chat_id,))
            if revoke:
                db.execute("UPDATE s1_chats SET active=0 WHERE chat_id=?", (chat_id,))

    def current_action(self, principal: Principal, target: dict):
        with self._transaction() as db:
            rows = db.execute(
                "SELECT event_id FROM actions WHERE tenant_ref=? AND actor_ref=? AND target_ref=? ORDER BY rowid DESC",
                (
                    principal.tenant_ref,
                    principal.actor_ref,
                    json.dumps(target, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            ).fetchall()
            if rows:
                return self._projection(db, rows[0]["event_id"])
        raise NotFoundError("S1 active action is unavailable")

"""
orchestrator/memory.py
──────────────────────
Persistent memory system using SQLite.

WHY SQLITE?
  - Zero setup — no database server needed
  - File-based — survives process restarts
  - Python has built-in sqlite3 support — no extra install
  - Industry standard for local persistent storage in agents

TABLES:
  tasks     — Every task ever processed (task_id, agent, action, status, etc.)
  summaries — YouTube summaries keyed by video_id (avoids re-processing)
  sessions  — Conversation context within a session
"""

import sqlite3
import json
import uuid
from datetime import datetime
from typing import Optional
from utils.logger import Logger


log = Logger("Memory")


class Memory:
    """
    Handles all read/write to the SQLite database.

    The Orchestrator holds one Memory instance and passes it to agents
    so they can cache results and avoid duplicate work.
    """

    def __init__(self, db_path: str = "orchestrator_memory.db"):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row   # Rows behave like dicts
        self._initialize_schema()
        log.success(f"Memory initialized at '{db_path}'")

    def _initialize_schema(self):
        """Creates tables if they don't already exist."""
        cursor = self._conn.cursor()

        # Task history — every task routed through the orchestrator
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id      TEXT PRIMARY KEY,
                agent        TEXT NOT NULL,
                action       TEXT NOT NULL,
                payload      TEXT NOT NULL,     -- JSON blob
                status       TEXT NOT NULL,
                result       TEXT,              -- JSON blob (nullable)
                error        TEXT,
                created_at   TEXT NOT NULL,
                completed_at TEXT
            )
        """)

        # Summary cache — avoids re-calling YouTube + LLM for same video
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                video_id     TEXT PRIMARY KEY,
                video_url    TEXT NOT NULL,
                summary_json TEXT NOT NULL,     -- Full TechnicalSummary as JSON
                github_url   TEXT,              -- GitHub URL if pushed
                created_at   TEXT NOT NULL
            )
        """)

        # Session log — stores conversation history for context
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                role         TEXT NOT NULL,     -- "user" or "assistant"
                content      TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
        """)

        # App state — key/value store for multi-step conversation flows
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_state (
                key          TEXT PRIMARY KEY,
                value        TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
        """)

        # JIRA ticket cache — avoid re-fetching tickets
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jira_tickets (
                ticket_key   TEXT PRIMARY KEY,
                summary      TEXT NOT NULL,
                issue_type   TEXT,
                priority     TEXT,
                status       TEXT,
                description  TEXT,
                url          TEXT,
                pr_url       TEXT,
                created_at   TEXT NOT NULL
            )
        """)

        self._conn.commit()

    # ─────────────────────────────────────────────────────────────────────────
    # TASK OPERATIONS
    # ─────────────────────────────────────────────────────────────────────────

    def create_task(self, agent: str, action: str, payload: dict) -> str:
        """Inserts a new task and returns its task_id."""
        task_id = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO tasks (task_id, agent, action, payload, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (task_id, agent, action, json.dumps(payload), datetime.now().isoformat())
        )
        self._conn.commit()
        log.info(f"Task created → {task_id[:8]}... [{agent}:{action}]")
        return task_id

    def update_task_status(self, task_id: str, status: str,
                           result: Optional[dict] = None,
                           error: Optional[str] = None):
        """Updates task status after agent completes or fails."""
        self._conn.execute(
            """UPDATE tasks
               SET status=?, result=?, error=?, completed_at=?
               WHERE task_id=?""",
            (
                status,
                json.dumps(result) if result else None,
                error,
                datetime.now().isoformat() if status in ("completed", "failed") else None,
                task_id
            )
        )
        self._conn.commit()

    def get_task_history(self, limit: int = 10) -> list[dict]:
        """Returns the N most recent tasks for the 'history' command."""
        rows = self._conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # SUMMARY CACHE
    # ─────────────────────────────────────────────────────────────────────────

    def cache_summary(self, video_id: str, video_url: str, summary: dict):
        """Saves a summary. If video_id exists, updates it."""
        self._conn.execute(
            """INSERT INTO summaries (video_id, video_url, summary_json, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET summary_json=excluded.summary_json""",
            (video_id, video_url, json.dumps(summary), datetime.now().isoformat())
        )
        self._conn.commit()
        log.info(f"Summary cached for video_id={video_id}")

    def get_cached_summary(self, video_id: str) -> Optional[dict]:
        """Returns cached summary if it exists, else None."""
        row = self._conn.execute(
            "SELECT * FROM summaries WHERE video_id=?", (video_id,)
        ).fetchone()
        if row:
            log.info(f"Cache HIT for video_id={video_id} — skipping API calls")
            return json.loads(row["summary_json"])
        return None

    def update_summary_github_url(self, video_id: str, github_url: str):
        """Records the GitHub URL after a summary is pushed."""
        self._conn.execute(
            "UPDATE summaries SET github_url=? WHERE video_id=?",
            (github_url, video_id)
        )
        self._conn.commit()

    def list_summaries(self) -> list[dict]:
        """Returns all cached summaries (for the 'list' command)."""
        rows = self._conn.execute(
            "SELECT video_id, video_url, github_url, created_at FROM summaries ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # SESSION / CONVERSATION LOG
    # ─────────────────────────────────────────────────────────────────────────

    def log_message(self, role: str, content: str):
        """Appends a message to the conversation history."""
        self._conn.execute(
            "INSERT INTO session_log (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, datetime.now().isoformat())
        )
        self._conn.commit()

    def get_recent_messages(self, limit: int = 10) -> list[dict]:
        """Returns the last N messages — passed to Claude as conversation context."""
        rows = self._conn.execute(
            "SELECT role, content FROM session_log ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # ─────────────────────────────────────────────────────────────────────────
    # APP STATE — Key/value store for multi-step flows
    # ─────────────────────────────────────────────────────────────────────────

    def set_state(self, key: str, value: dict):
        """Saves a state value (used for multi-step conversation tracking)."""
        self._conn.execute(
            """INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, json.dumps(value), datetime.now().isoformat())
        )
        self._conn.commit()

    def get_state(self, key: str) -> Optional[dict]:
        """Returns a state value, or None if not set."""
        row = self._conn.execute(
            "SELECT value FROM app_state WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row["value"]) if row else None

    def clear_state(self, key: str):
        """Deletes a state entry."""
        self._conn.execute("DELETE FROM app_state WHERE key=?", (key,))
        self._conn.commit()

    # ─────────────────────────────────────────────────────────────────────────
    # JIRA TICKET CACHE
    # ─────────────────────────────────────────────────────────────────────────

    def cache_jira_ticket(self, ticket: dict):
        """Saves or updates a JIRA ticket in local cache."""
        self._conn.execute(
            """INSERT INTO jira_tickets
               (ticket_key, summary, issue_type, priority, status, description, url, pr_url, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticket_key) DO UPDATE SET
                 summary=excluded.summary, status=excluded.status,
                 pr_url=excluded.pr_url, description=excluded.description""",
            (
                ticket.get("key"), ticket.get("summary"), ticket.get("issue_type"),
                ticket.get("priority"), ticket.get("status"), ticket.get("description"),
                ticket.get("url"), ticket.get("pr_url"), datetime.now().isoformat()
            )
        )
        self._conn.commit()

    def get_jira_ticket(self, ticket_key: str) -> Optional[dict]:
        """Returns a cached JIRA ticket."""
        row = self._conn.execute(
            "SELECT * FROM jira_tickets WHERE ticket_key=?", (ticket_key,)
        ).fetchone()
        return dict(row) if row else None

    def update_jira_pr_url(self, ticket_key: str, pr_url: str):
        """Stores the PR URL against a JIRA ticket."""
        self._conn.execute(
            "UPDATE jira_tickets SET pr_url=? WHERE ticket_key=?",
            (pr_url, ticket_key)
        )
        self._conn.commit()

    def close(self):
        """Closes the database connection cleanly."""
        self._conn.close()
        log.info("Memory connection closed")

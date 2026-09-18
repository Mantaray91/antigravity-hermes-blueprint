import sqlite3
import json
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from datetime import datetime, timezone
from contextlib import contextmanager


def strip_system_metadata(text: str) -> str:
    if not text:
        return ""
    # Strip ADDITIONAL_METADATA with or without attributes
    text = re.sub(r'<ADDITIONAL_METADATA[^>]*>.*?</ADDITIONAL_METADATA>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip USER_SETTINGS_CHANGE
    text = re.sub(r'<USER_SETTINGS_CHANGE[^>]*>.*?</USER_SETTINGS_CHANGE>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip CONTEXT_SUMMARY
    text = re.sub(r'<CONTEXT_SUMMARY[^>]*>.*?</CONTEXT_SUMMARY>', '', text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def load_canonical_transcript_steps(path: Path) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    active_steps: Dict[int, Dict[str, Any]] = {}
    fallback_steps: List[Dict[str, Any]] = []
    has_step_indices = False

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                step = json.loads(line)
            except Exception:
                continue
            if not isinstance(step, dict):
                continue

            step_idx = step.get("step_index")
            step_type = step.get("type", "")
            if step_idx is not None and isinstance(step_idx, int):
                has_step_indices = True
                # Rewind resolution: only prune discarded branch when a new turn begins (USER_INPUT)
                # with a step_idx that jumps backward. Intra-turn out-of-order log flushes (e.g. tool output
                # written before planner response) must not trigger rewind pruning.
                if step_type in ("USER_INPUT", "user") and active_steps and step_idx <= max(active_steps.keys()):
                    for k in [k for k in list(active_steps.keys()) if k >= step_idx]:
                        del active_steps[k]
                active_steps[step_idx] = step
            else:
                fallback_steps.append(step)

    max_idx = max((s.get("step_index", 0) for s in active_steps.values()), default=-1)
    for unindexed in fallback_steps:
        max_idx += 1
        unindexed["step_index"] = max_idx
        active_steps[max_idx] = unindexed

    return [active_steps[k] for k in sorted(active_steps.keys())]



class SessionDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=2000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        self._init_db()

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    workspace TEXT,
                    started_at TEXT,
                    title TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    step_index INTEGER,
                    role TEXT,
                    content TEXT,
                    created_at TEXT,
                    UNIQUE(session_id, step_index)
                );
            """)
            cur.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    content,
                    session_id UNINDEXED,
                    role UNINDEXED,
                    content_rowid UNINDEXED
                );
            """)

    def ingest_transcript(
        self,
        transcript_or_id: Any = None,
        session_id_or_steps: Any = None,
        workspace: str = "",
        deadline: Optional[float] = None,
        *,
        session_id: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        transcript_path: Optional[Union[str, Path]] = None,
    ) -> int:
        actual_session_id = session_id or ""
        resolved_steps: List[Dict[str, Any]] = steps if steps is not None else []

        if not resolved_steps:
            if isinstance(session_id_or_steps, list):
                # Pattern: ingest_transcript(session_id, steps, ...)
                if not actual_session_id and transcript_or_id is not None:
                    actual_session_id = str(transcript_or_id)
                resolved_steps = session_id_or_steps
            elif isinstance(transcript_or_id, list):
                # Pattern: ingest_transcript(steps, session_id, ...)
                resolved_steps = transcript_or_id
                if not actual_session_id and session_id_or_steps is not None:
                    actual_session_id = str(session_id_or_steps)
            else:
                # Pattern: ingest_transcript(transcript_path, session_id, ...)
                path_candidate = transcript_path or transcript_or_id
                if path_candidate:
                    path = Path(path_candidate)
                    if path.exists():
                        resolved_steps = load_canonical_transcript_steps(path)
                    else:
                        return 0
                if not actual_session_id and session_id_or_steps is not None:
                    actual_session_id = str(session_id_or_steps)

        if not actual_session_id:
            actual_session_id = "unknown"

        self._init_db()
        inserted = 0
        now = datetime.now(timezone.utc).isoformat()

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO sessions (session_id, workspace, started_at) VALUES (?, ?, ?)",
                (actual_session_id, workspace, now)
            )
            EXCLUDED_STEP_TYPES = {"EPHEMERAL_MESSAGE", "CHECKPOINT", "ERROR_MESSAGE"}
            for idx, step in enumerate(resolved_steps):
                if deadline and time.monotonic() > deadline:
                    break
                step_type = step.get("type", "")
                if step_type in EXCLUDED_STEP_TYPES:
                    continue
                content = step.get("content", "")
                if not content or not isinstance(content, str):
                    continue
                content = strip_system_metadata(content)
                if not content:
                    continue
                role = "user" if step_type in ("USER_INPUT", "user") else ("assistant" if step_type in ("PLANNER_RESPONSE", "assistant") else "tool")
                step_idx = step.get("step_index", idx)
                step_time = step.get("created_at") or now
                try:
                    cur.execute(
                        "INSERT INTO messages (session_id, step_index, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                        (actual_session_id, step_idx, role, content, step_time)
                    )
                    row_id = cur.lastrowid
                    cur.execute(
                        "INSERT INTO messages_fts (content, session_id, role, content_rowid) VALUES (?, ?, ?, ?)",
                        (content, actual_session_id, role, row_id)
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    continue
        return inserted

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            limit = max(1, min(100, int(limit)))
        except (ValueError, TypeError):
            limit = 10

        tokens = query.strip().split()
        safe_tokens = []
        for t in tokens:
            cleaned = "".join(c for c in t if c.isalnum() or c in "._-/")
            cleaned = cleaned.strip(".-_/")
            if not cleaned:
                continue
            if any(c in "._-/" for c in cleaned):
                escaped = cleaned.replace('"', '""')
                safe_tokens.append(f'"{escaped}"')
            else:
                safe_tokens.append(cleaned)

        clean_query = " ".join(safe_tokens)
        if not clean_query:
            return []

        self.init_db()
        results = []
        with self._get_conn() as conn:
            cur = conn.cursor()
            sql = """
                SELECT f.session_id, f.role, snippet(messages_fts, 0, '<b>', '</b>', '...', 20) as snippet,
                       m.id as message_id, m.content, m.created_at
                FROM messages_fts f
                JOIN messages m ON f.content_rowid = m.id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            try:
                for row in cur.execute(sql, (clean_query, limit)):
                    truncated = False
                    content_str = row["content"]
                    total_len = len(content_str)
                    if total_len > 1200:
                        content_str = content_str[:1200]
                        truncated = True
                    results.append({
                        "session_id": row["session_id"],
                        "message_id": row["message_id"],
                        "role": row["role"],
                        "snippet": row["snippet"],
                        "content": content_str,
                        "content_truncated": truncated,
                        "total_length": total_len,
                        "created_at": row["created_at"]
                    })
            except sqlite3.OperationalError:
                return []
        return results

    def search_sessions(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        return self.search(query, limit=limit)

    def get_message(self, message_id: int, offset: int = 0, limit: int = 4000) -> Dict[str, Any]:
        self.init_db()
        offset = max(0, int(offset))
        limit = max(1, int(limit))
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, session_id, role, content, created_at FROM messages WHERE id = ?", (message_id,))
            row = cur.fetchone()
            if not row:
                return {"success": False, "error": f"Message {message_id} not found"}
            full_content = row["content"] or ""
            chunk = full_content[offset:offset + limit]
            return {
                "success": True,
                "message_id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": chunk,
                "offset": offset,
                "limit": limit,
                "total_length": len(full_content),
                "has_more": (offset + limit) < len(full_content),
                "created_at": row["created_at"]
            }


    def read_session(self, session_id: str) -> List[Dict[str, Any]]:
        self.init_db()
        results = []
        with self._get_conn() as conn:
            cur = conn.cursor()
            sql = """
                SELECT session_id, step_index, role, content, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY step_index ASC
            """
            for row in cur.execute(sql, (session_id,)):
                results.append({
                    "session_id": row["session_id"],
                    "step_index": row["step_index"],
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"]
                })
        return results

import pytest
import json
from pathlib import Path
from hermes_engine.session_db import SessionDB, load_canonical_transcript_steps

@pytest.fixture
def session_db(tmp_path):
    db_file = tmp_path / "test_state.db"
    db = SessionDB(db_path=db_file)
    db.init_db()
    return db

def test_ingest_and_search_fts5(session_db, tmp_path):
    transcript_file = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"type": "USER_INPUT", "content": "How do we configure IBKR gateway?"}),
        json.dumps({"type": "PLANNER_RESPONSE", "content": "Use ibkr-manager-4.0 toolset with port 7497."})
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    ingested_count = session_db.ingest_transcript(transcript_file, session_id="sess-001", workspace="/tmp/test")
    assert ingested_count == 2

    # Search via FTS5
    results = session_db.search("IBKR gateway")
    assert len(results) > 0
    assert results[0]["session_id"] == "sess-001"
    assert "configure IBKR gateway" in results[0]["content"]

def test_ingest_idempotency(session_db, tmp_path):
    transcript_file = tmp_path / "transcript.jsonl"
    lines = [json.dumps({"type": "USER_INPUT", "content": "Unique message for test"})]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    c1 = session_db.ingest_transcript(transcript_file, "sess-002", "/tmp")
    c2 = session_db.ingest_transcript(transcript_file, "sess-002", "/tmp")
    assert c1 == 1
    assert c2 == 0  # Re-ingesting existing transcript rows does not duplicate

def test_read_session(session_db, tmp_path):
    transcript_file = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"type": "USER_INPUT", "content": "First input"}),
        json.dumps({"type": "PLANNER_RESPONSE", "content": "First response"}),
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    session_db.ingest_transcript(transcript_file, "sess-003", "/tmp")
    messages = session_db.read_session("sess-003")
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "First input"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "First response"

def test_search_edge_cases(session_db, tmp_path):
    assert session_db.search("") == []
    assert session_db.search("   ") == []
    assert session_db.search("!@#$%^&*()") == []
    assert session_db.ingest_transcript(tmp_path / "nonexistent.jsonl", "sess-none", "/tmp") == 0

def test_search_technical_characters(session_db, tmp_path):
    transcript_file = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"type": "USER_INPUT", "content": "Check USER.md and state.db in mcp/hermes_engine"}),
        json.dumps({"type": "PLANNER_RESPONSE", "content": "Configured self-evolution pipeline."})
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")
    session_db.ingest_transcript(transcript_file, "sess-tech", "/tmp")

    # Search with dots, slashes, hyphens
    res1 = session_db.search("USER.md")
    assert len(res1) > 0
    assert "USER.md" in res1[0]["content"]

    res2 = session_db.search("mcp/hermes_engine")
    assert len(res2) > 0
    assert "mcp/hermes_engine" in res2[0]["content"]

    res3 = session_db.search("self-evolution")
    assert len(res3) > 0
    assert "self-evolution" in res3[0]["content"]


def test_connection_lifecycle_and_explicit_close(tmp_path, monkeypatch):
    import sqlite3
    db_file = tmp_path / "lifecycle.db"
    db = SessionDB(db_path=db_file)

    opened_connections = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened_connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)

    # 1. init_db
    db.init_db()
    assert len(opened_connections) >= 1
    for conn in opened_connections:
        with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
            conn.execute("SELECT 1")

    # 2. search
    prev_count = len(opened_connections)
    db.search("test")
    assert len(opened_connections) > prev_count
    for conn in opened_connections:
        with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
            conn.execute("SELECT 1")

    # 3. read_session
    prev_count = len(opened_connections)
    db.read_session("test-sess")
    assert len(opened_connections) > prev_count
    for conn in opened_connections:
        with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
            conn.execute("SELECT 1")

    # 4. ingest_transcript
    prev_count = len(opened_connections)
    t_file = tmp_path / "t.jsonl"
    t_file.write_text(json.dumps({"type": "USER_INPUT", "content": "hi"}), encoding="utf-8")
    db.ingest_transcript(t_file, "sess-1", "/tmp")
    assert len(opened_connections) > prev_count
    for conn in opened_connections:
        with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
            conn.execute("SELECT 1")


def test_ingest_skips_ephemeral_messages(session_db, tmp_path):
    transcript_file = tmp_path / "transcript_with_ephemeral.jsonl"
    lines = [
        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "What is my configuration?"}),
        json.dumps({"step_index": 1, "type": "EPHEMERAL_MESSAGE", "content": "[PERSISTENT MEMORY SNAPSHOT]\n=== USER PROFILE ===\n- User: test_user"}),
        json.dumps({"step_index": 2, "type": "PLANNER_RESPONSE", "content": "Your configuration is standard."})
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    ingested = session_db.ingest_transcript(transcript_file, "sess-eph", "/tmp")
    # Only USER_INPUT and PLANNER_RESPONSE should be ingested, EPHEMERAL_MESSAGE must be skipped
    assert ingested == 2

    # Searching for snapshot text must yield 0 results
    results = session_db.search("PERSISTENT MEMORY SNAPSHOT")
    assert len(results) == 0

    # Searching for user message works
    user_results = session_db.search("configuration")
    assert len(user_results) > 0


def test_ingest_handles_rewound_trajectory(session_db, tmp_path):
    transcript_file = tmp_path / "transcript_rewound.jsonl"
    # Simulates:
    # Step 0: User asks A
    # Step 1: Assistant replies A
    # Step 2: User asks B (to be aborted)
    # Step 3: Assistant starts B
    # User rewinds to step 2!
    # Step 2: User asks C (final canonical)
    # Step 3: Assistant replies C (final canonical)
    lines = [
        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "Initial prompt A"}),
        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "content": "Initial reply A"}),
        json.dumps({"step_index": 2, "type": "USER_INPUT", "content": "Aborted branch prompt B"}),
        json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "content": "Aborted branch reply B"}),
        json.dumps({"step_index": 2, "type": "USER_INPUT", "content": "Rewound prompt C"}),
        json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "content": "Rewound reply C"}),
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    session_db.ingest_transcript(transcript_file, "sess-rewound", "/tmp")

    # Messages in database must only contain steps 0, 1, and the final steps 2, 3 (prompt C, reply C)
    messages = session_db.read_session("sess-rewound")
    assert len(messages) == 4
    contents = [m["content"] for m in messages]
    assert "Initial prompt A" in contents
    assert "Initial reply A" in contents
    assert "Rewound prompt C" in contents
    assert "Rewound reply C" in contents
    assert "Aborted branch prompt B" not in contents
    assert "Aborted branch reply B" not in contents


def test_load_canonical_preserves_intra_turn_out_of_order_steps(tmp_path):
    transcript_file = tmp_path / "out_of_order.jsonl"
    lines = [
        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "Run command X"}),
        json.dumps({"step_index": 2, "type": "GENERIC", "content": "Tool stdout result"}),
        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "content": "Calling tool..."}),
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    steps = load_canonical_transcript_steps(transcript_file)
    assert len(steps) == 3
    indices = [s["step_index"] for s in steps]
    assert indices == [0, 1, 2]
    contents = [s["content"] for s in steps]
    assert "Tool stdout result" in contents
    assert "Calling tool..." in contents


def test_ingest_skips_internal_metadata_steps(session_db, tmp_path):
    transcript_file = tmp_path / "metadata_steps.jsonl"
    lines = [
        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "Valid user prompt"}),
        json.dumps({"step_index": 1, "type": "CHECKPOINT", "content": "# Resuming from a compaction\nContext summary boilerplate..."}),
        json.dumps({"step_index": 2, "type": "ERROR_MESSAGE", "content": "Internal harness connection reset"}),
        json.dumps({"step_index": 3, "type": "EPHEMERAL_MESSAGE", "content": "[PERSISTENT MEMORY SNAPSHOT]"}),
        json.dumps({"step_index": 4, "type": "PLANNER_RESPONSE", "content": "Valid assistant response"})
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    ingested = session_db.ingest_transcript(transcript_file, "sess-metadata", "/tmp")
    assert ingested == 2

    messages = session_db.read_session("sess-metadata")
    assert len(messages) == 2
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant"]

    # FTS5 must not contain metadata content
    assert len(session_db.search("compaction")) == 0
    assert len(session_db.search("boilerplate")) == 0
    assert len(session_db.search("harness")) == 0


def test_ingest_strips_additional_metadata_and_preserves_step_timestamp(session_db, tmp_path):
    transcript_file = tmp_path / "transcript.jsonl"
    step_time = "2026-09-16T05:00:00Z"
    transcript_file.write_text(json.dumps({
        "step_index": 0,
        "type": "USER_INPUT",
        "role": "user",
        "created_at": step_time,
        "content": "<USER_REQUEST>test search</USER_REQUEST><ADDITIONAL_METADATA>The current local time is: 2026-09-16T07:26:17+07:00.</ADDITIONAL_METADATA>"
    }) + "\n")

    session_db.ingest_transcript(transcript_file, "sess_1", "/workspace")
    results = session_db.search("local time")
    assert len(results) == 0  # Cleaned, no false positive hit!

    # Note: <USER_REQUEST> envelope is intentionally preserved in FTS5 archive
    # to retain full user turn context; only runtime telemetry noise is stripped.
    res_test = session_db.search("test search")
    assert len(res_test) == 1
    assert res_test[0]["created_at"] == step_time
    assert res_test[0]["message_id"] is not None
    assert res_test[0]["content"] == "<USER_REQUEST>test search</USER_REQUEST>"


def test_session_get_message_pagination(session_db, tmp_path):
    transcript_file = tmp_path / "long_transcript.jsonl"
    long_content = "X" * 3000
    transcript_file.write_text(json.dumps({
        "step_index": 0,
        "type": "USER_INPUT",
        "role": "user",
        "content": f"querykey {long_content}"
    }) + "\n")
    session_db.ingest_transcript(transcript_file, "sess_2", "/workspace")

    search_res = session_db.search("querykey")
    assert search_res[0]["content_truncated"] is True
    assert len(search_res[0]["content"]) == 1200
    msg_id = search_res[0]["message_id"]

    # Fetch full via get_message
    detail = session_db.get_message(msg_id, offset=0, limit=4000)
    assert detail["success"] is True
    assert detail["total_length"] == len(f"querykey {long_content}")
    assert detail["content"] == f"querykey {long_content}"

    # Test pagination chunking
    chunk = session_db.get_message(msg_id, offset=0, limit=50)
    assert chunk["success"] is True
    assert len(chunk["content"]) == 50
    assert chunk["offset"] == 0
    assert chunk["limit"] == 50
    assert chunk["has_more"] is True

    # Test not found
    not_found = session_db.get_message(999999)
    assert not_found["success"] is False
    assert "not found" in not_found["error"].lower()


def test_wal_mode_and_connection_pragmas(tmp_path):
    db_file = tmp_path / "pragmas.db"
    db = SessionDB(db_file)
    db.init_db()

    with db._get_conn() as conn:
        cur = conn.cursor()
        # Verify WAL mode was set by init_db
        journal_mode = cur.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"

        # Verify busy_timeout set on connection
        busy_timeout = cur.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert busy_timeout == 2000

        # Verify synchronous set to NORMAL (1)
        synchronous = cur.execute("PRAGMA synchronous;").fetchone()[0]
        assert synchronous == 1


def test_ingest_transcript_cooperative_deadline(session_db, tmp_path):
    import time
    transcript_file = tmp_path / "transcript_deadline.jsonl"
    lines = [
        json.dumps({"step_index": i, "type": "USER_INPUT", "content": f"Message {i}"})
        for i in range(20)
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    # If deadline is already in the past, loop must immediately break without inserting
    inserted = session_db.ingest_transcript(
        transcript_file,
        session_id="sess-deadline",
        workspace="/tmp",
        deadline=time.monotonic() - 10.0
    )
    assert inserted == 0


def test_strip_system_metadata_handles_attributes_and_extra_tags():
    from hermes_engine.session_db import strip_system_metadata
    raw = (
        "<ADDITIONAL_METADATA source=\"client\" priority=\"high\">\n"
        "Time: 2026-09-17\n"
        "</ADDITIONAL_METADATA>\n"
        "<USER_SETTINGS_CHANGE>\nDark mode on\n</USER_SETTINGS_CHANGE>\n"
        "<CONTEXT_SUMMARY>\nOld turns summary\n</CONTEXT_SUMMARY>\n"
        "User question here."
    )
    cleaned = strip_system_metadata(raw)
    assert "User question here." == cleaned.strip()
    assert "ADDITIONAL_METADATA" not in cleaned
    assert "USER_SETTINGS_CHANGE" not in cleaned
    assert "CONTEXT_SUMMARY" not in cleaned


def test_get_message_defends_negative_bounds(session_db, tmp_path):
    session_id = "test-neg-bounds"
    steps = [{"step_index": 0, "type": "USER_INPUT", "content": "Sample content for testing bounds"}]
    session_db.ingest_transcript(session_id, steps)
    
    # Retrieve message_id from search
    results = session_db.search_sessions("Sample")
    assert len(results) > 0
    msg_id = results[0]["message_id"]
    
    # Negative offset clamped to 0, non-positive limit clamped to >= 1
    msg = session_db.get_message(msg_id, offset=-10, limit=-5)
    assert msg["success"] is True
    assert msg["offset"] == 0
    assert msg["limit"] == 1
    assert msg["content"] == "S"  # clamped to offset=0, limit=1


def test_load_canonical_transcript_steps_assigns_synthetic_indices(tmp_path):
    transcript_file = tmp_path / "mixed_indices.jsonl"
    lines = [
        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "Indexed step 0"}),
        json.dumps({"type": "PLANNER_RESPONSE", "content": "Unindexed step A"}),
        json.dumps({"type": "PLANNER_RESPONSE", "content": "Unindexed step B"}),
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")
    steps = load_canonical_transcript_steps(transcript_file)
    assert len(steps) == 3
    assert steps[0]["step_index"] == 0
    assert steps[1]["step_index"] == 1
    assert steps[2]["step_index"] == 2


def test_search_defends_limit_bounds_and_invalid_types(session_db):
    steps = [{"step_index": i, "type": "USER_INPUT", "content": f"commonword message {i}"} for i in range(15)]
    session_db.ingest_transcript("sess_bounds", steps)

    # Invalid non-integer limit should default gracefully (e.g. 10) instead of throwing IntegrityError
    res_str_limit = session_db.search("commonword", limit="abc")
    assert len(res_str_limit) == 10

    # Negative limit should be clamped to at least 1 instead of -1 (unlimited)
    res_neg_limit = session_db.search("commonword", limit=-1)
    assert len(res_neg_limit) == 1

    # Zero limit clamped to 1
    res_zero = session_db.search("commonword", limit=0)
    assert len(res_zero) == 1

    # Excessively large limit capped to 100
    res_large = session_db.search("commonword", limit=500)
    assert len(res_large) <= 100


def test_ingest_transcript_keyword_arguments(session_db):
    steps = [{"step_index": 0, "type": "USER_INPUT", "content": "Keyword argument ingestion test."}]
    # Test explicit keyword arguments: session_id and steps
    inserted = session_db.ingest_transcript(session_id="kw-sess-1", steps=steps, workspace="/test/workspace")
    assert inserted == 1

    hits = session_db.search("Keyword argument ingestion")
    assert len(hits) == 1
    assert hits[0]["session_id"] == "kw-sess-1"


def test_ingest_transcript_flexible_signatures(session_db, tmp_path):
    # Test positional pattern: ingest_transcript(session_id, steps)
    steps = [{"step_index": 0, "type": "USER_INPUT", "content": "Positional steps test"}]
    ins1 = session_db.ingest_transcript("pos-sess-1", steps)
    assert ins1 == 1

    # Test positional pattern: ingest_transcript(file_path, session_id)
    tfile = tmp_path / "test_flex.jsonl"
    tfile.write_text(json.dumps({"type": "USER_INPUT", "content": "Path positional test"}), encoding="utf-8")
    ins2 = session_db.ingest_transcript(tfile, "path-sess-1")
    assert ins2 == 1


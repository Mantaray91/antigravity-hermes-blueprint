import pytest
from pathlib import Path
from hermes_engine.memory import MemoryStore
from hermes_engine.skills import SkillManager
from hermes_engine.session_db import SessionDB

def test_full_evolution_lifecycle(tmp_path):
    # 1. Setup paths
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    skills_dir = tmp_path / "skills"
    usage_file = skills_dir / ".usage.json"
    db_file = tmp_path / "state.db"

    mem_store = MemoryStore(user_file, mem_file)
    skill_mgr = SkillManager(skills_dir, usage_file)
    session_db = SessionDB(db_file)

    # 2. Ingest Memory
    mem_store.add("user", "Prefers high technical terseness.")
    mem_store.add("memory", "Project uses FastMCP.")
    assert "Prefers high technical terseness" in mem_store.render_snapshot()

    # 3. Create Agent-Created Skill
    skill_content = "---\nname: auto-deploy\ndescription: Use when deploying service to staging.\n---\n# Deploy\n1. Run deploy"
    res_skill = skill_mgr.create("auto-deploy", skill_content, origin="background_review")
    assert res_skill["success"] is True
    assert skill_mgr.is_agent_created("auto-deploy") is True

    # 4. Ingest Transcript to SQLite FTS5
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type": "USER_INPUT", "content": "Deploy failed with port clash"}\n{"type": "PLANNER_RESPONSE", "content": "Kill stale process on 8080"}\n', encoding="utf-8")
    session_db.ingest_transcript(transcript, "session-101", str(tmp_path))

    # 5. Verify Search
    search_hits = session_db.search("port clash")
    assert len(search_hits) > 0
    assert "Deploy failed with port clash" in search_hits[0]["content"]

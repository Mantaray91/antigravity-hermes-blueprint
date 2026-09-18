import pytest
import ast
import json
from pathlib import Path
from hermes_engine import server

@pytest.fixture
def mock_server_env(tmp_path, monkeypatch):
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    skills_dir = tmp_path / "skills"
    usage_file = skills_dir / ".usage.json"
    db_file = tmp_path / "state.db"

    # Reconfigure server modules with tmp_path
    from hermes_engine.memory import MemoryStore
    from hermes_engine.skills import SkillManager
    from hermes_engine.session_db import SessionDB

    mem_store = MemoryStore(user_file, mem_file, user_limit=500, memory_limit=500)
    skill_mgr = SkillManager(skills_dir, usage_file)
    sess_db = SessionDB(db_file)
    sess_db.init_db()

    monkeypatch.setattr(server, "memory_store", mem_store)
    monkeypatch.setattr(server, "skill_manager", skill_mgr)
    monkeypatch.setattr(server, "session_db", sess_db)

    return {
        "mem_store": mem_store,
        "skill_mgr": skill_mgr,
        "sess_db": sess_db,
    }

def test_server_memory_action_get(mock_server_env):
    res_raw = server.memory(action="get", target="user")
    res = json.loads(res_raw)
    assert res["success"] is True
    assert "entries" in res

def test_server_memory_batch_string_parsing(mock_server_env):
    ops = json.dumps([{"action": "add", "target": "user", "content": "- User prefers dark mode."}])
    res_raw = server.memory(action="batch", operations=ops)
    res = json.loads(res_raw)
    assert res["success"] is True

def test_server_memory_batch_invalid_json(mock_server_env):
    res_raw = server.memory(action="batch", operations="{bad json")
    res = json.loads(res_raw)
    assert res["success"] is False
    assert "Invalid JSON in operations" in res["error"]

def test_server_memory_tool(mock_server_env):
    # Anchor
    server.memory(action="add", target="memory", content="Anchor memory entry")

    # Add
    res_str = server.memory(action="add", target="memory", content="Test server memory entry")
    res = json.loads(res_str)
    assert res["success"] is True

    # Replace
    res_str = server.memory(action="replace", target="memory", old_text="Test server", content="Updated entry")
    res = json.loads(res_str)
    assert res["success"] is True

    # Remove
    res_str = server.memory(action="remove", target="memory", old_text="Updated entry")
    res = json.loads(res_str)
    assert res["success"] is True

    # Batch
    batch_ops = [
        {"action": "add", "target": "user", "content": "User prefers concise style."}
    ]
    res_str = server.memory(action="batch", operations=batch_ops)
    res = json.loads(res_str)
    assert res["success"] is True

    # Unknown
    res_str = server.memory(action="invalid_action")
    res = json.loads(res_str)
    assert res["success"] is False

def test_server_skill_manage_tool(mock_server_env):
    # Create
    skill_content = "---\nname: tool-srv\ndescription: Test tool.\n---\n# Server Tool\n"
    res_str = server.skill_manage(action="create", name="tool-srv", content=skill_content)
    res = json.loads(res_str)
    assert res["success"] is True

    # Patch
    res_str = server.skill_manage(action="patch", name="tool-srv", old_string="# Server Tool", new_string="# Updated Tool")
    res = json.loads(res_str)
    assert res["success"] is True

    # Write file
    res_str = server.skill_manage(action="write_file", name="tool-srv", file_path="scripts/run.py", file_content="print(1)\n")
    res = json.loads(res_str)
    assert res["success"] is True

    # Delete action
    res_str = server.skill_manage(action="delete", name="tool-srv")
    res = json.loads(res_str)
    assert res["success"] is True

    # Unknown action
    res_str = server.skill_manage(action="invalid_action", name="tool-srv")
    res = json.loads(res_str)
    assert res["success"] is False

def test_server_session_search_tool(mock_server_env):
    res_str = server.session_search(query="nonexistent term")
    res = json.loads(res_str)
    assert isinstance(res, list)
    assert len(res) == 0

def test_server_session_get_message_tool(mock_server_env, tmp_path):
    sess_db = mock_server_env["sess_db"]
    t_file = tmp_path / "t.jsonl"
    t_file.write_text(json.dumps({"type": "USER_INPUT", "content": "Hello world from server test"}) + "\n")
    sess_db.ingest_transcript(t_file, "sess_srv", "/tmp")

    search_raw = server.session_search("Hello world")
    search_res = json.loads(search_raw)
    assert len(search_res) == 1
    msg_id = search_res[0]["message_id"]

    get_raw = server.session_get_message(message_id=msg_id, offset=0, limit=100)
    get_res = json.loads(get_raw)
    assert get_res["success"] is True
    assert get_res["content"] == "Hello world from server test"
    assert get_res["total_length"] == len("Hello world from server test")


def test_server_root_resolution(tmp_path, monkeypatch):
    assert hasattr(server, "AGENTS_ROOT")
    assert not str(server.SKILLS_DIR).endswith(".agents/.agents/skills")
    assert not str(server.MEMORY_PATH).endswith(".agents/.agents/memories/MEMORY.md")
    assert not str(server.DB_PATH).endswith(".agents/.agents/state/state.db")

    # Test resolve_agents_root when cwd is an external folder
    monkeypatch.chdir(tmp_path)
    resolved = server.resolve_agents_root()
    assert (resolved / "skills").is_dir() or resolved == Path.home() / ".agents"


def test_server_memory_tool_unknown_target_handled():
    from hermes_engine.server import memory
    res_str = memory(action="get", target="invalid_target")
    data = json.loads(res_str)
    assert data["success"] is False
    assert "error" in data


def test_server_memory_tool_invalid_operations_type():
    from hermes_engine.server import memory
    res_str = memory(action="batch", operations="not a list or json")
    data = json.loads(res_str)
    assert data["success"] is False
    assert "error" in data

    res_str2 = memory(action="batch", operations=123)
    data2 = json.loads(res_str2)
    assert data2["success"] is False
    assert "error" in data2

    res_str3 = memory(action="batch", operations=["not_a_dict"])
    data3 = json.loads(res_str3)
    assert data3["success"] is False
    assert "error" in data3


def test_server_session_get_message_handles_none_content(monkeypatch):
    from hermes_engine.server import session_get_message
    import hermes_engine.server as server_mod

    class DummyDB:
        def get_message(self, *args, **kwargs):
            return {"success": True, "message_id": 1, "session_id": "test", "step_index": 0, "content": None, "total_length": 0}

    monkeypatch.setattr(server_mod, "get_session_db", lambda: DummyDB())
    res_str = session_get_message(message_id=1)
    data = json.loads(res_str)
    assert data["success"] is True
    assert data["content"] == ""

def test_server_skill_manage_handles_none_arguments():
    from hermes_engine.server import skill_manage
    # Test create with None content
    res = json.loads(skill_manage(action="create", name="dummy-none"))
    assert res["success"] is False
    assert "error" in res

    # Test patch with None old_string
    res = json.loads(skill_manage(action="patch", name="dummy-none"))
    assert res["success"] is False
    assert "error" in res

    # Test write_file with None file_path
    res = json.loads(skill_manage(action="write_file", name="dummy-none"))
    assert res["success"] is False
    assert "error" in res


def test_server_skill_manage_write_file_content_alias(mock_server_env):
    from hermes_engine.server import skill_manage
    # Create skill first
    res = json.loads(skill_manage(action="create", name="alias-skill", content="---\nname: alias-skill\ndescription: test\n---\n"))
    assert res["success"] is True

    # Use 'content' instead of 'file_content'
    res = json.loads(skill_manage(action="write_file", name="alias-skill", file_path="run.py", content="print('hello alias')\n"))
    assert res["success"] is True
    assert res["path"] == "run.py"

    # Cleanup
    skill_manage(action="delete", name="alias-skill")

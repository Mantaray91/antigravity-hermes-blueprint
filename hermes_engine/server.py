import os
import sys
import json
from pathlib import Path
from typing import Any, Optional, Union, List, Dict

# Ensure repo root is on sys.path for direct invocation
repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from fastmcp import FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except (ImportError, ModuleNotFoundError):
        try:
            from mcp.server.mcpserver import MCPServer as FastMCP
        except ImportError:
            class FastMCP:
                def __init__(self, name: str):
                    self.name = name

                def tool(self):
                    def decorator(fn):
                        return fn
                    return decorator

                def run(self):
                    pass

try:
    from hermes_engine.memory import MemoryStore
    from hermes_engine.skills import (
        SkillManager,
        set_current_write_origin,
        get_current_write_origin,
    )
    from hermes_engine.session_db import SessionDB
    from hermes_engine.config import (
        resolve_agents_root,
        resolve_user_memory_path,
        resolve_workspace_memory_path,
    )
except ImportError:
    from mcp.hermes_engine.memory import MemoryStore
    from mcp.hermes_engine.skills import (
        SkillManager,
        set_current_write_origin,
        get_current_write_origin,
    )
    from mcp.hermes_engine.session_db import SessionDB
    from mcp.hermes_engine.config import (
        resolve_agents_root,
        resolve_user_memory_path,
        resolve_workspace_memory_path,
    )

mcp = FastMCP("hermes-engine")

AGENTS_ROOT = resolve_agents_root()
USER_PATH = resolve_user_memory_path()
MEMORY_PATH = resolve_workspace_memory_path()
SKILLS_DIR = AGENTS_ROOT / "skills"
USAGE_PATH = SKILLS_DIR / ".usage.json"
DB_PATH = AGENTS_ROOT / "state" / "state.db"

memory_store = MemoryStore(USER_PATH, MEMORY_PATH)
skill_manager = SkillManager(SKILLS_DIR, USAGE_PATH)
session_db = SessionDB(DB_PATH)

def get_session_db() -> SessionDB:
    return session_db

@mcp.tool()
def memory(
    action: str,
    target: str = "memory",
    content: Optional[str] = None,
    old_text: Optional[str] = None,
    operations: Optional[Union[str, List[Dict[str, Any]]]] = None,
) -> str:
    """Manage persistent curated memory (MEMORY.md or USER.md). Action: get, add, replace, remove, batch."""
    try:
        if action != "batch" and target not in ("user", "memory"):
            return json.dumps({"success": False, "error": f"Unknown target '{target}'. Use 'user' or 'memory'."})

        if action == "batch" or operations is not None:
            if operations is None:
                operations = []
            elif isinstance(operations, str):
                try:
                    operations = json.loads(operations)
                except Exception as e:
                    return json.dumps({"success": False, "error": f"Invalid JSON in operations: {e}"})
            if not isinstance(operations, list):
                return json.dumps({"success": False, "error": "operations must be a list of operation objects"})
            for i, op in enumerate(operations):
                if not isinstance(op, dict):
                    return json.dumps({"success": False, "error": f"Operation at index {i} must be a dictionary"})
            return json.dumps(memory_store.batch(operations))

        memory_store.load()
        if action == "get":
            return json.dumps({"success": True, "target": target, "entries": memory_store.get_entries(target)})
        elif action == "add":
            return json.dumps(memory_store.add(target, content))
        elif action == "replace":
            return json.dumps(memory_store.replace(target, old_text, content))
        elif action == "remove":
            return json.dumps(memory_store.remove(target, old_text))
        return json.dumps({"success": False, "error": f"Unknown action '{action}'"})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

@mcp.tool()
def skill_manage(action: str, name: str, content: str = None, file_path: str = None, file_content: str = None, old_string: str = None, new_string: str = None, origin: str = None) -> str:
    """Create, patch, or delete procedural skills following agentskills.io standard. Action: create, patch, write_file, delete."""
    try:
        if action == "create":
            return json.dumps(skill_manager.create(name, content, origin=origin))
        elif action == "patch":
            return json.dumps(skill_manager.patch(name, old_string, new_string, origin=origin))
        elif action == "write_file":
            effective_content = file_content if file_content is not None else content
            return json.dumps(skill_manager.write_file(name, file_path, effective_content, origin=origin))
        elif action == "delete":
            return json.dumps(skill_manager.delete(name, origin=origin))
        return json.dumps({"success": False, "error": f"Unknown action '{action}'"})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def session_search(query: str, limit: int = 10) -> str:
    """Search historical session messages using SQLite FTS5 BM25 ranking."""
    try:
        db = get_session_db()
        return json.dumps(db.search(query, limit=limit))
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

@mcp.tool()
def session_get_message(message_id: int, offset: int = 0, limit: int = 4000) -> str:
    """Retrieve full content of a message from session search with pagination."""
    try:
        db = get_session_db()
        msg = db.get_message(message_id, offset=offset, limit=limit)
        if not msg or not msg.get("success", True):
            return json.dumps({"success": False, "error": msg.get("error", f"Message {message_id} not found")})
        if msg.get("content") is None:
            msg["content"] = ""
        return json.dumps({"success": True, **msg})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


if __name__ == "__main__":
    mcp.run()

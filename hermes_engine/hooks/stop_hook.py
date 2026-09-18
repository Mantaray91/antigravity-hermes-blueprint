#!/usr/bin/env python3
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Optional, Union, Dict, Any

# Ensure repo root is on sys.path for direct invocation
repo_root = str(Path(__file__).resolve().parent.parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from hermes_engine.session_db import SessionDB, load_canonical_transcript_steps
    from hermes_engine.memory import MemoryStore
    from hermes_engine.skills import SkillManager
    from hermes_engine.reflector import SessionReflector
    from hermes_engine.config import resolve_agents_root
except ImportError:
    from mcp.hermes_engine.session_db import SessionDB, load_canonical_transcript_steps
    from mcp.hermes_engine.memory import MemoryStore
    from mcp.hermes_engine.skills import SkillManager
    from mcp.hermes_engine.reflector import SessionReflector
    from mcp.hermes_engine.config import resolve_agents_root


def run_stop_hook(
    transcript: Union[str, Path],
    conv_id: str = "unknown",
    workspace: str = "",
    db_path: Optional[Union[str, Path]] = None,
    user_path: Optional[Union[str, Path]] = None,
    memory_path: Optional[Union[str, Path]] = None,
    skills_dir: Optional[Union[str, Path]] = None,
    usage_path: Optional[Union[str, Path]] = None,
    max_time_seconds: float = 1.5,
) -> Dict[str, Any]:
    agents_root = resolve_agents_root()
    resolved_db = Path(db_path) if db_path is not None else agents_root / "state" / "state.db"
    resolved_user = Path(user_path) if user_path is not None else Path.home() / ".gemini" / "memories" / "USER.md"
    resolved_mem = Path(memory_path) if memory_path is not None else agents_root / "memories" / "MEMORY.md"
    resolved_skills = Path(skills_dir) if skills_dir is not None else agents_root / "skills"
    resolved_usage = Path(usage_path) if usage_path is not None else resolved_skills / ".usage.json"

    deadline = time.monotonic() + max_time_seconds if max_time_seconds is not None else None

    if transcript and Path(transcript).exists():
        transcript_path = Path(transcript)
        try:
            steps = load_canonical_transcript_steps(transcript_path)
        except Exception:
            steps = []

        # Ingest transcript into SQLite FTS5 database
        try:
            db = SessionDB(resolved_db)
            db.ingest_transcript(conv_id, steps, workspace=workspace, deadline=deadline)
        except Exception:
            pass

        # Reflect on session to extract preferences and procedural skills
        try:
            memory_store = MemoryStore(resolved_user, resolved_mem)
            skill_manager = SkillManager(resolved_skills, resolved_usage)
            reflector = SessionReflector(memory_store, skill_manager)
            reflector.reflect_session(transcript_path, deadline=deadline, steps=steps)
        except Exception:
            pass

    return {"decision": "allow"}

def main():
    agents_root = resolve_agents_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(agents_root / "state" / "state.db"))
    parser.add_argument("--user-path", default=str(Path.home() / ".gemini" / "memories" / "USER.md"))
    parser.add_argument("--memory-path", default=str(agents_root / "memories" / "MEMORY.md"))
    parser.add_argument("--skills-dir", default=str(agents_root / "skills"))
    parser.add_argument("--usage-path", default=None)
    parser.add_argument("--max-time-seconds", type=float, default=1.5)
    args, _ = parser.parse_known_args()

    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    transcript = payload.get("transcriptPath", "")
    conv_id = payload.get("conversationId", "unknown")
    ws = (payload.get("workspacePaths") or [str(Path.cwd())])[0]

    result = run_stop_hook(
        transcript=transcript,
        conv_id=conv_id,
        workspace=ws,
        db_path=args.db_path,
        user_path=args.user_path,
        memory_path=args.memory_path,
        skills_dir=args.skills_dir,
        usage_path=args.usage_path,
        max_time_seconds=args.max_time_seconds,
    )
    print(json.dumps(result))

if __name__ == "__main__":
    main()

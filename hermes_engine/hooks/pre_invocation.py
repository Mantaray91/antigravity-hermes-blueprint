#!/usr/bin/env python3
import sys
import json
import argparse
from pathlib import Path
from typing import Optional, Union, Dict, Any

# Ensure repo root is on sys.path for direct invocation
repo_root = str(Path(__file__).resolve().parent.parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from hermes_engine.memory import MemoryStore
    from hermes_engine.config import resolve_agents_root
except ImportError:
    from mcp.hermes_engine.memory import MemoryStore
    from mcp.hermes_engine.config import resolve_agents_root


def run_pre_invocation_hook(
    hook_input: Optional[Dict[str, Any]] = None,
    user_path: Optional[Union[str, Path]] = None,
    memory_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    try:
        if not isinstance(hook_input, dict):
            hook_input = {}

        raw_inv = hook_input.get("invocationNum", 0)
        try:
            inv_num = int(raw_inv) if raw_inv is not None else 0
        except (ValueError, TypeError):
            inv_num = 0

        # Only inject memory snapshot on first invocation of the turn (invocationNum == 0) to prevent token bloat
        if inv_num > 0:
            return {"injectSteps": []}

        agents_root = resolve_agents_root()
        u_path = Path(user_path) if user_path is not None else Path.home() / ".gemini" / "memories" / "USER.md"
        m_path = Path(memory_path) if memory_path is not None else agents_root / "memories" / "MEMORY.md"

        try:
            store = MemoryStore(u_path, m_path)
            snapshot = store.render_snapshot()
            if not snapshot.strip():
                return {"injectSteps": []}
            return {
                "injectSteps": [
                    {
                        "ephemeralMessage": f"[PERSISTENT MEMORY SNAPSHOT]\n{snapshot}"
                    }
                ]
            }
        except Exception:
            return {"injectSteps": []}
    except Exception:
        return {"injectSteps": []}

def main():
    agents_root = resolve_agents_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-path", default=str(Path.home() / ".gemini" / "memories" / "USER.md"))
    parser.add_argument("--memory-path", default=str(agents_root / "memories" / "MEMORY.md"))
    args, _ = parser.parse_known_args()

    # Read stdin to inspect invocationNum
    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    result = run_pre_invocation_hook(
        hook_input=payload,
        user_path=args.user_path,
        memory_path=args.memory_path,
    )
    print(json.dumps(result))

if __name__ == "__main__":
    main()

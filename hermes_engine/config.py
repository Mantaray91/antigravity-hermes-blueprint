import os
from pathlib import Path


def resolve_agents_root() -> Path:
    """
    Dynamically resolve the agents workspace root directory.

    Priority:
    1. AGENTS_ROOT environment variable (if set and points to an existing directory)
    2. Current working directory if it contains a 'skills' folder or is named '.agents'
    3. cwd / '.agents' if that directory contains a 'skills' folder
    4. ~/.agents if that directory contains a 'skills' folder
    5. Fallback to current working directory
    """
    env_root = os.environ.get("AGENTS_ROOT")
    if env_root and Path(env_root).is_dir():
        return Path(env_root)

    cwd = Path.cwd()
    home_agents = Path.home() / ".agents"

    if (cwd / "skills").is_dir() or cwd.name == ".agents":
        return cwd
    elif (cwd / ".agents" / "skills").is_dir():
        return cwd / ".agents"
    elif (home_agents / "skills").is_dir():
        return home_agents

    return cwd

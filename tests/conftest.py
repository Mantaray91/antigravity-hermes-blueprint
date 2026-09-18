import sys
from pathlib import Path

# Ensure repository root is on sys.path for test imports
repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

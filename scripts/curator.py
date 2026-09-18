#!/usr/bin/env python3
import os
import sys
import json
import time
import shutil
import tarfile
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from hermes_engine.config import resolve_agents_root
except ImportError:
    from mcp.hermes_engine.config import resolve_agents_root


class Curator:
    """
    Hermes Curator & Anti-Bloat Engine.
    Tracks skill provenance in .usage.json, prunes inactive agent-created skills to .archive/,
    and takes pre-mutation tarball backups with instant rollback capability.
    """

    def __init__(self, skills_dir: Path, usage_path: Path, backup_dir: Path):
        self.skills_dir = Path(skills_dir)
        self.usage_path = Path(usage_path)
        self.usage_file = self.usage_path
        self.backup_dir = Path(backup_dir)
        self.archive_dir = self.skills_dir / ".archive"

        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def _load_usage(self) -> dict:
        if self.usage_path.exists():
            try:
                return json.loads(self.usage_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_usage(self, data: dict) -> None:
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.usage_file.with_suffix(f".tmp.{os.getpid()}")
        temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_file.replace(self.usage_file)

    def set_skill_usage(
        self,
        name: str,
        agent_created: bool = True,
        last_used: Optional[float] = None,
        pinned: bool = False,
    ) -> None:
        data = self._load_usage()
        if name not in data:
            data[name] = {}
        data[name]["agent_created"] = agent_created
        data[name]["last_used"] = last_used if last_used is not None else time.time()
        data[name]["pinned"] = pinned
        self._save_usage(data)

    def pin(self, name: str) -> None:
        data = self._load_usage()
        if name not in data:
            data[name] = {}
        data[name]["pinned"] = True
        self._save_usage(data)

    def unpin(self, name: str) -> None:
        data = self._load_usage()
        if name not in data:
            data[name] = {}
        data[name]["pinned"] = False
        self._save_usage(data)

    def backup(self) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        target_tar = self.backup_dir / f"skills_backup_{ts}.tar.gz"
        with tarfile.open(target_tar, "w:gz") as tar:
            for item in self.skills_dir.iterdir():
                if item.name in (".curator_backups", ".archive"):
                    continue
                tar.add(item, arcname=item.name)
        return target_tar

    def rollback(self) -> bool:
        backups = sorted(self.backup_dir.glob("skills_backup_*.tar.gz"))
        if not backups:
            return False
        latest = backups[-1]

        # Read member top-level names before extracting
        with tarfile.open(latest, "r:gz") as tar:
            members = tar.getnames()
            top_level_members = {m.split("/")[0] for m in members if m}

            # Delete any skill folder in skills_dir that is NOT in the backup
            for item in self.skills_dir.iterdir():
                if item.name in (".curator_backups", ".archive"):
                    continue
                if item.name not in top_level_members:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()

            if hasattr(tarfile, "data_filter"):
                tar.extractall(path=self.skills_dir, filter="data")
            else:
                tar.extractall(path=self.skills_dir)
        return True

    def prune(self, archive_after_days: int = 30) -> List[str]:
        cutoff = time.time() - (archive_after_days * 86400)
        data = self._load_usage()
        archived: List[str] = []

        for item in self.skills_dir.iterdir():
            if not item.is_dir() or item.name in (".curator_backups", ".archive"):
                continue
            name = item.name
            meta = data.get(name, {})
            if not meta.get("agent_created", False) or meta.get("pinned", False):
                continue
            last_used = meta.get("last_used", 0)
            if last_used < cutoff:
                dest = self.archive_dir / name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.move(str(item), str(dest))
                archived.append(name)
        return archived

    def sync_existing_skills(self) -> int:
        data = self._load_usage()
        added = 0
        for item in self.skills_dir.iterdir():
            if not item.is_dir() or item.name in (".curator_backups", ".archive") or item.name.startswith("."):
                continue
            if not (item / "SKILL.md").exists():
                continue
            name = item.name
            if name not in data:
                data[name] = {
                    "created_by": "user",
                    "agent_created": False,
                    "pinned": False,
                    "created_at": item.stat().st_ctime,
                    "last_used": item.stat().st_mtime,
                }
                added += 1
        if added > 0:
            self._save_usage(data)
        return added

    def get_status(self, archive_after_days: int = 30) -> Dict[str, Any]:
        self.sync_existing_skills()
        cutoff = time.time() - (archive_after_days * 86400)
        data = self._load_usage()
        total_skills = 0
        stale_skills: List[str] = []
        pinned_skills: List[str] = []

        for item in self.skills_dir.iterdir():
            if not item.is_dir() or item.name in (".curator_backups", ".archive"):
                continue
            total_skills += 1
            meta = data.get(item.name, {})
            if meta.get("pinned", False):
                pinned_skills.append(item.name)
            if meta.get("agent_created", False) and not meta.get("pinned", False):
                last_used = meta.get("last_used", 0)
                if last_used < cutoff:
                    stale_skills.append(item.name)

        return {
            "total_skills": total_skills,
            "stale_count": len(stale_skills),
            "stale_skills": sorted(stale_skills),
            "pinned_skills": sorted(pinned_skills),
        }


def main():
    parser = argparse.ArgumentParser(description="Hermes Curator Engine")
    parser.add_argument("action", choices=["status", "backup", "rollback", "prune", "pin", "unpin", "sync"])
    parser.add_argument("--name", help="Skill name for pin/unpin")
    parser.add_argument("--days", type=int, default=30, help="Inactivity cutoff days for pruning/status")
    parser.add_argument("--skills-dir", help="Path to skills directory")
    parser.add_argument("--usage-file", help="Path to .usage.json file")
    parser.add_argument("--backup-dir", help="Path to backup directory")
    args = parser.parse_args()

    skills_dir = (
        Path(args.skills_dir)
        if args.skills_dir
        else resolve_agents_root() / "skills"
    )
    usage_file = Path(args.usage_file) if args.usage_file else skills_dir / ".usage.json"
    backup_dir = Path(args.backup_dir) if args.backup_dir else skills_dir / ".curator_backups"

    curator = Curator(skills_dir=skills_dir, usage_path=usage_file, backup_dir=backup_dir)

    if args.action == "backup":
        tar = curator.backup()
        print(f"Backup created at: {tar}")
    elif args.action == "rollback":
        res = curator.rollback()
        print(f"Rollback status: {res}")
    elif args.action == "prune":
        archived = curator.prune(archive_after_days=args.days)
        print(f"Archived {len(archived)} skills: {archived}")
    elif args.action in ("pin", "unpin"):
        if not args.name:
            print(f"Error: --name is required for {args.action}", file=sys.stderr)
            sys.exit(1)
        if args.action == "pin":
            curator.pin(args.name)
            print(f"Pinned {args.name}")
        else:
            curator.unpin(args.name)
            print(f"Unpinned {args.name}")
    elif args.action == "sync":
        added = curator.sync_existing_skills()
        print(f"Synced telemetry: {added} new skills registered into .usage.json")
    elif args.action == "status":
        st = curator.get_status(archive_after_days=args.days)
        print("Curator status: Active")
        print(f"Total skills: {st['total_skills']}")
        print(f"Stale skills ({args.days}d): {st['stale_count']} ({st['stale_skills']})")
        print(f"Pinned skills: {len(st['pinned_skills'])} ({st['pinned_skills']})")


if __name__ == "__main__":
    main()

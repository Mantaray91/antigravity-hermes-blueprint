import os
from pathlib import Path
from typing import Dict, Any, Optional
import json
import re
import ast
import time
import shutil
import contextvars

_write_origin: contextvars.ContextVar[str] = contextvars.ContextVar("skill_write_origin", default="foreground")

def set_current_write_origin(origin: str) -> contextvars.Token[str]:
    return _write_origin.set(origin or "foreground")

def reset_current_write_origin(token: contextvars.Token[str]) -> None:
    _write_origin.reset(token)

def get_current_write_origin() -> str:
    return _write_origin.get()

class SkillManager:
    def __init__(self, skills_dir: Path, usage_path: Path):
        self.skills_dir = Path(skills_dir)
        self.usage_path = Path(usage_path)
        self.usage_file = self.usage_path
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.sync_existing_skills()

    @staticmethod
    def _extract_description(content: str) -> Optional[str]:
        desc_match = re.search(r'(?m)^description:\s*(.+)$', content)
        if desc_match:
            return desc_match.group(1).strip().strip("\"'")
        return None

    def sync_existing_skills(self) -> int:
        data = self._load_usage()
        added = 0
        if self.skills_dir.exists():
            for item in self.skills_dir.iterdir():
                if not item.is_dir() or item.name.startswith("."):
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
                        "last_used": item.stat().st_mtime
                    }
                    added += 1
        if added > 0:
            self._save_usage(data)
        return added


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

    def is_agent_created(self, name: str) -> bool:
        data = self._load_usage()
        return bool(data.get(name, {}).get("agent_created", False))

    def mark_agent_created(self, name: str) -> None:
        data = self._load_usage()
        if name not in data:
            data[name] = {}
        data[name]["agent_created"] = True
        data[name]["created_by"] = "agent"
        data[name]["created_at"] = data[name].get("created_at") or time.time()
        self._save_usage(data)

    def is_pinned(self, name: str) -> bool:
        data = self._load_usage()
        return bool(data.get(name, {}).get("pinned", False))

    def create(self, name: str, content: str, origin: Optional[str] = None, description: Optional[str] = None) -> Dict[str, Any]:
        eff_origin = origin if origin is not None else get_current_write_origin()
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return {"success": False, "error": f"Invalid skill name '{name}'. Must be alphanumeric with '-' or '_'."}
        if not content:
            return {"success": False, "error": "Content is required for skill creation."}

        target_dir = self.skills_dir / name
        skill_file = target_dir / "SKILL.md"
        if eff_origin == "background_review":
            if skill_file.exists() and not self.is_agent_created(name):
                return {"success": False, "error": f"Refusing to overwrite user-made skill '{name}'."}
            if self.is_pinned(name):
                return {"success": False, "error": f"Skill '{name}' is pinned."}

        if description:
            if len(description) > 60:
                return {
                    "success": False,
                    "error": f"Description length exceeds 60 chars ({len(description)}/60). Keep it a concise trigger."
                }
            if not content.startswith("---"):
                content = f"---\nname: {name}\ndescription: {description}\n---\n{content}"

        desc = self._extract_description(content)
        if desc and len(desc) > 60:
            return {
                "success": False,
                "error": f"Description length exceeds 60 chars ({len(desc)}/60). Keep it a concise trigger."
            }

        target_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(content, encoding="utf-8")

        data = self._load_usage()
        if name not in data:
            data[name] = {}
        if eff_origin == "background_review":
            data[name]["agent_created"] = True
            data[name]["created_by"] = "agent"
        else:
            data[name]["agent_created"] = False
            data[name]["created_by"] = "user"
        data[name]["created_at"] = data[name].get("created_at") or time.time()
        self._save_usage(data)

        return {"success": True, "action": "create", "name": name}

    def patch(self, name: str, old_string: str, new_string: str, origin: Optional[str] = None) -> Dict[str, Any]:
        eff_origin = origin if origin is not None else get_current_write_origin()
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return {"success": False, "error": f"Invalid skill name '{name}'. Must be alphanumeric with '-' or '_'."}
        if not old_string or new_string is None:
            return {"success": False, "error": "Both old_string and new_string are required for patch."}

        skill_file = self.skills_dir / name / "SKILL.md"
        if not skill_file.exists():
            return {"success": False, "error": f"Skill '{name}' does not exist."}
        if eff_origin == "background_review":
            if not self.is_agent_created(name):
                return {"success": False, "error": f"Cannot modify user-made skill '{name}'."}
            if self.is_pinned(name):
                return {"success": False, "error": f"Skill '{name}' is pinned."}

        text = skill_file.read_text(encoding="utf-8")
        if old_string not in text:
            return {"success": False, "error": f"Target substring not found in {name}/SKILL.md."}

        updated = text.replace(old_string, new_string, 1)
        desc = self._extract_description(updated)
        if desc and len(desc) > 60:
            return {"success": False, "error": f"Description exceeds 60 characters ({len(desc)})"}
        skill_file.write_text(updated, encoding="utf-8")
        return {"success": True, "action": "patch", "name": name}

    def write_file(self, name: str, file_path: str, content: str, origin: Optional[str] = None) -> Dict[str, Any]:
        eff_origin = origin if origin is not None else get_current_write_origin()
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return {"success": False, "error": f"Invalid skill name '{name}'. Must be alphanumeric with '-' or '_'."}
        if not file_path or content is None:
            return {"success": False, "error": "Both file_path and content are required for write_file."}


        skill_dir = self.skills_dir / name
        if not (skill_dir / "SKILL.md").exists():
            return {"success": False, "error": f"Skill '{name}' does not exist."}
        if eff_origin == "background_review":
            if not self.is_agent_created(name):
                return {"success": False, "error": f"Cannot write files into user-made skill '{name}'."}
            if self.is_pinned(name):
                return {"success": False, "error": f"Skill '{name}' is pinned."}

        clean_path = Path(file_path)
        if clean_path.is_absolute() or ".." in clean_path.parts:
            return {"success": False, "error": f"Path traversal detected in file_path '{file_path}'."}

        target = (skill_dir / clean_path).resolve()
        if not target.is_relative_to(skill_dir.resolve()):
            return {"success": False, "error": f"Path traversal detected in '{file_path}'."}

        filename = clean_path.name
        if filename == "SKILL.md":
            desc = self._extract_description(content)
            if desc and len(desc) > 60:
                return {"success": False, "error": f"Description exceeds 60 characters ({len(desc)})"}

        if clean_path.suffix == ".py":
            try:
                ast.parse(content)
            except SyntaxError as e:
                return {"success": False, "error": f"Python syntax error in {file_path}: {e}"}

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"success": True, "action": "write_file", "name": name, "path": str(clean_path)}

    def delete(self, name: str, origin: Optional[str] = None) -> Dict[str, Any]:
        eff_origin = origin if origin is not None else get_current_write_origin()
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return {"success": False, "error": f"Invalid skill name '{name}'. Must be alphanumeric with '-' or '_'."}

        skill_dir = self.skills_dir / name
        if not skill_dir.exists():
            return {"success": False, "error": f"Skill '{name}' does not exist."}
        if self.is_pinned(name):
            return {"success": False, "error": f"Skill '{name}' is pinned."}
        if eff_origin == "background_review":
            if not self.is_agent_created(name):
                return {"success": False, "error": f"Cannot delete user-made skill '{name}'."}

        shutil.rmtree(skill_dir)
        data = self._load_usage()
        if name in data:
            del data[name]
            self._save_usage(data)
        return {"success": True, "action": "delete", "name": name}

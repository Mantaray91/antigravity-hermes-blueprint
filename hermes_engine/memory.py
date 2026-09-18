from pathlib import Path
from typing import Dict, Any, List, Optional
import tempfile
import os
import fcntl

ENTRY_DELIMITER = "\n§\n"

class MemoryStore:
    def __init__(
        self,
        user_path: Path,
        memory_path: Path,
        user_limit: int = 1375,
        memory_limit: int = 2200
    ):
        self.user_path = Path(user_path)
        self.memory_path = Path(memory_path)
        self.user_limit = user_limit
        self.memory_limit = memory_limit
        self.user_entries: List[str] = []
        self.memory_entries: List[str] = []
        self.load()

    @property
    def user_file(self) -> Path:
        return self.user_path

    @property
    def memory_file(self) -> Path:
        return self.memory_path

    def _get_target_state(self, target: str):
        if target == "user":
            return self.user_path, self.user_entries, self.user_limit
        elif target == "memory":
            return self.memory_path, self.memory_entries, self.memory_limit
        raise ValueError(f"Unknown target '{target}'. Use 'user' or 'memory'.")

    def get_entries(self, target: str) -> List[str]:
        _, entries, _ = self._get_target_state(target)
        return list(entries)

    def _load_target(self, target: str) -> None:
        path, entries_list, _ = self._get_target_state(target)
        entries_list.clear()
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                for raw in text.split(ENTRY_DELIMITER):
                    clean = raw.strip()
                    if clean:
                        entries_list.append(clean)

    def load(self, target: Optional[str] = None) -> None:
        targets = [target] if target else ["user", "memory"]
        for tgt in targets:
            path, _, _ = self._get_target_state(tgt)
            if path.exists():
                lock_file = path.parent / f".{path.name}.lock"
                lock_file.parent.mkdir(parents=True, exist_ok=True)
                with open(lock_file, "a+", encoding="utf-8") as lf:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_SH)
                    try:
                        self._load_target(tgt)
                    finally:
                        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            else:
                _, entries_list, _ = self._get_target_state(tgt)
                entries_list.clear()

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = path.parent / f".{path.name}.lock"
        with open(lock_file, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                temp_file = path.with_suffix(f".tmp.{os.getpid()}")
                try:
                    temp_file.write_text(content, encoding="utf-8")
                    temp_file.replace(path)
                except Exception:
                    if temp_file.exists():
                        try:
                            temp_file.unlink()
                        except OSError:
                            pass
                    raise
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _sync(self, target: str) -> None:
        path, entries, _ = self._get_target_state(target)
        raw_text = ENTRY_DELIMITER.join(entries) if entries else ""
        self._atomic_write(path, raw_text)

    def _mutate(self, target: str, callback) -> Dict[str, Any]:
        path, entries, limit = self._get_target_state(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = path.parent / f".{path.name}.lock"
        with open(lock_file, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                # 1. Reload fresh state from disk under exclusive lock
                self._load_target(target)
                _, entries, limit = self._get_target_state(target)

                # 2. Execute mutation callback
                res = callback(entries, limit)
                if res.get("success", False):
                    # 3. Write atomically to disk
                    raw_text = ENTRY_DELIMITER.join(entries) if entries else ""
                    temp_file = path.with_suffix(f".tmp.{os.getpid()}")
                    try:
                        temp_file.write_text(raw_text, encoding="utf-8")
                        temp_file.replace(path)
                    except Exception:
                        if temp_file.exists():
                            try:
                                temp_file.unlink()
                            except OSError:
                                pass
                        raise
                return res
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def add_entry(self, target: str, content: str = None) -> bool:
        if content is None:
            content = target
            target = "memory"
        res = self.add(target, content)
        return bool(res.get("success", False))

    def add(self, target: str, content: str) -> Dict[str, Any]:
        content = (content or "").strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}
        if ENTRY_DELIMITER.strip() in content:
            return {"success": False, "error": f"Content cannot contain delimiter '{ENTRY_DELIMITER.strip()}'."}

        def _do_add(entries: List[str], limit: int) -> Dict[str, Any]:
            if content in entries:
                return {"success": True, "message": "Content already exists"}

            candidate_entries = list(entries) + [content]
            candidate_raw = ENTRY_DELIMITER.join(candidate_entries)
            evicted_entries: List[Dict[str, str]] = []

            # Two-Zone Eviction if limit exceeded
            # Index 0 is the Anchor Zone (Profile header/metadata)
            if len(candidate_raw) > limit:
                # While over limit and there are dynamic entries to evict (index 1+)
                while len(candidate_raw) > limit and len(candidate_entries) > 2:
                    evicted = candidate_entries.pop(1)
                    evicted_entries.append({"target": target, "evicted": evicted})
                    candidate_raw = ENTRY_DELIMITER.join(candidate_entries)

            if len(candidate_raw) > limit:
                return {
                    "success": False,
                    "error": f"Memory limit exceeded ({len(candidate_raw)}/{limit} chars). Even after dynamic eviction, content cannot fit."
                }

            entries.clear()
            entries.extend(candidate_entries)
            result: Dict[str, Any] = {"success": True, "message": "Content added successfully"}
            if evicted_entries:
                result["evicted"] = evicted_entries
                result["eviction_count"] = len(evicted_entries)
            return result


        return self._mutate(target, _do_add)

    def replace(self, target: str, old_text: str, content: str) -> Dict[str, Any]:
        content = (content or "").strip()
        old_text = (old_text or "").strip()
        if not old_text or not content:
            return {"success": False, "error": "Both old_text and content are required."}
        if ENTRY_DELIMITER.strip() in content:
            return {"success": False, "error": f"Content cannot contain delimiter '{ENTRY_DELIMITER.strip()}'."}

        def _do_replace(entries: List[str], limit: int) -> Dict[str, Any]:
            matches = [i for i, e in enumerate(entries) if old_text in e]
            if not matches:
                return {"success": False, "error": f"No entry containing '{old_text}' found."}
            if len(matches) > 1:
                return {"success": False, "error": f"Ambiguous match for '{old_text}'. Found {len(matches)} entries."}
            idx = matches[0]
            candidate = list(entries)
            candidate[idx] = content
            total_len = len(ENTRY_DELIMITER.join(candidate))
            if total_len > limit:
                return {
                    "success": False,
                    "error": f"Replacement exceeds memory limit ({total_len}/{limit} chars)."
                }
            entries[idx] = content
            return {"success": True, "action": "replace", "target": target}

        return self._mutate(target, _do_replace)

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        old_text = (old_text or "").strip()
        if not old_text:
            return {"success": False, "error": "old_text is required for remove."}

        def _do_remove(entries: List[str], limit: int) -> Dict[str, Any]:
            matches = [i for i, e in enumerate(entries) if old_text in e]
            if not matches:
                return {"success": False, "error": f"No entry containing '{old_text}' found."}
            if len(matches) > 1:
                return {"success": False, "error": f"Ambiguous match for '{old_text}'. Found {len(matches)} entries."}
            idx = matches[0]
            if idx == 0:
                return {
                    "success": False,
                    "error": "Cannot remove Anchor Zone (index 0). Use 'replace' action to modify identity entries."
                }
            del entries[idx]
            return {"success": True, "action": "remove", "target": target}

        return self._mutate(target, _do_remove)

    def batch(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        targets = sorted(list({op.get("target", "memory") for op in operations if isinstance(op, dict)}))
        file_handles = []
        try:
            for tgt in targets:
                p, _, _ = self._get_target_state(tgt)
                p.parent.mkdir(parents=True, exist_ok=True)
                lf_path = p.parent / f".{p.name}.lock"
                lf = open(lf_path, "a+", encoding="utf-8")
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                file_handles.append(lf)
                self._load_target(tgt)

            user_backup = list(self.user_entries)
            mem_backup = list(self.memory_entries)
            evicted_entries: List[Dict[str, str]] = []

            try:
                for op in operations:
                    if not isinstance(op, dict):
                        raise ValueError("Each operation must be a dictionary")
                    act = op.get("action")
                    tgt = op.get("target", "memory")
                    if tgt not in ("user", "memory"):
                        raise ValueError(f"Unknown target '{tgt}'. Use 'user' or 'memory'.")
                    cnt = (op.get("content") or "").strip()
                    old = (op.get("old_text") or "").strip()

                    _, entries, limit = self._get_target_state(tgt)

                    if act == "add":
                        if not cnt or ENTRY_DELIMITER.strip() in cnt:
                            raise ValueError("Invalid content for add")
                        candidate = list(entries) + [cnt]
                        candidate_raw = ENTRY_DELIMITER.join(candidate)
                        
                        # Two-Zone Eviction in batch: evict index 1+ if over limit
                        while len(candidate_raw) > limit and len(candidate) > 2:
                            evicted = candidate.pop(1)
                            evicted_entries.append({"target": tgt, "evicted": evicted})
                            candidate_raw = ENTRY_DELIMITER.join(candidate)

                        if len(candidate_raw) > limit:
                            raise ValueError(f"Limit exceeded for {tgt} even after dynamic eviction")
                        entries.clear()
                        entries.extend(candidate)
                    elif act == "replace":
                        if not old or not cnt or ENTRY_DELIMITER.strip() in cnt:
                            raise ValueError("Invalid parameters for replace")
                        matches = [i for i, e in enumerate(entries) if old in e]
                        if len(matches) != 1:
                            raise ValueError(f"Ambiguous or missing match for '{old}'")
                        entries[matches[0]] = cnt
                        if len(ENTRY_DELIMITER.join(entries)) > limit:
                            raise ValueError(f"Limit exceeded for {tgt}")
                    elif act == "remove":
                        if not old:
                            raise ValueError("old_text required for remove")
                        matches = [i for i, e in enumerate(entries) if old in e]
                        if len(matches) != 1:
                            raise ValueError(f"Ambiguous or missing match for '{old}'")
                        if matches[0] == 0:
                            raise ValueError("Cannot remove Anchor Zone (index 0) via batch. Use 'replace' action.")
                        del entries[matches[0]]
                    else:
                        raise ValueError(f"Unknown batch action '{act}'")

                # Atomic write loop across modified targets
                for tgt in targets:
                    p, entries, _ = self._get_target_state(tgt)
                    raw_text = ENTRY_DELIMITER.join(entries) if entries else ""
                    temp_file = p.with_suffix(f".tmp.{os.getpid()}")
                    try:
                        temp_file.write_text(raw_text, encoding="utf-8")
                        temp_file.replace(p)
                    except Exception:
                        if temp_file.exists():
                            try:
                                temp_file.unlink()
                            except OSError:
                                pass
                        raise

                result: Dict[str, Any] = {"success": True, "applied": len(operations)}
                if evicted_entries:
                    result["evicted"] = evicted_entries
                    result["eviction_count"] = len(evicted_entries)
                return result

            except Exception as e:
                self.user_entries = user_backup
                self.memory_entries = mem_backup
                return {"success": False, "error": f"Batch transaction aborted: {e}"}

        finally:
            for lf in reversed(file_handles):
                try:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                    lf.close()
                except Exception:
                    pass


    def render_snapshot(self) -> str:
        parts = []
        if self.user_entries:
            parts.append("=== USER PROFILE (who the user is) ===")
            parts.append(ENTRY_DELIMITER.join(self.user_entries))
        if self.memory_entries:
            parts.append("=== WORKSPACE MEMORY (conventions & notes) ===")
            parts.append(ENTRY_DELIMITER.join(self.memory_entries))
        return "\n\n".join(parts)

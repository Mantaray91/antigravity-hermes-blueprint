import json
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
try:
    from hermes_engine.memory import MemoryStore
    from hermes_engine.skills import SkillManager
    from hermes_engine.session_db import load_canonical_transcript_steps
except ImportError:
    from mcp.hermes_engine.memory import MemoryStore
    from mcp.hermes_engine.skills import SkillManager
    from mcp.hermes_engine.session_db import load_canonical_transcript_steps

NEGATION_TOKENS: Set[str] = {
    "tidak", "jangan", "bukan", "tanpa",
    "no", "not", "never", "don't", "dont", "cannot", "cant"
}

def _tokenize(text: str) -> Set[str]:
    return set(re.findall(r'\w+', text.lower()))

def _has_negation_conflict(tokens_a: Set[str], tokens_b: Set[str]) -> bool:
    neg_a = tokens_a & NEGATION_TOKENS
    neg_b = tokens_b & NEGATION_TOKENS
    return neg_a != neg_b

def _jaccard(set_a: Set[str], set_b: Set[str]) -> float:
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)

def is_duplicate_preference(new_pref: str, existing_pref: str, threshold: float = 0.85) -> bool:
    # Layer 1: Exact normalized equality
    norm_new = re.sub(r'[^\w\s]', '', new_pref.lower()).strip()
    norm_old = re.sub(r'[^\w\s]', '', existing_pref.lower()).strip()
    if norm_new == norm_old:
        return True

    # Layer 2: Token-set Jaccard with negation conflict guard
    tokens_new = _tokenize(new_pref)
    tokens_old = _tokenize(existing_pref)
    if _has_negation_conflict(tokens_new, tokens_old):
        return False
    return _jaccard(tokens_new, tokens_old) >= threshold

# Anti-bloat / Do-not-capture block filters
TRIVIAL_BINARIES = {
    "ls", "pwd", "cd", "echo", "cat", "head", "tail", "grep",
    "which", "test", "true", "false", "find", "sleep", "whoami",
    "touch", "mkdir"
}

INFORMATIONAL_TOOLS = {
    "view_file", "list_dir", "grep_search", "find_by_name",
    "read_url_content", "send_message", "manage_task", "ask_question",
    "schedule", "read_resource", "list_resources"
}

PREFERENCE_KEYWORDS = re.compile(
    r"\b(selalu|always|prefer|biasakan|mulai sekarang|from now on|tolong ingat|please remember|preferensi|my preference|jangan pernah|never|lebih suka|lebih memilih|prioritaskan|biasanya saya)\b",
    re.IGNORECASE
)

LEAD_FILLER_PATTERN = re.compile(
    r"^(tolong\s+(ingat[,\s]*|catat[,\s]*|pastikan[,\s]*)|"
    r"please\s+(remember\s+to[,\s]*|remember[,\s]*|note[,\s]*|ensure\s+that[,\s]*)|"
    r"mulai\s+sekarang[,\s]*|"
    r"from\s+now\s+on[,\s]*|"
    r"ingat[,\s]+|"
    r"remember[,\s]+|"
    r"catat[,\s]+)",
    re.IGNORECASE
)

INTENT_STOP_WORDS = {
    "buat", "bikin", "jalankan", "tolong", "mohon", "cara", "script",
    "create", "make", "run", "setup", "how", "to", "a", "an", "the",
    "for", "of", "in", "on", "please"
}


class SessionReflector:
    def __init__(self, memory_store: MemoryStore, skill_manager: SkillManager):
        self.memory_store = memory_store
        self.skill_manager = skill_manager

    @staticmethod
    def _extract_user_prompt_content(step: Dict[str, Any]) -> str:
        content = step.get("content", "")
        if not isinstance(content, str) or not content.strip():
            return ""

        # Extract all <USER_REQUEST> blocks
        matches = re.findall(r'<USER_REQUEST[^>]*>(.*?)</USER_REQUEST>', content, re.DOTALL | re.IGNORECASE)
        if matches:
            return "\n".join(m.strip() for m in matches if m.strip())

        # Fallback: clean all variations of tags
        cleaned = re.sub(
            r'<(ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|CONTEXT_SUMMARY|USER_REQUEST)[^>]*>.*?</\1>',
            '',
            content,
            flags=re.DOTALL | re.IGNORECASE
        )
        cleaned = re.sub(r'</?USER_REQUEST[^>]*>', '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def extract_user_preferences(self, transcript_data: List[Dict[str, Any]], deadline: Optional[float] = None) -> List[str]:
        preferences: List[str] = []
        seen_lower: set = set()

        for item in transcript_data:
            if deadline and time.monotonic() > deadline:
                break
            if not isinstance(item, dict):
                continue

            item_type = item.get("type", "")
            role = item.get("role", "")
            source = item.get("source", "")

            # Filter for user messages
            if item_type not in ("USER_INPUT", "user") and role != "user" and source != "USER_EXPLICIT":
                continue

            content = item.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue

            cleaned_content = self._extract_user_prompt_content(item)

            # Filter out quotes, markdown headings, and bot confirmation phrases line by line
            valid_lines = []
            for line in cleaned_content.splitlines():
                l = line.strip()
                if not l:
                    continue
                # Ignore lines starting with markdown quote or headers
                if l.startswith(("> ", ">", "#")):
                    continue
                # Ignore agent status phrases, memory snapshots, or confirmation logs
                if any(ignored in l for ignored in [
                    "[PERSISTENT MEMORY SNAPSHOT]",
                    "tersimpan di memori",
                    "preferensi baru tersimpan",
                    "User Profile & Preferences",
                    "Workspace Rules",
                    "Code Diff Preference"
                ]):
                    continue
                # If line is a bullet item, strip leading marker but preserve content
                if l.startswith(("- ", "* ", "• ")):
                    l = re.sub(r"^[-*•]\s+", "", l)
                    if l.startswith("**"):
                        continue
                valid_lines.append(l)

            cleaned_content = "\n".join(valid_lines)
            if not cleaned_content.strip():
                continue

            # Split into sentence candidates safely without breaking decimal numbers (e.g. 3.11)
            sentences = re.split(r"[\n;]+|(?<!\d)\.(?:\s+|$)|[!?]+(?:\s+|$)", cleaned_content)
            for raw_s in sentences:
                s = raw_s.strip()
                if len(s) < 8 or len(s) > 300 or s.endswith(":"):
                    continue

                if not PREFERENCE_KEYWORDS.search(s):
                    continue

                # Anti-bloat: skip if reporting transient errors or stack traces
                if re.search(r"\b(error|exception|stack trace|exit code \d+|failed with)\b", s, re.IGNORECASE):
                    continue

                # Clean leading conversational filler
                cleaned = LEAD_FILLER_PATTERN.sub("", s).strip()
                cleaned = cleaned.lstrip(",:;- ").strip()
                if not cleaned or len(cleaned) < 5 or cleaned.endswith(":"):
                    continue

                # Capitalize first letter
                cleaned = cleaned[0].upper() + cleaned[1:]

                norm_key = cleaned.lower()
                if norm_key not in seen_lower:
                    seen_lower.add(norm_key)
                    preferences.append(cleaned)

        return preferences

    def extract_procedural_learnings(self, transcript_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # In authentic Hermes Agent, procedural distillation requires an LLM evaluation
        # pass or explicit skill-creator invocation, not shallow regex over arbitrary exit code 0 commands.
        # Naive deterministic auto-minting is disabled to prevent skill spam.
        return []

    def _derive_skill_metadata(self, intent: str, binary: str, command: str) -> tuple[str, str, str]:
        words = []
        if intent:
            clean_intent = re.sub(r"[^a-zA-Z0-9\s]", " ", intent)
            tokens = clean_intent.lower().split()
            words = [w for w in tokens if w not in INTENT_STOP_WORDS and len(w) > 1]

        if not words:
            clean_binary = re.sub(r"[^a-zA-Z0-9]", "-", binary).strip("-").lower()
            skill_name = f"{clean_binary}-recipe"
            title = f"{clean_binary.title()} Recipe"
            desc = f"Execute verified {clean_binary} procedure."
        else:
            skill_name = "-".join(words[:4])
            skill_name = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_name).strip("-")[:35].strip("-")
            title = " ".join(words[:4]).title()
            desc = f"Procedure to {' '.join(words[:4])}."

        if not skill_name:
            skill_name = f"{binary}-task"

        # Hard constraint: description <= 60 characters
        if len(desc) > 60:
            desc = desc[:57] + "..."

        return skill_name, title, desc

    def reflect_session(
        self,
        transcript_path: Optional[Path] = None,
        deadline: Optional[float] = None,
        steps: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        path = Path(transcript_path) if transcript_path else None
        try:
            if steps is not None:
                transcript_data = steps
            else:
                if not path or not path.exists():
                    return {
                        "success": False,
                        "error": f"Transcript path '{path}' not found.",
                        "memories_added": [],
                        "skills_created": []
                    }
                transcript_data = load_canonical_transcript_steps(path)

            if not transcript_data:
                return {
                    "success": True,
                    "memories_added": [],
                    "skills_created": [],
                    "preferences_extracted": [],
                    "learnings_extracted": []
                }

            # 1. User Preferences Extraction & Storage
            extracted_prefs = self.extract_user_preferences(transcript_data, deadline=deadline)
            self.memory_store.load()
            existing_user_entries = self.memory_store.get_entries("user")

            memories_added: List[str] = []
            for pref in extracted_prefs:
                if deadline and time.monotonic() > deadline:
                    break
                if any(is_duplicate_preference(pref, ex) for ex in existing_user_entries):
                    continue

                res = self.memory_store.add("user", pref)
                if res.get("success"):
                    memories_added.append(pref)
                    existing_user_entries.append(pref)

            # 2. Procedural Learnings & Skill Distillation
            skills_created: List[str] = []
            extracted_learnings: List[Dict[str, Any]] = []
            if not (deadline and time.monotonic() > deadline):
                extracted_learnings = self.extract_procedural_learnings(transcript_data)
                for learning in extracted_learnings:
                    if deadline and time.monotonic() > deadline:
                        break
                    name = learning["name"]
                    content = learning["content"]
                    res = self.skill_manager.create(name, content, origin="background_review")
                    if res.get("success"):
                        skills_created.append(name)

            return {
                "success": True,
                "memories_added": memories_added,
                "skills_created": skills_created,
                "preferences_extracted": extracted_prefs,
                "learnings_extracted": extracted_learnings
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "memories_added": [],
                "skills_created": []
            }

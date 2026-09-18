import pytest
import json
import subprocess
import sys
from pathlib import Path
from hermes_engine.session_db import SessionDB

def test_pre_invocation_injects_memory(tmp_path, monkeypatch):
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    user_file.write_text("User is Python expert.", encoding="utf-8")
    mem_file.write_text("Always run pytest before commit.", encoding="utf-8")

    hook_script = Path(__file__).parent.parent / "hermes_engine" / "hooks" / "pre_invocation.py"
    proc = subprocess.run(
        [sys.executable, str(hook_script), "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps({"invocationNum": 0}),
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "injectSteps" in data
    ephemeral = data["injectSteps"][0]["ephemeralMessage"]
    assert "User is Python expert." in ephemeral
    assert "Always run pytest before commit." in ephemeral

def test_pre_invocation_empty_memory(tmp_path):
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"

    hook_script = Path(__file__).parent.parent / "hermes_engine" / "hooks" / "pre_invocation.py"
    proc = subprocess.run(
        [sys.executable, str(hook_script), "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps({"invocationNum": 0}),
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data == {"injectSteps": []}

def test_pre_invocation_module_invocation(tmp_path):
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    user_file.write_text("User profile info.", encoding="utf-8")
    repo_root = Path(__file__).resolve().parent.parent

    proc = subprocess.run(
        [sys.executable, "-m", "hermes_engine.hooks.pre_invocation", "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps({"invocationNum": 0}),
        text=True,
        capture_output=True,
        cwd=str(repo_root)
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "injectSteps" in data
    assert "User profile info." in data["injectSteps"][0]["ephemeralMessage"]

def test_stop_hook_triggers_ingest(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "USER_INPUT", "content": "Test Stop Hook Ingestion"}), encoding="utf-8")
    db_file = tmp_path / "state.db"

    hook_script = Path(__file__).parent.parent / "hermes_engine" / "hooks" / "stop_hook.py"
    proc = subprocess.run(
        [sys.executable, str(hook_script), "--db-path", str(db_file)],
        input=json.dumps({"conversationId": "test-sess", "workspacePaths": [str(tmp_path)], "transcriptPath": str(transcript)}),
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    assert db_file.exists()

    db = SessionDB(db_file)
    results = db.search("Ingestion")
    assert len(results) == 1
    assert results[0]["session_id"] == "test-sess"
    assert "Test Stop Hook" in results[0]["snippet"]
    assert "<b>Ingestion</b>" in results[0]["snippet"]

def test_stop_hook_handles_missing_transcript_or_empty_input(tmp_path):
    db_file = tmp_path / "state.db"
    hook_script = Path(__file__).parent.parent / "hermes_engine" / "hooks" / "stop_hook.py"

    # Empty stdin
    proc = subprocess.run(
        [sys.executable, str(hook_script), "--db-path", str(db_file)],
        input="",
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"decision": "allow"}

    # Missing transcript path
    proc2 = subprocess.run(
        [sys.executable, str(hook_script), "--db-path", str(db_file)],
        input=json.dumps({"conversationId": "test-sess", "transcriptPath": "/nonexistent/transcript.jsonl"}),
        text=True,
        capture_output=True
    )
    assert proc2.returncode == 0
    assert json.loads(proc2.stdout) == {"decision": "allow"}

def test_stop_hook_module_invocation(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "USER_INPUT", "content": "Module Ingestion Test"}), encoding="utf-8")
    db_file = tmp_path / "state.db"
    repo_root = Path(__file__).resolve().parent.parent

    proc = subprocess.run(
        [sys.executable, "-m", "hermes_engine.hooks.stop_hook", "--db-path", str(db_file)],
        input=json.dumps({"conversationId": "mod-sess", "workspacePaths": [str(tmp_path)], "transcriptPath": str(transcript)}),
        text=True,
        capture_output=True,
        cwd=str(repo_root)
    )
    assert proc.returncode == 0
    assert db_file.exists()

    db = SessionDB(db_file)
    results = db.search("Module")
    assert len(results) == 1
    assert results[0]["session_id"] == "mod-sess"

def test_stop_hook_triggers_reflection(tmp_path):
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    skills_dir = tmp_path / "skills"
    usage_file = skills_dir / ".usage.json"
    db_file = tmp_path / "state.db"
    user_file.write_text("# User Profile\n", encoding="utf-8")
    mem_file.write_text("# Workspace Rules\n", encoding="utf-8")

    transcript = tmp_path / "transcript.jsonl"
    events = [
        {"type": "USER_INPUT", "content": "mulai sekarang selalu format markdown dengan bullet point"},
        {"type": "PLANNER_RESPONSE", "content": "Dimengerti."}
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

    payload = {
        "transcriptPath": str(transcript),
        "conversationId": "test-reflect-stop",
        "workspacePaths": [str(tmp_path)]
    }

    hook_script = Path(__file__).parent.parent / "hermes_engine" / "hooks" / "stop_hook.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(hook_script),
            "--db-path", str(db_file),
            "--user-path", str(user_file),
            "--memory-path", str(mem_file),
            "--skills-dir", str(skills_dir),
            "--usage-path", str(usage_file),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    res = json.loads(proc.stdout)
    assert res == {"decision": "allow"}

    # Ingestion into DB verified
    assert db_file.exists()
    db = SessionDB(db_file)
    results = db.search("bullet")
    assert len(results) >= 1

    # Reflection into memory verified
    updated_user = user_file.read_text(encoding="utf-8")
    assert "Selalu format markdown dengan bullet point" in updated_user

def test_stop_hook_triggers_skill_distillation(tmp_path):
    skills_dir = tmp_path / "skills"
    usage_file = skills_dir / ".usage.json"
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    db_file = tmp_path / "state.db"

    transcript = tmp_path / "transcript.jsonl"
    events = [
        {"type": "USER_INPUT", "content": "tolong buat backup database state.db"},
        {
            "type": "PLANNER_RESPONSE",
            "content": "Executing backup...",
            "tool_calls": [
                {
                    "name": "run_command",
                    "arguments": {"CommandLine": "sqlite3 state.db .dump > backup.sql"}
                }
            ]
        },
        {
            "type": "TOOL_RESULT",
            "content": "exit code 0\nDump completed."
        }
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

    payload = {
        "transcriptPath": str(transcript),
        "conversationId": "test-skill-distill",
        "workspacePaths": [str(tmp_path)]
    }

    hook_script = Path(__file__).parent.parent / "hermes_engine" / "hooks" / "stop_hook.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(hook_script),
            "--db-path", str(db_file),
            "--user-path", str(user_file),
            "--memory-path", str(mem_file),
            "--skills-dir", str(skills_dir),
            "--usage-path", str(usage_file),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    res = json.loads(proc.stdout)
    assert res == {"decision": "allow"}

    # Verify shallow auto-minting is disabled (skill is not created for random 1-line commands)
    skill_file = skills_dir / "backup-database-state-db" / "SKILL.md"
    assert not skill_file.exists()


def test_stop_hook_failsafe_on_reflection_exception(tmp_path):
    db_file = tmp_path / "state.db"
    transcript = tmp_path / "transcript.jsonl"
    events = [
        {"type": "USER_INPUT", "content": "selalu gunakan tabs"},
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

    bad_skills_dir = tmp_path / "not_a_dir"
    bad_skills_dir.write_text("plain file", encoding="utf-8")

    payload = {
        "transcriptPath": str(transcript),
        "conversationId": "test-failsafe",
        "workspacePaths": [str(tmp_path)]
    }

    hook_script = Path(__file__).parent.parent / "hermes_engine" / "hooks" / "stop_hook.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(hook_script),
            "--db-path", str(db_file),
            "--skills-dir", str(bad_skills_dir / "nested"),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    res = json.loads(proc.stdout)
    assert res == {"decision": "allow"}

def test_hooks_json_configuration():
    repo_root = Path(__file__).resolve().parent.parent
    hooks_file = repo_root / "templates" / "hooks.json.example"
    assert hooks_file.exists(), "templates/hooks.json.example must exist in repo root"

    data = json.loads(hooks_file.read_text(encoding="utf-8"))
    assert "hermes-memory-injector" in data
    assert "hermes-session-stop" in data

    pre_cmd = data["hermes-memory-injector"]["PreInvocation"][0]["command"]
    stop_cmd = data["hermes-session-stop"]["Stop"][0]["command"]

    assert "pre_invocation.py" in pre_cmd
    assert "stop_hook.py" in stop_cmd

    # Verify target scripts exist in repository
    pre_path = repo_root / "hermes_engine" / "hooks" / "pre_invocation.py"
    stop_path = repo_root / "hermes_engine" / "hooks" / "stop_hook.py"
    assert pre_path.exists()
    assert stop_path.exists()

def test_hooks_execute_from_external_cwd(tmp_path):
    # Verify pre_invocation and stop_hook can run when cwd is completely unrelated (e.g. tmp_path)
    hook_script = Path(__file__).parent.parent / "hermes_engine" / "hooks" / "pre_invocation.py"
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    user_file.write_text("External cwd test.", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(hook_script), "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps({"invocationNum": 0}),
        text=True,
        capture_output=True,
        cwd=str(tmp_path)
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "External cwd test." in data["injectSteps"][0]["ephemeralMessage"]

def test_pre_invocation_skips_subsequent_invocations(tmp_path):
    hook_script = Path(__file__).parent.parent / "hermes_engine" / "hooks" / "pre_invocation.py"
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    user_file.write_text("User profile content.", encoding="utf-8")

    # Initial invocation (invocationNum: 0) MUST inject memory snapshot
    proc0 = subprocess.run(
        [sys.executable, str(hook_script), "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps({"invocationNum": 0}),
        text=True,
        capture_output=True,
        cwd=str(tmp_path)
    )
    assert proc0.returncode == 0
    data0 = json.loads(proc0.stdout)
    assert len(data0.get("injectSteps", [])) == 1

    # Second invocation in the same turn (invocationNum: 1) MUST skip injection to prevent token bloat
    proc1 = subprocess.run(
        [sys.executable, str(hook_script), "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps({"invocationNum": 1}),
        text=True,
        capture_output=True,
        cwd=str(tmp_path)
    )
    assert proc1.returncode == 0
    data1 = json.loads(proc1.stdout)
    assert data1.get("injectSteps") == []

    # Subsequent invocation (invocationNum: 2) must also skip
    proc2 = subprocess.run(
        [sys.executable, str(hook_script), "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps({"invocationNum": 2}),
        text=True,
        capture_output=True,
        cwd=str(tmp_path)
    )
    assert proc2.returncode == 0
    data2 = json.loads(proc2.stdout)
    assert data2.get("injectSteps") == []


def test_pre_invocation_null_safe_invocation_guard(tmp_path):
    hook_script = Path(__file__).resolve().parent.parent / "hermes_engine" / "hooks" / "pre_invocation.py"
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    user_file.write_text("User profile content.", encoding="utf-8")

    # Case 1: invocationNum is explicit null / None -> must not raise TypeError and must inject
    proc_null = subprocess.run(
        [sys.executable, str(hook_script), "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps({"invocationNum": None}),
        text=True,
        capture_output=True,
        cwd=str(tmp_path)
    )
    assert proc_null.returncode == 0
    data_null = json.loads(proc_null.stdout)
    assert len(data_null.get("injectSteps", [])) == 1

    # Case 2: payload is a JSON array instead of dict -> must not raise AttributeError
    proc_arr = subprocess.run(
        [sys.executable, str(hook_script), "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps([1, 2, 3]),
        text=True,
        capture_output=True,
        cwd=str(tmp_path)
    )
    assert proc_arr.returncode == 0
    data_arr = json.loads(proc_arr.stdout)
    assert len(data_arr.get("injectSteps", [])) == 1

    # Case 3: invocationNum is non-integer string -> must handle gracefully
    proc_str = subprocess.run(
        [sys.executable, str(hook_script), "--user-path", str(user_file), "--memory-path", str(mem_file)],
        input=json.dumps({"invocationNum": "invalid"}),
        text=True,
        capture_output=True,
        cwd=str(tmp_path)
    )
    assert proc_str.returncode == 0


def test_stop_hook_cooperative_timeout(tmp_path):
    transcript_file = tmp_path / "huge_transcript.jsonl"
    lines = [json.dumps({"step_index": i, "type": "GENERIC", "content": "data"}) for i in range(100)]
    transcript_file.write_text("\n".join(lines))

    db_file = tmp_path / "state.db"
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    from hermes_engine.hooks.stop_hook import run_stop_hook
    # Passing 1ms budget ensures timeout triggered cooperatively
    result = run_stop_hook(
        str(transcript_file), "conv_timeout", str(tmp_path),
        db_path=db_file, user_path=user_file, memory_path=mem_file,
        skills_dir=skills_dir, max_time_seconds=0.001
    )
    assert result.get("decision") == "allow"


def test_pre_invocation_string_invocation_number():
    from hermes_engine.hooks.pre_invocation import run_pre_invocation_hook
    # String "2" must be treated as second invocation (inv_num > 0 -> skip injection)
    hook_input = {"invocationNum": "2"}
    output = run_pre_invocation_hook(hook_input)
    assert output == {"injectSteps": []}


def test_pre_invocation_failsafe_on_store_exception(monkeypatch):
    from hermes_engine.hooks.pre_invocation import run_pre_invocation_hook
    import hermes_engine.hooks.pre_invocation as pre_mod

    class CrashingStore:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Disk failure")

    monkeypatch.setattr(pre_mod, "MemoryStore", CrashingStore)
    output = run_pre_invocation_hook({"invocationNum": 1})
    assert output == {"injectSteps": []}

    output0 = run_pre_invocation_hook({"invocationNum": 0})
    assert output0 == {"injectSteps": []}


def test_stop_hook_single_pass_transcript_parsing(tmp_path, monkeypatch):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "USER_INPUT", "content": "Single pass test"}), encoding="utf-8")
    db_file = tmp_path / "state.db"

    import hermes_engine.hooks.stop_hook as stop_mod
    from hermes_engine.hooks.stop_hook import run_stop_hook

    load_count = 0
    orig_load = stop_mod.load_canonical_transcript_steps

    def spy_load(*args, **kwargs):
        nonlocal load_count
        load_count += 1
        return orig_load(*args, **kwargs)

    monkeypatch.setattr(stop_mod, "load_canonical_transcript_steps", spy_load)

    res = run_stop_hook(
        transcript=transcript,
        conv_id="test-single-pass",
        workspace=str(tmp_path),
        db_path=db_file,
    )
    assert res == {"decision": "allow"}
    assert load_count == 1




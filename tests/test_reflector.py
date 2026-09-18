import json
import pytest
from pathlib import Path
from hermes_engine.memory import MemoryStore
from hermes_engine.skills import SkillManager
from hermes_engine.reflector import SessionReflector

@pytest.fixture
def reflector_setup(tmp_path):
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    skills_dir = tmp_path / "skills"
    usage_file = skills_dir / ".usage.json"
    mem_store = MemoryStore(user_file, mem_file)
    skill_mgr = SkillManager(skills_dir, usage_file)
    return SessionReflector(mem_store, skill_mgr), mem_store, skill_mgr, tmp_path

@pytest.fixture
def reflector(reflector_setup):
    return reflector_setup[0]

def test_extract_user_preference_from_transcript(reflector_setup):
    reflector, mem_store, _, tmp_path = reflector_setup
    transcript = tmp_path / "transcript.jsonl"
    events = [
        {"type": "USER_INPUT", "content": "tolong ingat, selalu gunakan flag --preview saat deploy"},
        {"type": "PLANNER_RESPONSE", "content": "Siap, akan selalu gunakan flag --preview."}
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events))

    result = reflector.reflect_session(transcript)
    assert result["success"] is True
    assert len(result["memories_added"]) >= 1
    snapshot = mem_store.render_snapshot()
    assert "--preview" in snapshot

def test_extract_procedural_skill_from_transcript(reflector_setup):
    reflector, _, skill_mgr, tmp_path = reflector_setup
    transcript = tmp_path / "transcript.jsonl"
    events = [
        {"type": "USER_INPUT", "content": "buat script backup database postgres"},
        {
            "type": "PLANNER_RESPONSE",
            "content": "Running backup command",
            "tool_calls": [
                {"name": "run_command", "arguments": {"CommandLine": "pg_dump -U postgres dbname > backup.sql"}}
            ]
        },
        {"type": "TOOL_RESULT", "content": "Exit code 0. Backup complete."}
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events))

    result = reflector.reflect_session(transcript)
    assert result["success"] is True
    # Naive deterministic auto-minting of 1-line bash commands is disabled to prevent skill spam
    assert len(result.get("skills_created", [])) == 0


def test_reflector_handles_empty_transcript(reflector_setup):
    reflector, _, _, tmp_path = reflector_setup
    transcript = tmp_path / "empty.jsonl"
    transcript.write_text("")

    result = reflector.reflect_session(transcript)
    assert result["success"] is True
    assert result["memories_added"] == []

def test_reflector_handles_nonexistent_transcript(reflector_setup):
    reflector, _, _, tmp_path = reflector_setup
    non_existent = tmp_path / "does_not_exist.jsonl"

    result = reflector.reflect_session(non_existent)
    assert result["success"] is False
    assert "not found" in result["error"].lower()

def test_reflector_handles_corrupted_jsonl(reflector_setup):
    reflector, mem_store, _, tmp_path = reflector_setup
    transcript = tmp_path / "corrupt.jsonl"
    corrupt_content = "\n".join([
        "{invalid json line",
        json.dumps({"type": "USER_INPUT", "content": "selalu sertakan unit test untuk setiap fungsi"}),
        "another bad line}{",
        json.dumps({"type": "PLANNER_RESPONSE", "content": "Baik."})
    ])
    transcript.write_text(corrupt_content)

    result = reflector.reflect_session(transcript)
    assert result["success"] is True
    assert len(result["memories_added"]) >= 1
    snapshot = mem_store.render_snapshot()
    assert "unit test" in snapshot

def test_extract_user_preferences_direct(reflector_setup):
    reflector, _, _, _ = reflector_setup
    events = [
        {"type": "USER_INPUT", "content": "mulai sekarang selalu gunakan Python 3.11"},
        {"type": "PLANNER_RESPONSE", "content": "Oke."},
        {"type": "USER_INPUT", "content": "prefer gunakan pytest daripada unittest"},
        {"type": "USER_INPUT", "content": "hanya pertanyaan biasa tentang cuaca"}
    ]
    prefs = reflector.extract_user_preferences(events)
    assert len(prefs) == 2
    assert any("Python 3.11" in p for p in prefs)
    assert any("pytest" in p for p in prefs)

def test_filter_xml_tags_and_quotes_and_bullets(reflector_setup):
    reflector, _, _, _ = reflector_setup
    events = [
        {"type": "USER_INPUT", "content": "<USER_SETTINGS_CHANGE> If reporting what model you are, please use a human readable name instead of the exact string </USER_SETTINGS_CHANGE>"},
        {"type": "USER_INPUT", "content": "> Tolong simpan preferensi baru ke memory user: 'Saya selalu ingin diff kode'\nPreferensi baru tersimpan di memori user (USER.md):\n* **Update Existing (Prefer)**: Update an active Rule/Skill\n• Code Diff Preference: Selalu tampilkan diff kode dalam unified context 5 baris (-U5)"},
        {"type": "USER_INPUT", "content": "Saya selalu ingin format response dalam Bahasa Indonesia yang ringkas"}
    ]
    prefs = reflector.extract_user_preferences(events)
    # Only the genuine user sentence should be captured, not XML tags, quotes, bullets, or confirmations
    assert len(prefs) == 1
    assert "Bahasa Indonesia yang ringkas" in prefs[0]

def test_filter_xml_tags_with_attributes(reflector_setup):
    reflector, _, _, _ = reflector_setup
    events = [
        {"type": "USER_INPUT", "content": '<USER_SETTINGS_CHANGE timestamp="2026-09-14T18:30:00Z" origin="system"> If reporting what model you are, please use a human readable name </USER_SETTINGS_CHANGE>'},
        {"type": "USER_INPUT", "content": '<additional_metadata source="agent-runtime">\nprefer gunakan bahasa indonesia\n</additional_metadata>'},
        {"type": "USER_INPUT", "content": "Tolong ingat bahwa saya selalu ingin kode diformat dengan black"}
    ]
    prefs = reflector.extract_user_preferences(events)
    assert len(prefs) == 1
    assert "diformat dengan black" in prefs[0]

def test_extract_user_preferences_from_bullet_lists(reflector_setup):
    reflector, _, _, _ = reflector_setup
    events = [
        {
            "type": "USER_INPUT",
            "content": "<USER_REQUEST>\nTolong catat preferensi saya:\n- Saya selalu menggunakan pytest untuk unit testing\n* Prefer gunakan uv daripada pip\n• Selalu sertakan type hints pada fungsi python\n</USER_REQUEST>"
        }
    ]
    prefs = reflector.extract_user_preferences(events)
    assert len(prefs) == 3
    assert any("pytest untuk unit testing" in p for p in prefs)
    assert any("uv daripada pip" in p for p in prefs)
    assert any("type hints pada fungsi python" in p for p in prefs)


def test_deduplication_of_existing_user_preferences(reflector_setup):
    reflector, mem_store, _, tmp_path = reflector_setup
    # Pre-populate memory
    mem_store.add("user", "Selalu gunakan flag --preview saat deploy")

    transcript = tmp_path / "dup.jsonl"
    events = [
        {"type": "USER_INPUT", "content": "tolong ingat, selalu gunakan flag --preview saat deploy"},
        {"type": "PLANNER_RESPONSE", "content": "Sudah dicatat."}
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events))

    result = reflector.reflect_session(transcript)
    assert result["success"] is True
    # Should not add duplicate
    assert len(result["memories_added"]) == 0

def test_anti_bloat_guardrails_on_procedural_learnings(reflector_setup):
    reflector, _, skill_mgr, _ = reflector_setup
    # Trivial command (ls) and failed command (exit code 1)
    events = [
        {"type": "USER_INPUT", "content": "cek file di direktori"},
        {
            "type": "PLANNER_RESPONSE",
            "content": "Listing files",
            "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "ls -la"}}]
        },
        {"type": "TOOL_RESULT", "content": "Exit code 0. total 0"},
        {"type": "USER_INPUT", "content": "compile binary C++"},
        {
            "type": "PLANNER_RESPONSE",
            "content": "Compiling",
            "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "g++ main.cpp -o app"}}]
        },
        {"type": "TOOL_RESULT", "content": "Exit code 1. fatal error: iostream: No such file"}
    ]
    learnings = reflector.extract_procedural_learnings(events)
    assert len(learnings) == 0

def test_memory_character_limit_graceful_handling(reflector_setup):
    reflector, mem_store, _, tmp_path = reflector_setup
    # Fill user memory near the 1375 limit
    padding = "A" * 1350
    mem_store.add("user", padding)

    transcript = tmp_path / "overflow.jsonl"
    events = [
        {"type": "USER_INPUT", "content": "selalu gunakan flag --extra-long-flag-that-will-exceed-the-user-limit-budget"}
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events))

    # Must not raise an unhandled exception
    result = reflector.reflect_session(transcript)
    assert result["success"] is True
    assert result["memories_added"] == []

def test_generated_skill_format_and_provenance(reflector_setup):
    reflector, _, skill_mgr, _ = reflector_setup
    skill_name, title, desc = reflector._derive_skill_metadata(
        "buatkan prosedur automasi kompresi video mp4 menggunakan ffmpeg resolusi 1080p",
        "ffmpeg",
        "ffmpeg -i input.mov output.mp4"
    )
    assert len(desc) <= 60
    assert "ffmpeg" in skill_name or "video" in skill_name or "prosedur" in skill_name
    # Create via skill manager with background_review origin
    res = skill_mgr.create(
        skill_name,
        f"---\nname: {skill_name}\ndescription: {desc}\n---\n# {title}\n",
        origin="background_review"
    )
    assert res["success"] is True
    assert skill_mgr.is_agent_created(skill_name) is True


def test_missing_or_empty_tool_result_ignored(reflector_setup):
    reflector, _, _, _ = reflector_setup
    # Cut-off transcript without TOOL_RESULT
    events = [
        {"type": "USER_INPUT", "content": "jalankan database migration"},
        {
            "type": "PLANNER_RESPONSE",
            "content": "Executing migration command",
            "tool_calls": [
                {"name": "run_command", "arguments": {"CommandLine": "alembic upgrade head"}}
            ]
        }
    ]
    learnings = reflector.extract_procedural_learnings(events)
    assert len(learnings) == 0

    # TOOL_RESULT with empty/whitespace content
    events_empty_res = [
        {"type": "USER_INPUT", "content": "jalankan database migration"},
        {
            "type": "PLANNER_RESPONSE",
            "content": "Executing migration command",
            "tool_calls": [
                {"name": "run_command", "arguments": {"CommandLine": "alembic upgrade head"}}
            ]
        },
        {"type": "TOOL_RESULT", "content": "   "}
    ]
    learnings_empty = reflector.extract_procedural_learnings(events_empty_res)
    assert len(learnings_empty) == 0


def test_extract_indonesian_preference_keywords(reflector_setup):
    reflector, _, _, _ = reflector_setup
    events = [
        {"type": "USER_INPUT", "content": "saya lebih suka pakai Agy CLI daripada GUI"},
        {"type": "USER_INPUT", "content": "untuk format file, saya lebih memilih JSON daripada YAML"},
        {"type": "USER_INPUT", "content": "prioritaskan performa rendering font di terminal"}
    ]
    prefs = reflector.extract_user_preferences(events)
    assert len(prefs) == 3
    assert any("lebih suka pakai Agy" in p for p in prefs)
    assert any("lebih memilih JSON" in p for p in prefs)
    assert any("prioritaskan performa rendering" in p.lower() for p in prefs)


def test_reflector_handles_rewound_trajectory(reflector_setup):
    reflector, mem_store, _, tmp_path = reflector_setup
    transcript = tmp_path / "rewound_pref.jsonl"
    # User sends preference in aborted branch (step 2), then rewinds to step 2 and sends different instruction
    lines = [
        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "Halo, mulai sesi"}),
        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "content": "Siap."}),
        json.dumps({"step_index": 2, "type": "USER_INPUT", "content": "selalu gunakan bahasa latin kuno"}), # ABORTED
        json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "content": "Memproses..."}),
        # Rewind to step 2!
        json.dumps({"step_index": 2, "type": "USER_INPUT", "content": "selalu gunakan bahasa indonesia ringkas"}), # CANONICAL
        json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "content": "Siap, menggunakan bahasa indonesia ringkas."})
    ]
    transcript.write_text("\n".join(lines), encoding="utf-8")

    res = reflector.reflect_session(transcript)
    assert res["success"] is True

    user_entries = mem_store.get_entries("user")
    # Must only contain the canonical branch preference, NOT the aborted one!
    assert any("bahasa indonesia ringkas" in e for e in user_entries)
    assert not any("bahasa latin kuno" in e for e in user_entries)


def test_extract_user_preferences_from_antigravity_envelope(reflector):
    transcript = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": (
                "<USER_REQUEST>\n"
                "hapus saja antigravity GUI, saya lebih suka pakai Agy daripada GUI\n"
                "</USER_REQUEST>\n"
                "<ADDITIONAL_METADATA>\n"
                "The current local time is: 2026-09-16T07:26:17+07:00.\n"
                "</ADDITIONAL_METADATA>\n"
                "<USER_SETTINGS_CHANGE>\n"
                "The user changed setting Model Selection from None to Gemini 3.8 Flash (High).\n"
                "</USER_SETTINGS_CHANGE>"
            )
        }
    ]
    prefs = reflector.extract_user_preferences(transcript)
    assert len(prefs) == 1
    assert "lebih suka pakai Agy daripada GUI" in prefs[0]
    assert "<USER_REQUEST>" not in prefs[0]
    assert "ADDITIONAL_METADATA" not in prefs[0]


def test_reflector_cooperative_deadline(reflector_setup):
    import time
    reflector, mem_store, _, tmp_path = reflector_setup
    transcript = tmp_path / "deadline_transcript.jsonl"
    lines = [
        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "selalu gunakan tabs daripada spasi"}),
        json.dumps({"step_index": 1, "type": "USER_INPUT", "content": "selalu tulis type annotations"})
    ]
    transcript.write_text("\n".join(lines), encoding="utf-8")

    # Deadline in the past: should break immediately without persisting preferences
    res = reflector.reflect_session(transcript, deadline=time.monotonic() - 10.0)
    assert res["success"] is True
    assert res["memories_added"] == []


def test_deduplication_two_layer_and_negation_guard():
    from hermes_engine.reflector import is_duplicate_preference
    
    # Layer 1: exact normalized match is duplicate
    assert is_duplicate_preference("Gunakan Python 3.12!", "gunakan python 3.12") is True
    
    # Subsumption does NOT suppress specific detail
    # "Gunakan Python" vs "Gunakan Python 3.12 untuk deep learning"
    assert is_duplicate_preference("Gunakan Python 3.12 untuk deep learning", "Gunakan Python") is False
    
    # High word overlap with opposite negation is NOT duplicate
    pref_positive = "Selalu gunakan Python untuk backend"
    pref_negative = "Jangan gunakan Python untuk backend"
    assert is_duplicate_preference(pref_positive, pref_negative) is False
    assert is_duplicate_preference(pref_negative, pref_positive) is False
    
    # Near identical paraphrase without negation conflict IS duplicate:
    # A: "Selalu gunakan format kode ruff di setiap file python" (9 tokens)
    # B: "Gunakan format kode ruff di setiap file python" (8 tokens)
    # Intersection = 8, Union = 9 -> Jaccard = 8/9 ≈ 0.888 >= 0.85 -> Duplicate
    pref1 = "Selalu gunakan format kode ruff di setiap file python"
    pref2 = "Gunakan format kode ruff di setiap file python"
    assert is_duplicate_preference(pref1, pref2) is True


def test_extract_user_prompt_content_multi_block_user_requests():
    from hermes_engine.reflector import SessionReflector
    step = {
        "content": "<USER_REQUEST>\nFirst prompt part\n</USER_REQUEST>\nIntermediary text\n<USER_REQUEST>\nSecond prompt part\n</USER_REQUEST>"
    }
    extracted = SessionReflector._extract_user_prompt_content(step)
    assert "First prompt part" in extracted
    assert "Second prompt part" in extracted


def test_extract_user_prompt_content_cleans_unclosed_tags():
    from hermes_engine.reflector import SessionReflector
    step = {
        "content": "<USER_REQUEST source=\"cli\">\nUnclosed prompt without closing tag"
    }
    extracted = SessionReflector._extract_user_prompt_content(step)
    assert "<USER_REQUEST" not in extracted
    assert "Unclosed prompt without closing tag" in extracted





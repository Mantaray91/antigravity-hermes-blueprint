import pytest
import json
from pathlib import Path
from hermes_engine.skills import (
    SkillManager,
    set_current_write_origin,
    reset_current_write_origin,
    get_current_write_origin,
)

@pytest.fixture
def skill_mgr(tmp_path):
    skills_dir = tmp_path / "skills"
    usage_file = skills_dir / ".usage.json"
    return SkillManager(skills_dir=skills_dir, usage_path=usage_file)

@pytest.fixture
def skill_manager(skill_mgr):
    return skill_mgr

def test_create_valid_skill(skill_mgr):
    content = "---\nname: my-tool\ndescription: Use when deploying apps. Fast deploy helper.\n---\n# My Tool\n## Procedure\n1. Step one"
    res = skill_mgr.create("my-tool", content, origin="background_review")
    assert res["success"] is True
    skill_file = skill_mgr.skills_dir / "my-tool" / "SKILL.md"
    assert skill_file.exists()
    assert skill_mgr.is_agent_created("my-tool") is True

def test_create_rejects_long_description(skill_mgr):
    content = "---\nname: bad-tool\ndescription: " + ("A" * 65) + "\n---\n# Bad Tool"
    res = skill_mgr.create("bad-tool", content)
    assert res["success"] is False
    assert "description" in res["error"].lower()

def test_rejects_modifying_user_made_skill(skill_mgr):
    # User-made skill not in .usage.json as agent-created
    user_skill = skill_mgr.skills_dir / "user-custom"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("User original code", encoding="utf-8")
    
    res = skill_mgr.patch("user-custom", "User original", "Agent modified", origin="background_review")
    assert res["success"] is False
    assert "user-made" in res["error"].lower()

def test_write_file_ast_validation(skill_mgr):
    skill_mgr.create("py-tool", "---\nname: py-tool\ndescription: Python helper tool.\n---\n# Py")
    # Invalid python AST
    res = skill_mgr.write_file("py-tool", "scripts/helper.py", "def broken_python(: pass")
    assert res["success"] is False
    assert "syntax" in res["error"].lower()
    
    # Valid python AST
    res_valid = skill_mgr.write_file("py-tool", "scripts/helper.py", "def valid_python(): pass\n")
    assert res_valid["success"] is True

def test_patch_success(skill_mgr):
    skill_mgr.create("patch-tool", "---\nname: patch-tool\ndescription: Patch test.\n---\n# V1")
    res = skill_mgr.patch("patch-tool", "# V1", "# V2")
    assert res["success"] is True
    skill_file = skill_mgr.skills_dir / "patch-tool" / "SKILL.md"
    assert "# V2" in skill_file.read_text()

def test_patch_pinned_skill_rejected(skill_mgr):
    skill_mgr.create("pinned-tool", "---\nname: pinned-tool\ndescription: Pinned tool.\n---\n# Pinned", origin="background_review")
    data = skill_mgr._load_usage()
    data["pinned-tool"]["pinned"] = True
    skill_mgr._save_usage(data)

    res = skill_mgr.patch("pinned-tool", "# Pinned", "# Modified", origin="background_review")
    assert res["success"] is False
    assert "pinned" in res["error"].lower()

def test_patch_nonexistent_or_missing_substring(skill_mgr):
    res = skill_mgr.patch("ghost-tool", "old", "new")
    assert res["success"] is False
    assert "does not exist" in res["error"].lower()

    skill_mgr.create("sub-tool", "---\nname: sub-tool\ndescription: Sub tool.\n---\n# Text")
    res2 = skill_mgr.patch("sub-tool", "nonexistent substring", "replacement")
    assert res2["success"] is False
    assert "not found" in res2["error"].lower()

def test_write_file_rejects_user_made_or_nonexistent(skill_mgr):
    # Nonexistent skill
    res = skill_mgr.write_file("ghost-tool", "scripts/test.py", "x = 1")
    assert res["success"] is False
    assert "does not exist" in res["error"].lower()

    # User-made skill
    user_skill = skill_mgr.skills_dir / "user-tool"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("Original", encoding="utf-8")
    res_user = skill_mgr.write_file("user-tool", "scripts/test.py", "x = 1", origin="background_review")
    assert res_user["success"] is False
    assert "user-made" in res_user["error"].lower()

def test_create_refuses_to_overwrite_user_made_skill(skill_mgr):
    user_skill = skill_mgr.skills_dir / "existing-user"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("User original", encoding="utf-8")

    res = skill_mgr.create("existing-user", "---\nname: existing-user\ndescription: Overwrite.\n---\n# New", origin="background_review")
    assert res["success"] is False
    assert "user-made" in res["error"].lower()

def test_skill_name_validation(skill_mgr):
    invalid_names = ["bad name", "bad/slash", "bad..dot", "bad$char", "", "bad@name"]
    for bad_name in invalid_names:
        res_create = skill_mgr.create(bad_name, "---\nname: bad\ndescription: bad\n---\n")
        assert res_create["success"] is False
        assert "Invalid skill name" in res_create["error"]

        res_patch = skill_mgr.patch(bad_name, "old", "new")
        assert res_patch["success"] is False
        assert "Invalid skill name" in res_patch["error"]

        res_write = skill_mgr.write_file(bad_name, "test.py", "x = 1")
        assert res_write["success"] is False
        assert "Invalid skill name" in res_write["error"]

        res_delete = skill_mgr.delete(bad_name)
        assert res_delete["success"] is False
        assert "Invalid skill name" in res_delete["error"]

def test_write_file_symlink_path_traversal(skill_mgr, tmp_path):
    skill_mgr.create("sec-tool", "---\nname: sec-tool\ndescription: Security test.\n---\n# Sec")
    
    # Create outside directory and symlink inside skill directory
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    symlink_dir = skill_mgr.skills_dir / "sec-tool" / "symlink_dir"
    symlink_dir.symlink_to(outside_dir, target_is_directory=True)

    # Attempting to write through symlink escaping skill directory should be rejected
    res = skill_mgr.write_file("sec-tool", "symlink_dir/escaped.py", "print('hacked')\n")
    assert res["success"] is False
    assert "Path traversal detected" in res["error"]
    assert not (outside_dir / "escaped.py").exists()

def test_delete_skill(skill_mgr):
    skill_mgr.create("del-tool", "---\nname: del-tool\ndescription: To delete.\n---\n# Del", origin="background_review")
    assert (skill_mgr.skills_dir / "del-tool").exists()
    assert skill_mgr.is_agent_created("del-tool") is True

    # Successful delete
    res = skill_mgr.delete("del-tool")
    assert res["success"] is True
    assert not (skill_mgr.skills_dir / "del-tool").exists()
    assert skill_mgr.is_agent_created("del-tool") is False

    # Delete nonexistent
    res_ghost = skill_mgr.delete("ghost-tool")
    assert res_ghost["success"] is False

    # Refuse delete user-made skill
    user_skill = skill_mgr.skills_dir / "user-kept"
    user_skill.mkdir()
    (user_skill / "SKILL.md").write_text("User", encoding="utf-8")
    res_user = skill_mgr.delete("user-kept", origin="background_review")
    assert res_user["success"] is False
    assert "user-made" in res_user["error"].lower()

def test_foreground_can_create_and_modify_user_skill(skill_mgr):
    # Foreground creates a user skill
    res = skill_mgr.create("my-custom-tool", "---\nname: my-custom-tool\ndescription: A user tool\n---\nBody v1", origin="foreground")
    assert res["success"] is True
    assert not skill_mgr.is_agent_created("my-custom-tool")

    # Foreground can patch its own skill
    res_patch = skill_mgr.patch("my-custom-tool", "Body v1", "Body v2", origin="foreground")
    assert res_patch["success"] is True
    assert "Body v2" in (skill_mgr.skills_dir / "my-custom-tool" / "SKILL.md").read_text()

    # Foreground can write files into user-made skill
    res_file = skill_mgr.write_file("my-custom-tool", "scripts/run.py", "print('hello')", origin="foreground")
    assert res_file["success"] is True

def test_background_review_blocked_on_user_skill(skill_mgr):
    # Create user skill
    skill_mgr.create("user-skill", "---\nname: user-skill\ndescription: User skill\n---\nUser body", origin="foreground")

    # Background review cannot patch user-made skill
    res_patch = skill_mgr.patch("user-skill", "User body", "Hacked body", origin="background_review")
    assert res_patch["success"] is False
    assert "user-owned" in res_patch["error"].lower() or "user-made" in res_patch["error"].lower()

    # Background review cannot write file to user-made skill
    res_file = skill_mgr.write_file("user-skill", "test.py", "x = 1", origin="background_review")
    assert res_file["success"] is False

    # Background review cannot delete user-made skill
    res_del = skill_mgr.delete("user-skill", origin="background_review")
    assert res_del["success"] is False

def test_pinned_allows_patch_blocks_delete(skill_mgr):
    skill_mgr.create("agent-skill", "---\nname: agent-skill\ndescription: Agent skill\n---\nInitial text", origin="background_review")
    assert skill_mgr.is_agent_created("agent-skill")
    
    # Pin the skill
    data = skill_mgr._load_usage()
    data["agent-skill"]["pinned"] = True
    skill_mgr._save_usage(data)

    # Foreground can still patch pinned skill
    res_patch = skill_mgr.patch("agent-skill", "Initial text", "Updated text", origin="foreground")
    assert res_patch["success"] is True

    # Delete is blocked for both foreground and background
    res_del_fg = skill_mgr.delete("agent-skill", origin="foreground")
    assert res_del_fg["success"] is False
    assert "pinned" in res_del_fg["error"].lower()

    res_del_bg = skill_mgr.delete("agent-skill", origin="background_review")
    assert res_del_bg["success"] is False

def test_contextvar_write_origin_lifecycle():
    assert get_current_write_origin() == "foreground"
    token = set_current_write_origin("background_review")
    assert get_current_write_origin() == "background_review"
    reset_current_write_origin(token)
    assert get_current_write_origin() == "foreground"

def test_sync_existing_skills_auto_seed(tmp_path):
    skills_dir = tmp_path / "skills"
    usage_file = skills_dir / ".usage.json"

    # Pre-populate an existing skill folder directly without using SkillManager
    pre_existing = skills_dir / "legacy-tool"
    pre_existing.mkdir(parents=True, exist_ok=True)
    (pre_existing / "SKILL.md").write_text("---\nname: legacy-tool\ndescription: Legacy tool.\n---\n# Legacy\n", encoding="utf-8")

    # Initializing SkillManager should automatically discover and seed legacy-tool into .usage.json
    sm = SkillManager(skills_dir, usage_file)
    assert sm.is_agent_created("legacy-tool") is False
    usage_data = json.loads(usage_file.read_text(encoding="utf-8"))
    assert "legacy-tool" in usage_data
    assert usage_data["legacy-tool"]["created_by"] == "user"

def test_write_file_skill_md_rejects_long_description(skill_manager):
    # Creating an agent skill with valid description
    skill_manager.create("desc_test_skill", "Valid instructions", description="Short desc")
    
    # Attempting to write SKILL.md with description > 60 chars must fail
    long_desc = "A" * 70
    new_content = f"---\nname: desc_test_skill\ndescription: {long_desc}\n---\nBody"
    res = skill_manager.write_file("desc_test_skill", "SKILL.md", new_content)
    assert res["success"] is False
    assert "Description exceeds 60 characters" in res["error"]

def test_patch_rejects_long_description(skill_manager):
    skill_manager.create("patch_desc_test", "Valid instructions", description="Short desc")
    long_desc = "B" * 70
    res = skill_manager.patch("patch_desc_test", "description: Short desc", f"description: {long_desc}")
    assert res["success"] is False
    assert "Description exceeds 60 characters" in res["error"]

def test_atomic_usage_save(skill_mgr, monkeypatch):
    recorded_tmp = []
    orig_replace = Path.replace
    def fake_replace(src, dst):
        recorded_tmp.append((str(src), str(dst), src.exists()))
        return orig_replace(src, dst)
    monkeypatch.setattr(Path, "replace", fake_replace)

    skill_mgr._save_usage({"test": {"created_by": "user"}})
    assert len(recorded_tmp) == 1
    src, dst, src_existed = recorded_tmp[0]
    assert ".tmp." in src
    assert src_existed is True
    assert dst.endswith(".usage.json")
    assert skill_mgr._load_usage() == {"test": {"created_by": "user"}}


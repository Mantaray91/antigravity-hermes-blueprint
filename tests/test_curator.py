import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import tarfile
import time
import json
from scripts.curator import Curator

@pytest.fixture
def curator_env(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True)
    usage_file = skills_dir / ".usage.json"
    backup_dir = skills_dir / ".curator_backups"
    return Curator(skills_dir=skills_dir, usage_path=usage_file, backup_dir=backup_dir)

def test_backup_and_rollback(curator_env):
    skill = curator_env.skills_dir / "test-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("Initial content", encoding="utf-8")

    archive_tar = curator_env.backup()
    assert archive_tar.exists()

    # Mutate skill
    (skill / "SKILL.md").write_text("Mutated content", encoding="utf-8")
    assert (skill / "SKILL.md").read_text() == "Mutated content"

    # Rollback
    curator_env.rollback()
    assert (skill / "SKILL.md").read_text() == "Initial content"

def test_rollback_no_backup(curator_env):
    assert curator_env.rollback() is False

def test_prune_stale_skills(curator_env):
    skill = curator_env.skills_dir / "stale-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("Stale skill", encoding="utf-8")
    
    # Mark as agent-created and unused for 35 days
    old_time = time.time() - (35 * 86400)
    curator_env.set_skill_usage("stale-skill", agent_created=True, last_used=old_time)

    archived = curator_env.prune(archive_after_days=30)
    assert "stale-skill" in archived
    assert not (curator_env.skills_dir / "stale-skill").exists()
    assert (curator_env.skills_dir / ".archive" / "stale-skill" / "SKILL.md").exists()

def test_prune_ignores_pinned_and_user_skills(curator_env):
    # Pinned skill (stale, agent_created=True, pinned=True)
    pinned = curator_env.skills_dir / "pinned-skill"
    pinned.mkdir()
    (pinned / "SKILL.md").write_text("Pinned", encoding="utf-8")
    old_time = time.time() - (35 * 86400)
    curator_env.set_skill_usage("pinned-skill", agent_created=True, last_used=old_time, pinned=True)

    # User created skill (stale, agent_created=False, pinned=False)
    user_skill = curator_env.skills_dir / "user-skill"
    user_skill.mkdir()
    (user_skill / "SKILL.md").write_text("User skill", encoding="utf-8")
    curator_env.set_skill_usage("user-skill", agent_created=False, last_used=old_time, pinned=False)

    # Recent skill (agent_created=True, recent usage)
    recent = curator_env.skills_dir / "recent-skill"
    recent.mkdir()
    (recent / "SKILL.md").write_text("Recent", encoding="utf-8")
    curator_env.set_skill_usage("recent-skill", agent_created=True, last_used=time.time(), pinned=False)

    archived = curator_env.prune(archive_after_days=30)
    assert archived == []
    assert (curator_env.skills_dir / "pinned-skill").exists()
    assert (curator_env.skills_dir / "user-skill").exists()
    assert (curator_env.skills_dir / "recent-skill").exists()

def test_pin_and_unpin(curator_env):
    curator_env.pin("test-pin")
    data = curator_env._load_usage()
    assert data["test-pin"]["pinned"] is True

    curator_env.unpin("test-pin")
    data = curator_env._load_usage()
    assert data["test-pin"]["pinned"] is False

def test_get_status(curator_env):
    skill = curator_env.skills_dir / "stale-skill"
    skill.mkdir()
    old_time = time.time() - (35 * 86400)
    curator_env.set_skill_usage("stale-skill", agent_created=True, last_used=old_time)

    pinned = curator_env.skills_dir / "pinned-skill"
    pinned.mkdir()
    curator_env.set_skill_usage("pinned-skill", agent_created=True, pinned=True)

    status = curator_env.get_status(archive_after_days=30)
    assert status["total_skills"] == 2
    assert status["stale_count"] == 1
    assert "stale-skill" in status["stale_skills"]
    assert "pinned-skill" in status["pinned_skills"]


def test_cli_actions(curator_env, monkeypatch, capsys):
    from scripts.curator import main

    skill = curator_env.skills_dir / "cli-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("CLI test", encoding="utf-8")
    curator_env.set_skill_usage("cli-skill", agent_created=True, last_used=time.time() - (40 * 86400))

    common_args = [
        "curator.py",
        "--skills-dir", str(curator_env.skills_dir),
        "--usage-file", str(curator_env.usage_path),
        "--backup-dir", str(curator_env.backup_dir),
    ]

    # test pin
    monkeypatch.setattr(sys, "argv", common_args + ["pin", "--name", "cli-skill"])
    main()
    out = capsys.readouterr().out
    assert "Pinned cli-skill" in out
    assert curator_env._load_usage()["cli-skill"]["pinned"] is True

    # test status
    monkeypatch.setattr(sys, "argv", common_args + ["status"])
    main()
    out = capsys.readouterr().out
    assert "Curator status: Active" in out
    assert "Total skills: 1" in out
    assert "cli-skill" in out

    # test unpin
    monkeypatch.setattr(sys, "argv", common_args + ["unpin", "--name", "cli-skill"])
    main()
    out = capsys.readouterr().out
    assert "Unpinned cli-skill" in out

    # test backup
    monkeypatch.setattr(sys, "argv", common_args + ["backup"])
    main()
    out = capsys.readouterr().out
    assert "Backup created at:" in out

    # test prune
    monkeypatch.setattr(sys, "argv", common_args + ["prune", "--days", "30"])
    main()
    out = capsys.readouterr().out
    assert "Archived 1 skills: ['cli-skill']" in out
    assert not (curator_env.skills_dir / "cli-skill").exists()

    # test rollback
    monkeypatch.setattr(sys, "argv", common_args + ["rollback"])
    main()
    out = capsys.readouterr().out
    assert "Rollback status: True" in out
    assert (curator_env.skills_dir / "cli-skill" / "SKILL.md").exists()

def test_backup_microsecond_timestamp(curator_env):
    import re
    tar1 = curator_env.backup()
    tar2 = curator_env.backup()
    assert tar1 != tar2, "Rapid backups must not collide thanks to microsecond resolution"
    # Matches format skills_backup_YYYYMMDD_HHMMSS_ffffff.tar.gz
    assert re.match(r"^skills_backup_\d{8}_\d{6}_\d{6}\.tar\.gz$", tar1.name)
    assert re.match(r"^skills_backup_\d{8}_\d{6}_\d{6}\.tar\.gz$", tar2.name)

def test_cli_pin_unpin_requires_name(curator_env, monkeypatch, capsys):
    from scripts.curator import main

    common_args = [
        "curator.py",
        "--skills-dir", str(curator_env.skills_dir),
        "--usage-file", str(curator_env.usage_path),
        "--backup-dir", str(curator_env.backup_dir),
    ]

    # pin without --name
    monkeypatch.setattr(sys, "argv", common_args + ["pin"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Error: --name is required for pin" in err

    # unpin without --name
    monkeypatch.setattr(sys, "argv", common_args + ["unpin"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Error: --name is required for unpin" in err

def test_curator_sync_existing_skills(curator_env):
    manual_skill = curator_env.skills_dir / "manual-skill"
    manual_skill.mkdir()
    (manual_skill / "SKILL.md").write_text("manual", encoding="utf-8")

    # Before sync, not in usage data
    assert "manual-skill" not in curator_env._load_usage()

    # Sync
    added = curator_env.sync_existing_skills()
    assert added == 1
    usage = curator_env._load_usage()
    assert "manual-skill" in usage
    assert usage["manual-skill"]["created_by"] == "user"
    assert usage["manual-skill"]["agent_created"] is False


def test_resolve_agents_root_and_no_phantom_agents(tmp_path, monkeypatch):
    from scripts.curator import resolve_agents_root, main

    fake_home = tmp_path / "home"
    fake_agents = fake_home / ".agents"
    fake_skills = fake_agents / "skills"
    fake_skills.mkdir(parents=True)
    external_dir = tmp_path / "somewhere_else"
    external_dir.mkdir()

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.chdir(external_dir)

    resolved = resolve_agents_root()
    assert resolved == fake_agents

    # Running main() without --skills-dir from external dir must not create phantom .agents
    monkeypatch.setattr(sys, "argv", ["curator.py", "status"])
    main()
    assert not (external_dir / ".agents").exists()

def test_rollback_removes_newly_created_skills(curator_env):
    # 1. Take initial backup
    backup_file = curator_env.backup()
    assert backup_file.exists()
    
    # 2. Add a new skill folder after the backup
    new_skill_dir = curator_env.skills_dir / "post_backup_skill"
    new_skill_dir.mkdir(parents=True)
    (new_skill_dir / "SKILL.md").write_text("---\nname: post_backup_skill\n---\nBody", encoding="utf-8")
    assert new_skill_dir.exists()
    
    # 3. Rollback to the previous backup
    res = curator_env.rollback()
    assert res is True
    
    # 4. Post-backup skill folder must be removed
    assert not new_skill_dir.exists()

def test_curator_atomic_usage_save(curator_env, monkeypatch):
    recorded_tmp = []
    orig_replace = Path.replace
    def fake_replace(src, dst):
        recorded_tmp.append((str(src), str(dst), src.exists()))
        return orig_replace(src, dst)
    monkeypatch.setattr(Path, "replace", fake_replace)

    curator_env._save_usage({"test": {"pinned": True}})
    assert len(recorded_tmp) == 1
    src, dst, src_existed = recorded_tmp[0]
    assert ".tmp." in src
    assert src_existed is True
    assert dst.endswith(".usage.json")
    assert curator_env._load_usage() == {"test": {"pinned": True}}

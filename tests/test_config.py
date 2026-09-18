import os
from pathlib import Path
from hermes_engine.config import resolve_agents_root


def test_resolve_agents_root_env_override(tmp_path, monkeypatch):
    custom_root = tmp_path / "custom_agents"
    custom_root.mkdir()
    monkeypatch.setenv("AGENTS_ROOT", str(custom_root))
    assert resolve_agents_root() == custom_root


def test_resolve_agents_root_cwd_with_skills(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_ROOT", raising=False)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    assert resolve_agents_root() == tmp_path


def test_resolve_agents_root_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_ROOT", raising=False)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)
    # When no skills dir exists in cwd or home, fallback returns cwd
    resolved = resolve_agents_root()
    assert isinstance(resolved, Path)

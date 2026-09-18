# Changelog

All notable changes to the **Antigravity X Hermes Architecture Blueprint** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.1] - 2026-09-18

### Added
- **Unified Path Resolvers**: Added `resolve_user_memory_path()` and `resolve_workspace_memory_path()` to `hermes_engine/config.py`. Centralizes `GEMINI_MEMORIES_DIR` environment override resolution across `server.py`, `pre_invocation.py`, and `stop_hook.py`.
- **Signature Flexibility in `SessionDB`**: Normalized `SessionDB.ingest_transcript()` to natively support explicit keyword arguments (`session_id`, `steps`, `transcript_path`) alongside existing positional patterns, eliminating fragile parameter assumptions.
- **Cross-Platform `fcntl` Guard**: Added graceful platform fallback for POSIX advisory file locking in `hermes_engine/memory.py` to prevent import crashes on non-Unix / Windows environments.
- **Active Python Interpreter Detection**: Updated `install.sh` to dynamically detect `which python3` and use the active interpreter path when generating `hooks.json`.
- **Expanded Test Coverage**: Added dedicated unit tests for configuration path resolvers and keyword transcript ingestion, bringing the automated test suite to 126 tests (100% pass).

### Changed
- **Two-Phase Atomic Multi-File Batch Writes**: Upgraded `MemoryStore.batch()` to write all target files to temporary buffers first before executing atomic renames, preventing partial state application across `USER.md` and `MEMORY.md`.
- **File Mode Normalization**: Enforced standard `100644` non-executable permission on `hermes_engine/hooks/__init__.py`.

---

## [1.0.0] - 2026-09-18

### Added
- **Initial Public Release** of the Antigravity X Hermes Architecture Blueprint.
- **Tier 1 (Short-Term Memory)**: `PreInvocation` hook (`pre_invocation.py`) injecting working context on `invocationNum == 0` for prefix-cache stability.
- **Tier 2 (Mid-Term Curated Memory)**: Two-Zone Memory Store (`memory.py`) managing `USER.md` ($\le 1,375$ chars) and `MEMORY.md` ($\le 2,200$ chars) with Anchor Zone immunity and Dynamic Zone FIFO eviction.
- **Tier 3 (Episodic Recall & Reflection)**: SQLite FTS5 database (`session_db.py`) with WAL mode, BM25 ranking, progressive pagination, and metadata sanitization (`strip_system_metadata`).
- **Autonomous Self-Evolution**: Background reflection pipeline (`reflector.py`) with 2-layer deduplication (exact + Token-set Jaccard $\ge 0.85$) and bilingual (Indonesian + English) negation conflict guard.
- **Write-Origin Provenance Guard**: `SkillManager` (`skills.py`) isolating user-owned and pinned skills from autonomous background mutation.
- **Skill Curator**: Anti-bloat engine (`scripts/curator.py`) with telemetry tracking, inactive skill archiving (`.archive/`), and rollback capabilities.
- **Life-Cycle Safety**: Monotonic cooperative deadline propagation (`time.monotonic() + 1.5s`) in the `Stop` hook guaranteeing sub-1.5s shutdown.
- **Comprehensive Documentation**: 5 formal architectural specifications (`docs/`), `README.md`, and `AGENTS.md`.
- **Automated Installer**: Portable `install.sh` for one-command installation by developers and autonomous agents.
- **Test Suite**: 122 unit tests validating all subsystem boundaries.

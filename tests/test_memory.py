import pytest
from pathlib import Path
from hermes_engine.memory import MemoryStore, ENTRY_DELIMITER

@pytest.fixture
def mem_store(tmp_path):
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    return MemoryStore(user_path=user_file, memory_path=mem_file, user_limit=100, memory_limit=200)

def test_add_and_render_snapshot(mem_store):
    res = mem_store.add("user", "User prefers concise technical Indonesian.")
    assert res["success"] is True
    assert "User prefers concise" in mem_store.render_snapshot()
    assert mem_store.user_path.exists()
    assert ENTRY_DELIMITER not in mem_store.user_path.read_text()

def test_add_multiple_entries_delimiter(mem_store):
    mem_store.add("memory", "Quirk A: always use pytest.")
    mem_store.add("memory", "Quirk B: fastmcp for tools.")
    content = mem_store.memory_path.read_text()
    assert ENTRY_DELIMITER.strip() in content
    entries = mem_store.get_entries("memory")
    assert len(entries) == 2

def test_character_limit_overflow(mem_store):
    long_text = "A" * 150
    res = mem_store.add("user", long_text)
    assert res["success"] is False
    assert "limit" in res["error"].lower()

def test_replace_entry(mem_store):
    mem_store.add("user", "Old preference.")
    res = mem_store.replace("user", "Old preference", "Updated preference.")
    assert res["success"] is True
    assert "Updated preference." in mem_store.render_snapshot()
    assert "Old preference." not in mem_store.render_snapshot()

def test_remove_entry(mem_store):
    mem_store.add("memory", "Anchor note.")
    mem_store.add("memory", "Temporary note.")
    res = mem_store.remove("memory", "Temporary note")
    assert res["success"] is True
    assert len(mem_store.get_entries("memory")) == 1
    assert mem_store.get_entries("memory")[0] == "Anchor note."

def test_batch_atomic_rollback(mem_store):
    mem_store.add("user", "Initial user note.")
    ops = [
        {"action": "add", "target": "user", "content": "Valid note."},
        {"action": "add", "target": "user", "content": "X" * 200}  # Overflows limit
    ]
    res = mem_store.batch(ops)
    assert res["success"] is False
    entries = mem_store.get_entries("user")
    assert len(entries) == 1
    assert entries[0] == "Initial user note."

def test_reject_raw_delimiter(mem_store):
    bad_content = f"Note with raw {ENTRY_DELIMITER.strip()} delimiter in between"
    res = mem_store.add("memory", bad_content)
    assert res["success"] is False
    assert "delimiter" in res["error"].lower()

    # Also check replace rejects raw delimiter
    mem_store.add("memory", "Clean note")
    res_replace = mem_store.replace("memory", "Clean note", bad_content)
    assert res_replace["success"] is False
    assert "delimiter" in res_replace["error"].lower()

    # Also check add_entry helper returns False
    assert mem_store.add_entry("memory", bad_content) is False

def test_atomic_write_cleans_up_tempfile_on_error(mem_store, monkeypatch):
    def fake_replace(self, target):
        raise OSError("Simulated disk error during atomic replace")

    monkeypatch.setattr(Path, "replace", fake_replace)

    with pytest.raises(OSError):
        mem_store.add("memory", "Test entry to trigger error")

    # Verify no tempfiles left behind
    tempfiles = list(mem_store.memory_path.parent.glob("*.tmp.*"))
    assert len(tempfiles) == 0

def test_file_locking_on_concurrent_writes(mem_store):
    import fcntl
    mem_store.add("memory", "Initial content")
    lock_file = mem_store.memory_path.parent / f".{mem_store.memory_path.name}.lock"
    assert lock_file.exists()

    with open(lock_file, "a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            with open(lock_file, "a+", encoding="utf-8") as lf2:
                with pytest.raises((BlockingIOError, OSError)):
                    fcntl.flock(lf2.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

def test_mutate_reloads_disk_state_and_locks(mem_store):
    import fcntl
    # Initial write
    mem_store.add("memory", "First entry")

    # Simulate another process modifying file on disk
    path = mem_store.memory_path
    lock_file = path.parent / f".{path.name}.lock"
    with open(lock_file, "a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            path.write_text(f"First entry{ENTRY_DELIMITER}External concurrent entry", encoding="utf-8")
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    # Next add should reload and keep external entry
    res = mem_store.add("memory", "Second entry")
    assert res["success"] is True

    content = mem_store.memory_path.read_text(encoding="utf-8")
    assert "First entry" in content
    assert "External concurrent entry" in content
    assert "Second entry" in content


@pytest.fixture
def memory_store(tmp_path):
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    return MemoryStore(user_path=user_file, memory_path=mem_file, user_limit=1375, memory_limit=2200)


def test_two_zone_memory_preserves_anchor_during_overflow(memory_store):
    anchor = "# User Profile & Preferences\n- User: test_user\n- OS: Linux"
    dyn1 = "- Pref 1: " + ("A" * 600)
    dyn2 = "- Pref 2: " + ("B" * 660)

    # Pre-populate memory
    memory_store._atomic_write(memory_store.user_file, f"{anchor}\n§\n{dyn1}\n§\n{dyn2}")

    # Adding new preference exceeds 1375 limit -> dyn1 should be evicted, anchor untouched
    new_pref = "- Pref 3: New Important Preference"
    res = memory_store.add("user", new_pref)
    assert res["success"] is True

    entries = memory_store.get_entries("user")
    assert entries[0] == anchor  # Anchor Zone untouched!
    assert dyn1 not in entries   # Oldest dynamic preference evicted
    assert new_pref in entries   # New preference accommodated


@pytest.fixture
def temp_memory_dir(tmp_path):
    user_file = tmp_path / "USER.md"
    mem_file = tmp_path / "MEMORY.md"
    return user_file, mem_file


def test_anchor_zone_removal_rejected_in_remove(temp_memory_dir):
    user_file, mem_file = temp_memory_dir
    store = MemoryStore(user_path=user_file, memory_path=mem_file, user_limit=500)
    store.add("user", "ANCHOR: Core identity")
    store.add("user", "Dynamic fact 1")
    
    # Attempting to remove anchor zone (index 0) must fail
    res = store.remove("user", "Core identity")
    assert res["success"] is False
    assert "Cannot remove Anchor Zone" in res["error"]
    assert store.get_entries("user")[0] == "ANCHOR: Core identity"


def test_anchor_zone_removal_rejected_in_batch(temp_memory_dir):
    user_file, mem_file = temp_memory_dir
    store = MemoryStore(user_path=user_file, memory_path=mem_file, user_limit=500)
    store.add("user", "ANCHOR: Core identity")
    store.add("user", "Dynamic fact 1")
    
    # Batch remove matching anchor must abort
    res = store.batch([{"action": "remove", "target": "user", "old_text": "Core identity"}])
    assert res["success"] is False
    assert "Cannot remove Anchor Zone" in res["error"]
    assert store.get_entries("user")[0] == "ANCHOR: Core identity"


def test_empty_or_whitespace_content_rejected(temp_memory_dir):
    user_file, mem_file = temp_memory_dir
    store = MemoryStore(user_path=user_file, memory_path=mem_file, user_limit=500)
    
    res_add_empty = store.add("user", "")
    assert res_add_empty["success"] is False
    res_add_ws = store.add("user", "   \n\t  ")
    assert res_add_ws["success"] is False
    
    store.add("user", "Valid entry")
    res_rep_empty = store.replace("user", "Valid entry", "  ")
    assert res_rep_empty["success"] is False


def test_batch_two_zone_eviction_with_transparency_report(temp_memory_dir):
    user_file, mem_file = temp_memory_dir
    # Budget 50 chars. Anchor (20) + Delimiter (3) + Entry1 (20) = 43 chars. Adding Entry2 (20) = 66 chars > 50 chars
    store = MemoryStore(user_path=user_file, memory_path=mem_file, user_limit=50)
    store.add("user", "ANCHOR: 012345678901")
    store.add("user", "DYNAMIC 1: 012345678")
    
    # Batch add that exceeds limit must trigger Two-Zone eviction of DYNAMIC 1 and report it
    res = store.batch([
        {"action": "add", "target": "user", "content": "DYNAMIC 2: 012345678"}
    ])
    assert res["success"] is True
    assert res["applied"] == 1
    assert "evicted" in res
    assert res["eviction_count"] == 1
    assert res["evicted"][0]["target"] == "user"
    assert "DYNAMIC 1" in res["evicted"][0]["evicted"]
    
    entries = store.get_entries("user")
    assert entries[0] == "ANCHOR: 012345678901"
    assert entries[1] == "DYNAMIC 2: 012345678"


def test_single_add_two_zone_eviction_telemetry(temp_memory_dir):
    user_file, mem_file = temp_memory_dir
    # Budget 50 chars. Anchor (20) + Delimiter (3) + Entry1 (20) = 43 chars.
    store = MemoryStore(user_path=user_file, memory_path=mem_file, user_limit=50)
    store.add("user", "ANCHOR: 012345678901")
    store.add("user", "DYNAMIC 1: 012345678")

    # Single add that exceeds limit must evict DYNAMIC 1 and return transparency telemetry
    res = store.add("user", "DYNAMIC 2: 012345678")
    assert res["success"] is True
    assert "evicted" in res
    assert res["eviction_count"] == 1
    assert res["evicted"][0]["target"] == "user"
    assert "DYNAMIC 1" in res["evicted"][0]["evicted"]

    entries = store.get_entries("user")
    assert entries[0] == "ANCHOR: 012345678901"
    assert entries[1] == "DYNAMIC 2: 012345678"


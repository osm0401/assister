import zipfile
from datetime import datetime

import pytest

from assister.memory.backup import BackupError, export_data, import_data
from assister.memory.store import MemoryStore
from assister.tasks.repository import TaskRepository

NOW = datetime(2026, 10, 6, 15, 0)


def fill(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    memory = MemoryStore(data_dir / "memory.db")
    memory.add_message("user", "내 이름은 상민이야", NOW)
    memory.add_fact("이름은 상민", NOW)
    memory.set_state("bond_points", 42)
    memory.close()
    tasks = TaskRepository(data_dir / "tasks.db")
    tasks.add_todo("우유 사기", NOW)
    tasks.close()
    (data_dir / "settings.json").write_text('{"outfit": "dress"}', encoding="utf-8")


def test_everything_moves_to_another_pc(tmp_path):
    fill(tmp_path / "old")
    archive = tmp_path / "기억.zip"

    export_data(tmp_path / "old", archive)
    import_data(archive, tmp_path / "new")

    memory = MemoryStore(tmp_path / "new" / "memory.db")
    tasks = TaskRepository(tmp_path / "new" / "tasks.db")
    try:
        assert [m.content for m in memory.recent_messages(5)] == ["내 이름은 상민이야"]
        assert [f.text for f in memory.list_facts()] == ["이름은 상민"]
        assert memory.get_state("bond_points", 0) == 42
        assert [t.text for t in tasks.list_todos()] == ["우유 사기"]
    finally:
        memory.close()
        tasks.close()
    assert (tmp_path / "new" / "settings.json").read_text(encoding="utf-8") == '{"outfit": "dress"}'


def test_export_works_while_the_database_is_open(tmp_path):
    fill(tmp_path / "old")
    memory = MemoryStore(tmp_path / "old" / "memory.db")
    try:
        memory.add_message("user", "앱이 켜진 채로 한 말", NOW)
        export_data(tmp_path / "old", tmp_path / "a.zip")
    finally:
        memory.close()

    import_data(tmp_path / "a.zip", tmp_path / "new")
    restored = MemoryStore(tmp_path / "new" / "memory.db")
    try:
        assert restored.count_messages() == 2
    finally:
        restored.close()


def test_import_replaces_what_was_there(tmp_path):
    fill(tmp_path / "old")
    export_data(tmp_path / "old", tmp_path / "a.zip")
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "settings.json").write_text('{"outfit": "basic"}', encoding="utf-8")

    import_data(tmp_path / "a.zip", tmp_path / "new")

    assert "dress" in (tmp_path / "new" / "settings.json").read_text(encoding="utf-8")


def test_archive_cannot_write_outside_the_data_folder(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", "x")
        bundle.writestr("nested/memory.db", "x")
        bundle.writestr("run.exe", "x")
        bundle.writestr("settings.json", "{}")

    import_data(archive, tmp_path / "new")

    assert sorted(p.name for p in (tmp_path / "new").iterdir()) == ["settings.json"]
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("content", [b"not a zip", b""])
def test_broken_archive_is_reported(tmp_path, content):
    archive = tmp_path / "broken.zip"
    archive.write_bytes(content)

    with pytest.raises(BackupError):
        import_data(archive, tmp_path / "new")


def test_archive_without_any_memory_is_refused_before_touching_anything(tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("readme.txt", "x")
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "settings.json").write_text("keep", encoding="utf-8")

    with pytest.raises(BackupError):
        import_data(archive, tmp_path / "new")

    assert (tmp_path / "new" / "settings.json").read_text(encoding="utf-8") == "keep"

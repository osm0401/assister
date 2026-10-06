from assister.system.autostart import SCRIPT_NAME, is_enabled, set_enabled


def test_turning_it_on_writes_one_launcher_and_turning_it_off_removes_it(tmp_path):
    folder = tmp_path / "Startup"
    assert not is_enabled(folder)

    set_enabled(True, folder, python=r"C:\app\.venv\Scripts\pythonw.exe", workdir=r"C:\app")

    assert is_enabled(folder)
    script = (folder / SCRIPT_NAME).read_text(encoding="utf-8")
    assert r'"C:\app\.venv\Scripts\pythonw.exe" -m assister' in script
    assert r'/D "C:\app"' in script

    set_enabled(False, folder)

    assert not is_enabled(folder)
    assert list(folder.iterdir()) == []


def test_turning_it_off_twice_is_harmless(tmp_path):
    set_enabled(False, tmp_path)
    set_enabled(False, tmp_path)

    assert not is_enabled(tmp_path)


def test_only_its_own_launcher_is_touched(tmp_path):
    other = tmp_path / "다른 프로그램.lnk"
    other.write_text("x", encoding="utf-8")

    set_enabled(True, tmp_path, python="pythonw.exe", workdir=".")
    set_enabled(False, tmp_path)

    assert other.exists()

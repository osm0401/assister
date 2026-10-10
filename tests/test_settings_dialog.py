import json

import pytest

from assister.character.art import generate_placeholder_pack
from assister.character.pack import Voice, list_packs, load_pack, save_profile
from assister.config import Settings
from assister.system.settings_dialog import SettingsDialog


@pytest.fixture
def pack(tmp_path):
    return load_pack(generate_placeholder_pack(tmp_path / "default"))


@pytest.fixture
def dialog(qapp, pack):
    settings = Settings(blocked_apps=("stardew",), screen_interval_sec=30)
    dialog = SettingsDialog(settings, pack)
    yield dialog
    dialog.close()


def test_dialog_opens_with_the_current_values(dialog):
    assert dialog.name.text() == "모모"
    assert dialog.interval.value() == 30
    assert dialog.blocked.toPlainText() == "stardew"
    assert dialog.voice.currentText() == "ko-KR-SunHiNeural"
    assert dialog.summon_hotkey.text() == "Ctrl+Shift+Space"


def test_changed_values_come_back_as_new_settings_and_profile(dialog):
    dialog.name.setText("하루")
    dialog.persona.setPlainText("반말로, 장난기 있게")
    dialog.voice.setCurrentText("ko-KR-InJoonNeural")
    dialog.pitch.setValue(4)
    dialog.rate.setValue(-10)
    dialog.aliases.setText("하루야, 하루짱")
    dialog.interval.setValue(10)
    dialog.proactive.setValue(0)
    dialog.blocked.setPlainText("stardew\n\n notion.exe \n")
    dialog.voice_hotkey.setText("Ctrl+Alt+V")

    settings, profile = dialog.result_values()

    assert settings.screen_interval_sec == 10 and settings.proactive_per_hour == 0
    assert settings.blocked_apps == ("stardew", "notion.exe")
    assert settings.voice_hotkey == "Ctrl+Alt+V"
    assert profile == {
        "name": "하루",
        "persona": "반말로, 장난기 있게",
        "voice": {"name": "ko-KR-InJoonNeural", "rate": "-10%", "pitch": "+4Hz"},
        "wake_aliases": ["하루야", "하루짱"],
    }


def test_ai_engine_can_be_switched_to_gemini(dialog):
    assert dialog.engine.currentData() == "ollama"
    assert "GEMINI_API_KEY" in dialog.engine.toolTip() and "Google 서버" in dialog.engine.toolTip()

    dialog.engine.setCurrentIndex(dialog.engine.findData("gemini"))

    assert dialog.result_values()[0].engine == "gemini"


def test_untouched_settings_are_kept(dialog):
    settings, _ = dialog.result_values()

    assert settings == Settings(blocked_apps=("stardew",), screen_interval_sec=30)


@pytest.mark.parametrize(("field", "value"), [("summon_hotkey", "Space"), ("voice_hotkey", "Ctrl+Nope"), ("name", "  ")])
def test_unusable_input_is_refused_with_a_reason(dialog, field, value):
    getattr(dialog, field).setText(value)

    dialog.accept()

    assert dialog.result() == 0  # 닫히지 않는다
    assert dialog.error.text() != ""


def test_same_hotkey_for_both_is_refused(dialog):
    dialog.voice_hotkey.setText(dialog.summon_hotkey.text())

    dialog.accept()

    assert dialog.result() == 0 and "단축키" in dialog.error.text()


def test_character_can_be_switched_among_the_folders_that_exist(qapp, pack, tmp_path):
    generate_placeholder_pack(tmp_path / "haru", name="하루")
    assert list_packs(tmp_path) == ["default", "haru"]

    dialog = SettingsDialog(Settings(), pack, characters=list_packs(tmp_path))
    try:
        assert dialog.character.currentText() == "default"
        dialog.character.setCurrentText("haru")

        settings, _ = dialog.result_values()

        assert settings.character == "haru"
        assert dialog.character_changed()
    finally:
        dialog.close()


def test_folders_without_pictures_are_not_offered_as_characters(tmp_path):
    (tmp_path / "notes").mkdir()
    generate_placeholder_pack(tmp_path / "default")

    assert list_packs(tmp_path) == ["default"]
    assert list_packs(tmp_path / "nowhere") == []


def test_voice_can_be_tried_before_saving(dialog):
    heard = []
    dialog.preview_requested.connect(heard.append)
    dialog.voice.setCurrentText("ko-KR-InJoonNeural")
    dialog.rate.setValue(10)

    dialog.preview.click()

    assert heard == [Voice("ko-KR-InJoonNeural", "+10%", "+0Hz")]


def test_start_with_windows_switch_shows_and_returns_its_state(qapp, pack):
    dialog = SettingsDialog(Settings(), pack, autostart=True)
    try:
        assert dialog.autostart.isChecked()
        dialog.autostart.setChecked(False)
        assert not dialog.autostart.isChecked()
    finally:
        dialog.close()


def test_profile_is_written_into_the_character_file_without_losing_outfits(pack):
    save_profile(pack.root, {"name": "하루", "persona": "반말", "voice": {"name": "ko-KR-InJoonNeural", "rate": "+0%", "pitch": "+4Hz"}, "wake_aliases": ["하루야"]})

    reloaded = load_pack(pack.root)

    assert reloaded.name == "하루" and reloaded.persona == "반말"
    assert reloaded.voice == Voice("ko-KR-InJoonNeural", "+0%", "+4Hz")
    assert reloaded.wake_names == ("하루", "하루야")
    assert len(reloaded.outfits) == 3 and len(reloaded.props) == 3
    assert "outfits" in json.loads((pack.root / "character.json").read_text(encoding="utf-8"))

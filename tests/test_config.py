import json

import pytest

from assister.config import Settings, SettingsError, load_settings, save_settings


def test_missing_file_gives_defaults(tmp_path):
    settings = load_settings(tmp_path / "settings.json")

    assert settings == Settings()
    assert settings.summon_hotkey == "Ctrl+Shift+Space"
    assert settings.do_not_disturb is False


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    original = Settings(character_height=400, position=(120, 340), do_not_disturb=True)

    save_settings(path, original)

    assert load_settings(path) == original


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"character_height": 250, "removed_option": 1}), encoding="utf-8")

    assert load_settings(path).character_height == 250


def test_broken_json_is_reported(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(SettingsError, match="설정 파일"):
        load_settings(path)


@pytest.mark.parametrize("height", [10, 5000, "big"])
def test_out_of_range_height_is_rejected(tmp_path, height):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"character_height": height}), encoding="utf-8")

    with pytest.raises(SettingsError, match="character_height"):
        load_settings(path)


def test_screen_watching_defaults_and_block_list_round_trip(tmp_path):
    defaults = Settings()
    assert (defaults.screen_watch_enabled, defaults.screen_interval_sec, defaults.screen_retention_days) == (True, 30, 7)
    assert defaults.blocked_apps == () and defaults.proactive_per_hour == 1

    path = tmp_path / "settings.json"
    save_settings(path, Settings(blocked_apps=("Stardew", "notion.exe"), screen_interval_sec=10))

    assert load_settings(path).blocked_apps == ("Stardew", "notion.exe")
    assert load_settings(path).screen_interval_sec == 10


@pytest.mark.parametrize(
    "raw",
    [
        {"screen_interval_sec": 1},
        {"screen_retention_days": 0},
        {"proactive_per_hour": -1},
        {"screen_width": "wide"},
        {"blocked_apps": "kakao"},
        {"blocked_apps": [1, 2]},
    ],
)
def test_unusable_screen_settings_are_rejected(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SettingsError, match=next(iter(raw))):
        load_settings(path)


def test_settings_are_immutable():
    settings = Settings()

    with pytest.raises(AttributeError):
        settings.character_height = 1  # type: ignore[misc]

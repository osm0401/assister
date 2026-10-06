import pytest

from assister.system.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, HotkeyError, parse_hotkey


def test_parses_modifiers_and_named_key():
    assert parse_hotkey("Ctrl+Shift+Space") == (MOD_CONTROL | MOD_SHIFT, 0x20)


def test_parsing_ignores_case_and_spaces():
    assert parse_hotkey(" ctrl + alt + a ") == (MOD_CONTROL | MOD_ALT, ord("A"))


def test_parses_function_keys_and_digits():
    assert parse_hotkey("Alt+F9") == (MOD_ALT, 0x78)
    assert parse_hotkey("Ctrl+1") == (MOD_CONTROL, ord("1"))


def test_bare_key_is_allowed_only_when_asked_for():
    """PC 조작을 멈추는 Esc처럼, 급할 때 한 손으로 누를 키는 보조키 없이 쓴다."""
    assert parse_hotkey("Esc", allow_bare=True) == (0, 0x1B)


@pytest.mark.parametrize("text", ["", "Space", "Ctrl+Shift", "Ctrl+Nope", "Ctrl+A+B"])
def test_rejects_unusable_hotkeys(text):
    with pytest.raises(HotkeyError):
        parse_hotkey(text)

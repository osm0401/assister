from assister.character.placement import Rect, bubble_position, default_position, input_position, place_on_screen

MAIN = Rect(0, 0, 1920, 1200)
SUB = Rect(-1920, 0, 1920, 1080)
SIZE = (240, 320)


def test_default_position_is_bottom_right_of_first_screen():
    assert default_position(SIZE, [MAIN, SUB]) == (1920 - 240 - 48, 1200 - 320 - 48)


def test_saved_position_on_a_screen_is_kept():
    assert place_on_screen((100, 200), SIZE, [MAIN, SUB]) == (100, 200)
    assert place_on_screen((-1500, 300), SIZE, [MAIN, SUB]) == (-1500, 300)


def test_position_on_unplugged_monitor_moves_back_to_default():
    assert place_on_screen((-1500, 300), SIZE, [MAIN]) == default_position(SIZE, [MAIN])


def test_missing_position_uses_default():
    assert place_on_screen(None, SIZE, [MAIN]) == default_position(SIZE, [MAIN])


def test_bubble_sits_centered_above_character():
    character = Rect(1000, 600, 240, 320)

    assert bubble_position(character, (200, 80), MAIN) == (1020, 600 - 80 - 4)


def test_bubble_stays_inside_the_screen():
    near_corner = Rect(1800, 20, 240, 320)

    x, y = bubble_position(near_corner, (300, 80), MAIN)

    assert x + 300 <= 1920
    assert y >= 0


def test_input_bar_sits_centered_below_character():
    character = Rect(1000, 600, 240, 320)

    assert input_position(character, (260, 40), MAIN) == (990, 600 + 320 + 4)


def test_input_bar_moves_above_feet_when_no_room_below():
    at_bottom = Rect(1000, 1200 - 320, 240, 320)

    _, y = input_position(at_bottom, (260, 40), MAIN)

    assert y + 40 <= 1200

import pytest

from assister.control.actions import ActionError
from assister.control.elements import CHOICE_FORMAT, MAX_CHOICES, Listing, build, parse_choice
from assister.system.uia import Seen, page_area

BUTTON, LINK, EDIT, TEXT, TAB = 50000, 50005, 50004, 50020, 50019
AREA = (0, 100, 1000, 800)  # 페이지가 차지하는 자리. 그 위(0~100)는 브라우저의 도구 모음이다


def seen(kind, name, x, y, *, width=80, height=30, help="", enabled=True, password=False) -> Seen:
    return Seen(kind, name, help, (x, y, x + width, y + height), enabled, password)


def names(listing: Listing) -> list[str]:
    return [f"[{item.kind}] {item.name}" for item in listing.elements]


def test_only_what_can_be_pressed_inside_the_page_is_listed_in_reading_order():
    things = [
        seen(LINK, "조립PC", 400, 120),
        seen(TAB, "내 은행 - 다른 탭", 10, 10),  # 페이지 밖(도구 모음)의 것은 밖으로 내보내지 않는다
        seen(EDIT, "검색어 입력", 300, 200, width=400),
        seen(BUTTON, "검색", 720, 200),
        seen(BUTTON, "꺼진 버튼", 100, 300, enabled=False),
        seen(EDIT, "비밀번호", 100, 400, password=True),
        seen(BUTTON, "점", 100, 500, width=3, height=3),  # 누를 수 없는 장식
        seen(TEXT, "삼성전자 DDR4-3200 4GB", 100, 600, width=300),
        seen(LINK, "   자동차\n", 300, 120),
    ]

    listing = build(things, AREA, "DDR4 4GB 찾기", window="다나와 - Chrome")

    assert names(listing) == ["[링크] 자동차", "[링크] 조립PC", "[입력 칸] 검색어 입력", "[버튼] 검색"]
    assert listing.texts == ("삼성전자 DDR4-3200 4GB",)
    assert listing.lines().splitlines()[2] == "3 [입력 칸] 검색어 입력"
    assert listing.elements[2].center == (500, 215)


def test_nameless_things_borrow_a_name_and_twins_are_told_apart():
    things = [
        seen(TEXT, "Gmarket", 100, 300),
        seen(BUTTON, "최저가", 400, 300),
        seen(TEXT, "AUCTION", 100, 340),
        seen(BUTTON, "최저가", 400, 340),
        seen(BUTTON, "닫기", 900, 120),
        seen(BUTTON, "닫기", 900, 700),
        seen(BUTTON, "", 760, 200, help="검색 실행"),  # 이름이 없으면 도움말을 쓴다
        seen(TEXT, "관심", 500, 500),
        seen(BUTTON, "", 600, 500),  # 도움말도 없으면 옆의 글자로 일러 준다
        seen(BUTTON, "", 100, 760),  # 이름도 옆 글자도 없으면 뺀다
        seen(BUTTON, "닫기", 901, 121),  # 같은 자리에 같은 이름으로 겹친 것은 하나만
    ]

    listing = build(things, AREA, "", window="쇼핑")

    assert names(listing) == [
        "[버튼] 닫기 (1번째)",
        "[버튼] 검색 실행",
        "[버튼] 최저가 (Gmarket 줄)",
        "[버튼] 최저가 (AUCTION 줄)",
        "[버튼] 이름 없는 버튼 (관심 옆)",
        "[버튼] 닫기 (2번째)",
    ]


def test_a_link_wrapped_in_a_list_item_is_counted_once():
    item = 50007
    things = [
        seen(item, "에누리", 100, 120, width=120, height=40),  # 링크를 싸고 있는 껍데기
        seen(LINK, "에누리", 110, 125, width=80),
        seen(item, "|테크", 300, 120, width=120, height=40),  # 이름이 달라도 다른 것을 품었으면 껍데기
        seen(BUTTON, "테크", 310, 125, width=60),
        seen(item, "혼자 있는 항목", 500, 120),  # 아무것도 품지 않은 목록 항목은 누를 수 있는 것이다
    ]

    assert names(build(things, AREA, "")) == ["[링크] 에누리", "[버튼] 테크", "[목록 항목] 혼자 있는 항목"]


def test_the_page_is_the_biggest_document_cut_to_what_the_window_shows():
    document = 50030
    window = (0, 0, 1000, 800)
    things = [
        seen(document, "광고", 700, 200, width=200, height=100),  # 페이지 안의 작은 문서(광고 틀)
        seen(document, "다나와", 0, 100, width=1000, height=5000),  # 아래로 길게 이어지는 페이지
        seen(BUTTON, "검색", 720, 200),
    ]

    assert page_area(things, window) == (0, 100, 1000, 800)
    assert page_area([seen(BUTTON, "확인", 10, 10)], window) is None  # 브라우저가 아니면 페이지가 없다


def test_a_long_list_keeps_input_boxes_and_what_the_goal_mentions():
    filler = [seen(LINK, f"광고 {index}", 10 + (index % 10) * 90, 150 + (index // 10) * 40) for index in range(120)]
    wanted = [seen(LINK, "DDR4 메모리", 500, 760), seen(EDIT, "검색어 입력", 100, 780, width=300)]

    listing = build([*filler, *wanted], AREA, "ddr4 4gb 가장 싼 것")

    assert len(listing.elements) == MAX_CHOICES
    assert {"DDR4 메모리", "검색어 입력"} <= {item.name for item in listing.elements}


@pytest.fixture
def listing() -> Listing:
    things = [seen(EDIT, "검색어 입력", 300, 200, width=400), seen(BUTTON, "검색", 720, 200), seen(LINK, "메모리", 100, 300)]
    return build(things, AREA, "", window="다나와")


def chosen(**fields) -> dict:
    return {"why": "그렇게 할게요.", "number": 0, "text": "", "submit": False, "keys": "", "amount": 0, "risky": False, **fields}


def test_pressing_a_number_clicks_the_middle_of_that_thing(listing):
    choice = parse_choice(chosen(action="press", number=2), listing)

    assert [(move.kind, point) for move, point in choice.moves] == [("click", (760, 215))]
    assert (choice.name, choice.said) == ("검색", "'검색'을(를) 눌렀다")


def test_typing_fills_the_box_afresh_and_can_submit(listing):
    choice = parse_choice(chosen(action="type", number=1, text="DDR4 4GB", submit=True), listing)

    assert [(move.kind, move.keys or move.text or move.target, point) for move, point in choice.moves] == [
        ("click", "검색어 입력", (500, 215)),
        ("key", "ctrl+a", None),  # 적혀 있던 것을 지우고 새로 넣는다
        ("type", "DDR4 4GB", None),
        ("key", "enter", None),
    ]
    assert choice.said == "'검색어 입력'에 'DDR4 4GB'을(를) 넣었다 그리고 Enter를 눌렀다"


def test_other_choices_become_plain_actions(listing):
    scroll = parse_choice(chosen(action="scroll"), listing)
    assert [(move.kind, move.amount, point) for move, point in scroll.moves] == [("scroll", -3, (500, 450))]

    opened = parse_choice(chosen(action="open", text="https://search.danawa.com/dsearch.php?query=DDR4"), listing)
    assert opened.moves[0][0].text.startswith("https://search.danawa.com")

    done = parse_choice(chosen(action="done", why="가장 싼 것은 12,000원이에요."), listing)
    assert (done.action.kind, done.action.why, done.moves) == ("done", "가장 싼 것은 12,000원이에요.", ())


@pytest.mark.parametrize(
    "raw",
    [
        chosen(action="press", number=9),  # 목록에 없는 번호는 누를 수 없다
        chosen(action="press", number=0),
        chosen(action="type", number=1, text=" "),
        chosen(action="key", keys="shift+delete"),
        chosen(action="dance"),
        "press 2",
    ],
)
def test_choices_that_cannot_be_carried_out_are_refused(listing, raw):
    with pytest.raises(ActionError):
        parse_choice(raw, listing)


def test_the_ai_is_shown_the_goal_the_list_and_the_words_on_screen(listing):
    prompt = listing.prompt("DDR4 4GB 찾기", ["'메모리'을(를) 눌렀다"])

    assert "[목표] DDR4 4GB 찾기" in prompt and "1. '메모리'을(를) 눌렀다" in prompt
    assert "[맨 앞 창] 다나와" in prompt and "2 [버튼] 검색" in prompt
    assert set(CHOICE_FORMAT["required"]) == set(CHOICE_FORMAT["properties"])

import zipfile
from xml.etree import ElementTree

from assister.system.documents import FOLDER_IDS, known_folder, safe_name, save_docx

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TEXT = """# API란 무엇인가

API는 프로그램끼리 정보를 주고받는 약속이다. <태그> & 기호도 그대로 적힌다.

# 쓰임새
- **날씨** 앱이 기상청에서 예보를 받아 온다
- 로그인
"""


def paragraphs(path):
    """(글, 굵은지, 글자 크기) 목록."""
    with zipfile.ZipFile(path) as archive:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(archive.namelist())
        body = ElementTree.fromstring(archive.read("word/document.xml"))
    found = []
    for paragraph in body.iter(f"{W}p"):
        look = paragraph.find(f"{W}r/{W}rPr")
        found.append((paragraph.find(f"{W}r/{W}t").text, look.find(f"{W}b") is not None, look.find(f"{W}sz").get(f"{W}val")))
    return found


def test_text_becomes_a_word_file_with_a_title_headings_and_a_list(tmp_path):
    path = save_docx(tmp_path, "API 정리", TEXT)

    assert path == tmp_path / "API 정리.docx"
    assert paragraphs(path) == [
        ("API란 무엇인가", True, "36"),
        ("API는 프로그램끼리 정보를 주고받는 약속이다. <태그> & 기호도 그대로 적힌다.", False, "22"),
        ("쓰임새", True, "28"),
        ("• 날씨 앱이 기상청에서 예보를 받아 온다", False, "22"),
        ("• 로그인", False, "22"),
    ]


def test_an_existing_file_is_never_overwritten(tmp_path):
    first = save_docx(tmp_path, "API", "하나")
    second = save_docx(tmp_path, "API", "둘")

    assert (first.name, second.name) == ("API.docx", "API (2).docx")
    assert paragraphs(first) == [("하나", True, "36")]


def test_file_names_are_made_safe():
    assert safe_name('  API: "정리"/초안?.docx ') == "API 정리 초안"
    assert safe_name("../../비밀") == "비밀"  # 다른 폴더로 빠져나가지 못한다
    assert safe_name("???") == "문서"


def test_known_folders_point_at_real_places():
    # 바탕화면·문서·다운로드는 PC마다 다른 곳에 있다(이 PC도 기본 자리가 아니다).
    assert all(known_folder(kind).is_dir() for kind in FOLDER_IDS)

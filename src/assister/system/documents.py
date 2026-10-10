"""글을 워드 파일(.docx)로 저장한다. 워드가 깔려 있지 않아도 되도록 파일 형식을 직접 쓴다."""

from __future__ import annotations

import ctypes
import io
import re
import sys
import zipfile
from ctypes import wintypes
from pathlib import Path
from xml.sax.saxutils import escape

FOLDER_IDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}
FOLDER_NAMES = {"desktop": "바탕화면", "documents": "문서", "downloads": "다운로드"}
TITLE_SIZE, HEADING_SIZE, BODY_SIZE = 36, 28, 22  # 글자 크기의 두 배 값 (워드 형식이 그렇게 적는다)
MAX_NAME_CHARS = 80
UNUSABLE_IN_NAMES = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
EXTENSION = re.compile(r"\.(docx?|txt|md)$", re.IGNORECASE)
UNUSABLE_IN_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELATIONSHIPS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)
DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>{paragraphs}<w:sectPr/></w:body></w:document>"
)


class _Guid(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


def known_folder(kind: str) -> Path:
    """바탕화면·문서·다운로드의 실제 자리. 다른 드라이브나 OneDrive로 옮겨 둔 PC가 있다."""
    fallback = Path.home() / kind.capitalize()
    if sys.platform != "win32":
        return fallback
    ole32, shell32 = ctypes.windll.ole32, ctypes.windll.shell32
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    guid, found = _Guid(), ctypes.c_wchar_p()
    if ole32.CLSIDFromString(FOLDER_IDS[kind], ctypes.byref(guid)) != 0:
        return fallback
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(found)) != 0 or not found.value:
        return fallback
    try:
        return Path(found.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(found, ctypes.c_void_p))


def safe_name(name: str) -> str:
    """파일 이름에 쓸 수 없는 글자를 빼고 확장자를 떼어 낸다."""
    cleaned = " ".join(UNUSABLE_IN_NAMES.sub(" ", name).split()).strip(" .")
    return EXTENSION.sub("", cleaned).strip(" .")[:MAX_NAME_CHARS] or "문서"


def _paragraph(text: str, size: int, *, bold: bool = False) -> str:
    look = ("<w:b/>" if bold else "") + f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    return f'<w:p><w:r><w:rPr>{look}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def docx_bytes(text: str) -> bytes:
    """첫 줄은 제목, '#'으로 시작하는 줄은 소제목, '- '로 시작하는 줄은 목록으로 꾸민 워드 파일."""
    lines = [UNUSABLE_IN_XML.sub("", line).replace("**", "").strip() for line in text.splitlines()]
    paragraphs = []
    for line in (line for line in lines if line):
        if not paragraphs:
            paragraphs.append(_paragraph(line.lstrip("# "), TITLE_SIZE, bold=True))
        elif line.startswith("#"):
            paragraphs.append(_paragraph(line.lstrip("# "), HEADING_SIZE, bold=True))
        elif line.startswith(("- ", "* ")):
            paragraphs.append(_paragraph(f"• {line[2:].strip()}", BODY_SIZE))
        else:
            paragraphs.append(_paragraph(line, BODY_SIZE))
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELATIONSHIPS)
        archive.writestr("word/document.xml", DOCUMENT.format(paragraphs="".join(paragraphs)))
    return packed.getvalue()


def save_docx(folder: Path, name: str, text: str) -> Path:
    """글을 워드 파일로 저장하고 그 자리를 돌려준다. 같은 이름이 있으면 덮어쓰지 않고 번호를 붙인다."""
    folder.mkdir(parents=True, exist_ok=True)
    base = safe_name(name)
    for number in range(1, 1000):
        path = folder / (f"{base}.docx" if number == 1 else f"{base} ({number}).docx")
        try:
            with path.open("xb") as file:  # 'x'로 열면 이미 있는 파일은 건드리지 못한다
                file.write(docx_bytes(text))
        except FileExistsError:
            continue
        return path
    raise OSError(f"'{base}'(으)로 저장할 이름을 찾지 못했습니다")

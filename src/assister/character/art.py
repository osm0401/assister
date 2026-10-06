"""임시 캐릭터 그림을 만든다.

진짜 그림이 준비되기 전까지 쓰는 자리 표시용이다. 옷마다 표정 PNG 한 세트와
소품 PNG를 만들어 pack.py가 읽는 폴더 구조로 저장한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from assister.character.pack import EXPRESSIONS, MANIFEST_NAME

CANVAS = (480, 640)
SCALE = 3  # 크게 그린 뒤 줄여서 가장자리를 부드럽게 한다.

SKIN = (255, 224, 196, 255)
HAIR = (92, 62, 48, 255)
LINE = (58, 42, 38, 255)
BLUSH = (255, 150, 140, 150)
WHITE = (255, 255, 255, 255)
MOUTH = (200, 80, 80, 255)

DEFAULT_NAME = "모모"


@dataclass(frozen=True)
class OutfitStyle:
    id: str
    label: str
    unlock_level: int
    body: tuple[int, int, int, int]
    accent: tuple[int, int, int, int]


@dataclass(frozen=True)
class PropStyle:
    id: str
    label: str
    unlock_level: int


OUTFITS = (
    OutfitStyle("basic", "주황 후드", 1, (242, 140, 56, 255), (255, 206, 150, 255)),
    OutfitStyle("cardigan", "하늘 가디건", 2, (110, 160, 220, 255), (236, 244, 255, 255)),
    OutfitStyle("dress", "분홍 원피스", 4, (240, 130, 160, 255), (255, 226, 234, 255)),
)

PROPS = (
    PropStyle("ribbon", "리본", 2),
    PropStyle("glasses", "안경", 3),
    PropStyle("headphones", "헤드폰", 5),
)


def _s(*values: float) -> tuple[int, ...]:
    return tuple(int(value * SCALE) for value in values)


def _new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", _s(*CANVAS), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def _finish(image: Image.Image) -> Image.Image:
    return image.resize(CANVAS, Image.LANCZOS)


def _draw_body(draw: ImageDraw.ImageDraw, style: OutfitStyle) -> None:
    # 뒷머리
    draw.ellipse(_s(100, 60, 380, 360), fill=HAIR)
    draw.rounded_rectangle(_s(104, 200, 376, 470), radius=60 * SCALE, fill=HAIR)
    # 몸과 팔
    draw.rounded_rectangle(_s(150, 350, 330, 610), radius=56 * SCALE, fill=style.body)
    draw.rounded_rectangle(_s(112, 372, 168, 540), radius=28 * SCALE, fill=style.body)
    draw.rounded_rectangle(_s(312, 372, 368, 540), radius=28 * SCALE, fill=style.body)
    draw.ellipse(_s(116, 516, 164, 564), fill=SKIN)
    draw.ellipse(_s(316, 516, 364, 564), fill=SKIN)
    # 옷깃과 장식
    draw.polygon([_s(196, 350), _s(240, 410), _s(284, 350)], fill=style.accent)
    draw.rounded_rectangle(_s(196, 500, 284, 540), radius=14 * SCALE, fill=style.accent)
    # 목과 얼굴
    draw.rectangle(_s(216, 318, 264, 362), fill=SKIN)
    draw.ellipse(_s(126, 96, 354, 344), fill=SKIN)
    # 앞머리
    draw.pieslice(_s(118, 70, 362, 300), start=180, end=360, fill=HAIR)
    draw.polygon([_s(126, 184), _s(170, 184), _s(140, 250)], fill=HAIR)
    draw.polygon([_s(354, 184), _s(310, 184), _s(340, 250)], fill=HAIR)


def _open_eyes(draw: ImageDraw.ImageDraw, *, radius: float = 15, look: tuple[float, float] = (0, 0)) -> None:
    for center_x in (196, 284):
        x, y = center_x + look[0], 226 + look[1]
        draw.ellipse(_s(x - radius, y - radius * 1.25, x + radius, y + radius * 1.25), fill=LINE)
        draw.ellipse(_s(x - radius * 0.2, y - radius * 0.9, x + radius * 0.45, y - radius * 0.25), fill=WHITE)


def _arc_eyes(draw: ImageDraw.ImageDraw, *, upward: bool) -> None:
    start, end = (180, 360) if upward else (0, 180)
    for center_x in (196, 284):
        draw.arc(_s(center_x - 18, 212, center_x + 18, 244), start=start, end=end, fill=LINE, width=5 * SCALE)


def _line_eyes(draw: ImageDraw.ImageDraw) -> None:
    for center_x in (196, 284):
        draw.line([_s(center_x - 17, 230), _s(center_x + 17, 230)], fill=LINE, width=5 * SCALE)


def _brows(draw: ImageDraw.ImageDraw, *, inner_lift: float) -> None:
    draw.line([_s(176, 194), _s(214, 194 - inner_lift)], fill=LINE, width=4 * SCALE)
    draw.line([_s(266, 194 - inner_lift), _s(304, 194)], fill=LINE, width=4 * SCALE)


def _smile(draw: ImageDraw.ImageDraw, *, width: float = 22, wide_open: bool = False) -> None:
    box = _s(240 - width, 262, 240 + width, 262 + width * 1.2)
    if wide_open:
        draw.pieslice(box, start=0, end=180, fill=MOUTH)
    else:
        draw.arc(box, start=10, end=170, fill=LINE, width=4 * SCALE)


def _draw_face(draw: ImageDraw.ImageDraw, expression: str) -> None:
    draw.ellipse(_s(150, 248, 190, 272), fill=BLUSH)
    draw.ellipse(_s(290, 248, 330, 272), fill=BLUSH)

    if expression == "smile":
        _arc_eyes(draw, upward=True)
        _smile(draw, width=26)
    elif expression == "thinking":
        _open_eyes(draw, look=(7, -6))
        _brows(draw, inner_lift=-8)
        draw.line([_s(226, 286), _s(256, 282)], fill=LINE, width=4 * SCALE)
        for index, size in enumerate((7, 10, 14)):
            x, y = 372 + index * 26, 150 - index * 30
            draw.ellipse(_s(x - size, y - size, x + size, y + size), fill=WHITE, outline=LINE, width=2 * SCALE)
    elif expression == "surprised":
        _open_eyes(draw, radius=19)
        _brows(draw, inner_lift=6)
        draw.ellipse(_s(226, 270, 254, 304), fill=MOUTH)
    elif expression == "sleepy":
        _line_eyes(draw)
        draw.ellipse(_s(232, 276, 248, 290), fill=MOUTH)
        for index, size in enumerate((16, 22, 30)):
            x, y = 366 + index * 24, 168 - index * 38
            draw.line([_s(x, y), _s(x + size, y), _s(x, y + size), _s(x + size, y + size)], fill=LINE, width=4 * SCALE)
    elif expression == "talking":
        _open_eyes(draw)
        draw.ellipse(_s(220, 266, 260, 300), fill=MOUTH)
    elif expression == "sad":
        _open_eyes(draw, look=(0, 4))
        _brows(draw, inner_lift=12)
        draw.arc(_s(220, 280, 260, 304), start=190, end=350, fill=LINE, width=4 * SCALE)
    elif expression == "excited":
        _arc_eyes(draw, upward=True)
        _smile(draw, width=30, wide_open=True)
        for x, y in ((106, 150), (378, 130), (400, 220)):
            draw.line([_s(x - 14, y), _s(x + 14, y)], fill=(255, 200, 60, 255), width=5 * SCALE)
            draw.line([_s(x, y - 14), _s(x, y + 14)], fill=(255, 200, 60, 255), width=5 * SCALE)
    else:
        _open_eyes(draw)
        _smile(draw)


def draw_expression(style: OutfitStyle, expression: str) -> Image.Image:
    image, draw = _new_canvas()
    _draw_body(draw, style)
    _draw_face(draw, expression)
    return _finish(image)


def draw_prop(prop_id: str) -> Image.Image:
    """캐릭터와 같은 크기의 투명한 판에 소품만 그린다. 그대로 겹치면 제자리에 놓인다."""
    image, draw = _new_canvas()
    if prop_id == "ribbon":
        red = (230, 70, 90, 255)
        draw.polygon([_s(300, 96), _s(350, 70), _s(350, 130)], fill=red)
        draw.polygon([_s(300, 96), _s(250, 70), _s(250, 130)], fill=red)
        draw.ellipse(_s(288, 84, 312, 108), fill=(190, 40, 60, 255))
    elif prop_id == "glasses":
        for center_x in (196, 284):
            draw.ellipse(_s(center_x - 30, 198, center_x + 30, 256), outline=LINE, width=4 * SCALE)
        draw.line([_s(226, 226), _s(254, 226)], fill=LINE, width=4 * SCALE)
    elif prop_id == "headphones":
        gray = (70, 74, 90, 255)
        draw.arc(_s(108, 52, 372, 330), start=180, end=360, fill=gray, width=12 * SCALE)
        draw.rounded_rectangle(_s(96, 180, 136, 268), radius=16 * SCALE, fill=gray)
        draw.rounded_rectangle(_s(344, 180, 384, 268), radius=16 * SCALE, fill=gray)
    else:
        raise ValueError(f"모르는 소품입니다: {prop_id}")
    return _finish(image)


def generate_placeholder_pack(root: Path, name: str = DEFAULT_NAME) -> Path:
    """임시 캐릭터 폴더를 통째로 만든다. 이미 있는 파일은 덮어쓴다."""
    for style in OUTFITS:
        folder = root / "outfits" / style.id
        folder.mkdir(parents=True, exist_ok=True)
        for expression in EXPRESSIONS:
            draw_expression(style, expression).save(folder / f"{expression}.png")

    props_folder = root / "props"
    props_folder.mkdir(parents=True, exist_ok=True)
    for prop in PROPS:
        draw_prop(prop.id).save(props_folder / f"{prop.id}.png")

    manifest = {
        "name": name,
        "default_outfit": OUTFITS[0].id,
        "outfits": {style.id: {"label": style.label, "unlock_level": style.unlock_level} for style in OUTFITS},
        "props": {prop.id: {"label": prop.label, "unlock_level": prop.unlock_level} for prop in PROPS},
    }
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return root

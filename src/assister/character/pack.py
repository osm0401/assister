"""캐릭터 그림 세트를 폴더에서 불러온다.

폴더 구조:
    characters/<캐릭터>/character.json          이름, 옷·소품 목록 (없어도 됨)
    characters/<캐릭터>/outfits/<옷>/<표정>.png   옷마다 표정 PNG 한 세트
    characters/<캐릭터>/props/<소품>.png          캐릭터 위에 겹쳐 그리는 소품

neutral.png만 있으면 동작하고, 없는 표정은 neutral.png로 대신한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EXPRESSIONS = ("neutral", "smile", "thinking", "surprised", "sleepy", "talking", "sad", "excited")
FALLBACK_EXPRESSION = "neutral"
MANIFEST_NAME = "character.json"


class PackError(Exception):
    """캐릭터 폴더를 쓸 수 없을 때."""


@dataclass(frozen=True)
class Outfit:
    id: str
    label: str
    unlock_level: int = 1


@dataclass(frozen=True)
class Prop:
    id: str
    label: str
    path: Path
    unlock_level: int = 1


@dataclass(frozen=True)
class Voice:
    """Edge TTS 목소리 설정. rate와 pitch는 '+10%', '-2Hz'처럼 적는다."""

    name: str = "ko-KR-SunHiNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"


@dataclass(frozen=True)
class CharacterPack:
    id: str
    name: str
    root: Path
    default_outfit: str
    outfits: tuple[Outfit, ...]
    props: tuple[Prop, ...] = ()
    persona: str = ""  # 말투 메모. 비어 있으면 AI 엔진 본래의 성격을 따른다.
    voice: Voice = Voice()
    wake_names: tuple[str, ...] = ()  # 이름 부르기에서 알아들을 이름들

    def image_path(self, outfit: str, expression: str) -> Path:
        """표정 PNG 경로. 없는 옷은 기본 옷으로, 없는 표정은 기본 표정으로 대신한다."""
        known = {item.id for item in self.outfits}
        folder = self.root / "outfits" / (outfit if outfit in known else self.default_outfit)
        wanted = folder / f"{expression}.png"
        return wanted if wanted.exists() else folder / f"{FALLBACK_EXPRESSION}.png"


def _read_manifest(root: Path) -> dict:
    path = root / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PackError(f"{path}을(를) 읽을 수 없습니다: {error}") from error
    if not isinstance(manifest, dict):
        raise PackError(f"{path}의 형식이 잘못됐습니다")
    return manifest


def list_packs(characters_root: Path) -> list[str]:
    """캐릭터 폴더들의 이름. 옷 폴더(outfits)가 있는 것만 캐릭터로 친다."""
    if not characters_root.is_dir():
        return []
    return sorted(path.name for path in characters_root.iterdir() if (path / "outfits").is_dir())


def save_profile(root: Path, profile: dict) -> None:
    """캐릭터의 이름·말투·목소리 같은 설정을 character.json에 적는다. 옷과 소품 목록은 그대로 둔다."""
    path = root / MANIFEST_NAME
    try:
        path.write_text(json.dumps({**_read_manifest(root), **profile}, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        raise PackError(f"{path}에 저장할 수 없습니다: {error}") from error


def load_pack(root: Path) -> CharacterPack:
    outfits_root = root / "outfits"
    if not outfits_root.is_dir():
        raise PackError(f"캐릭터 폴더를 찾을 수 없습니다: {outfits_root}")

    manifest = _read_manifest(root)
    described = manifest.get("outfits", {})
    folders = sorted(path.name for path in outfits_root.iterdir() if path.is_dir())
    # character.json에 적힌 순서를 먼저 따르고, 적히지 않은 폴더는 뒤에 붙인다.
    ordered = [name for name in described if name in folders] + [name for name in folders if name not in described]
    if not ordered:
        raise PackError(f"옷 폴더가 하나도 없습니다: {outfits_root}")

    for name in ordered:
        if not (outfits_root / name / f"{FALLBACK_EXPRESSION}.png").exists():
            raise PackError(f"'{name}' 옷에 {FALLBACK_EXPRESSION}.png가 없습니다: {outfits_root / name}")

    outfits = tuple(
        Outfit(
            id=name,
            label=described.get(name, {}).get("label", name),
            unlock_level=int(described.get(name, {}).get("unlock_level", 1)),
        )
        for name in ordered
    )
    props = tuple(
        Prop(
            id=prop_id,
            label=info.get("label", prop_id),
            path=root / "props" / f"{prop_id}.png",
            unlock_level=int(info.get("unlock_level", 1)),
        )
        for prop_id, info in manifest.get("props", {}).items()
        if (root / "props" / f"{prop_id}.png").exists()
    )

    default_outfit = manifest.get("default_outfit", ordered[0])
    if default_outfit not in ordered:
        default_outfit = ordered[0]

    name = manifest.get("name", root.name)
    persona = manifest.get("persona", "")
    if not isinstance(persona, str):
        raise PackError(f"{root / MANIFEST_NAME}의 persona는 글이어야 합니다")

    return CharacterPack(
        id=root.name,
        name=name,
        root=root,
        default_outfit=default_outfit,
        outfits=outfits,
        props=props,
        persona=persona,
        voice=_voice(manifest.get("voice", {}), root),
        wake_names=_wake_names(name, manifest.get("wake_aliases", []), root),
    )


def _voice(raw: object, root: Path) -> Voice:
    if not isinstance(raw, dict) or not all(isinstance(value, str) for value in raw.values()):
        raise PackError(f"{root / MANIFEST_NAME}의 voice는 name, rate, pitch를 글로 적어야 합니다")
    known = {key: raw[key] for key in ("name", "rate", "pitch") if raw.get(key)}
    return Voice(**known)


def _wake_names(name: str, aliases: object, root: Path) -> tuple[str, ...]:
    if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
        raise PackError(f"{root / MANIFEST_NAME}의 wake_aliases는 글 목록이어야 합니다")
    # 순서를 지키면서 겹치는 이름을 뺀다.
    return tuple(dict.fromkeys(item.strip() for item in [name, *aliases] if item.strip()))

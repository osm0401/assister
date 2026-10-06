import json

import pytest

from assister.character.art import generate_placeholder_pack
from assister.character.pack import EXPRESSIONS, PackError, Voice, load_pack


def make_pack(root, *, outfits=("basic",), expressions=("neutral",), manifest=None):
    for outfit in outfits:
        folder = root / "outfits" / outfit
        folder.mkdir(parents=True)
        for expression in expressions:
            (folder / f"{expression}.png").write_bytes(b"png")
    if manifest is not None:
        (root / "character.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_loads_name_and_outfits_from_manifest(tmp_path):
    manifest = {
        "name": "모모",
        "default_outfit": "basic",
        "outfits": {"basic": {"label": "기본 옷"}, "dress": {"label": "원피스", "unlock_level": 3}},
    }
    root = make_pack(tmp_path / "momo", outfits=("basic", "dress"), manifest=manifest)

    pack = load_pack(root)

    assert pack.name == "모모"
    assert pack.default_outfit == "basic"
    assert [(o.id, o.label, o.unlock_level) for o in pack.outfits] == [("basic", "기본 옷", 1), ("dress", "원피스", 3)]


def test_folder_without_manifest_still_loads(tmp_path):
    root = make_pack(tmp_path / "custom", expressions=("neutral", "smile"))

    pack = load_pack(root)

    assert pack.name == "custom"
    assert pack.default_outfit == "basic"


def test_missing_expression_falls_back_to_neutral(tmp_path):
    pack = load_pack(make_pack(tmp_path / "p", expressions=("neutral", "smile")))

    assert pack.image_path("basic", "smile").name == "smile.png"
    assert pack.image_path("basic", "sleepy").name == "neutral.png"


def test_unknown_outfit_falls_back_to_default(tmp_path):
    pack = load_pack(make_pack(tmp_path / "p"))

    assert pack.image_path("no-such-outfit", "neutral").parent.name == "basic"


def test_pack_without_neutral_image_is_rejected(tmp_path):
    root = make_pack(tmp_path / "p", expressions=("smile",))

    with pytest.raises(PackError, match="neutral.png"):
        load_pack(root)


def test_missing_folder_is_rejected(tmp_path):
    with pytest.raises(PackError, match="찾을 수 없습니다"):
        load_pack(tmp_path / "nowhere")


def test_persona_voice_and_wake_names_default_to_engine_and_character_name(tmp_path):
    pack = load_pack(make_pack(tmp_path / "p", manifest={"name": "모모"}))

    assert pack.persona == ""
    assert pack.voice == Voice()
    assert pack.wake_names == ("모모",)


def test_persona_voice_and_wake_aliases_are_read_per_character(tmp_path):
    manifest = {
        "name": "모모",
        "persona": "반말로, 장난기 있게",
        "voice": {"name": "ko-KR-InJoonNeural", "pitch": "+4Hz"},
        "wake_aliases": ["뭐뭐", "모모", " 모모짱 "],
    }

    pack = load_pack(make_pack(tmp_path / "p", manifest=manifest))

    assert pack.persona == "반말로, 장난기 있게"
    assert pack.voice == Voice(name="ko-KR-InJoonNeural", rate="+0%", pitch="+4Hz")
    assert pack.wake_names == ("모모", "뭐뭐", "모모짱")


@pytest.mark.parametrize(
    "manifest",
    [{"persona": 3}, {"voice": "sunhi"}, {"voice": {"rate": 10}}, {"wake_aliases": "모모"}, {"wake_aliases": [1]}],
)
def test_malformed_character_settings_are_rejected(tmp_path, manifest):
    with pytest.raises(PackError, match="character.json"):
        load_pack(make_pack(tmp_path / "p", manifest=manifest))


def test_placeholder_art_makes_a_complete_pack(tmp_path):
    root = generate_placeholder_pack(tmp_path / "default")

    pack = load_pack(root)

    assert len(pack.outfits) == 3
    for outfit in pack.outfits:
        for expression in EXPRESSIONS:
            assert pack.image_path(outfit.id, expression).name == f"{expression}.png"
    assert {prop.id for prop in pack.props} == {"ribbon", "glasses", "headphones"}
    assert all(prop.path.exists() for prop in pack.props)

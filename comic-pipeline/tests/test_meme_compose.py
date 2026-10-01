import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import meme_compose
import pipeline_config

FONT_PATH = Path(r"C:\Windows\Fonts\impact.ttf")
if not FONT_PATH.is_file():
    FONT_PATH = Path(r"C:\Windows\Fonts\Impact.ttf")

requires_font = pytest.mark.skipif(
    not FONT_PATH.is_file(), reason="Impact font not available on this machine"
)


def test_layout_boxes_top_and_bottom_for_two_boxes():
    boxes = meme_compose.layout_boxes(2, (400, 300))
    assert len(boxes) == 2
    top, bottom = boxes
    assert top.y0 < bottom.y0
    assert top.x0 == bottom.x0
    assert top.x1 == bottom.x1


def test_layout_boxes_single_box_is_top_only():
    boxes = meme_compose.layout_boxes(1, (400, 300))
    assert len(boxes) == 1


def test_layout_boxes_rejects_zero_boxes():
    with pytest.raises(ValueError):
        meme_compose.layout_boxes(0, (400, 300))


def test_layout_boxes_stacks_extra_boxes_between_top_and_bottom():
    boxes = meme_compose.layout_boxes(4, (400, 300))
    assert len(boxes) == 4
    ys = [box.y0 for box in boxes]
    assert ys == sorted(ys)


def test_resolve_font_path_uses_explicit_path():
    assert meme_compose.resolve_font_path(FONT_PATH) == FONT_PATH


def test_resolve_font_path_raises_for_missing_explicit_file(tmp_path):
    with pytest.raises(pipeline_config.ConfigError):
        meme_compose.resolve_font_path(tmp_path / "does-not-exist.ttf")


@requires_font
def test_compose_draws_captions_and_skips_empty_boxes(tmp_path):
    base_image = tmp_path / "base.png"
    Image.new("RGB", (400, 300), color=(10, 10, 10)).save(base_image)

    captions = [
        {"box_index": 0, "text": "top text"},
        {"box_index": 1, "text": ""},
    ]
    output_path = tmp_path / "out.png"
    result = meme_compose.compose(
        base_image, captions, output_path=output_path, box_count=2, font_path=FONT_PATH
    )

    assert result == output_path.resolve()
    output = Image.open(output_path)
    assert output.size == (400, 300)

    boxes = meme_compose.layout_boxes(2, (400, 300))

    def band_has_white_pixel(box):
        cropped = output.crop((box.x0, box.y0, box.x1, box.y1))
        return any(pixel == (255, 255, 255) for pixel in cropped.getdata())

    assert band_has_white_pixel(boxes[0])
    assert not band_has_white_pixel(boxes[1])  # empty caption, band left untouched


@requires_font
def test_compose_is_atomic_and_leaves_no_temp_files(tmp_path):
    base_image = tmp_path / "base.png"
    Image.new("RGB", (200, 150), color=(0, 0, 0)).save(base_image)
    output_path = tmp_path / "out.png"
    meme_compose.compose(
        base_image,
        [{"box_index": 0, "text": "HELLO"}],
        output_path=output_path,
        box_count=1,
        font_path=FONT_PATH,
    )
    assert list(tmp_path.glob(".*.tmp")) == []

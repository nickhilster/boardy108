import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compose_logo_insert


def test_logo_is_inserted_exactly_and_old_marks_are_covered(tmp_path):
    render = tmp_path / "render.png"
    Image.new("RGB", (200, 200), (30, 30, 30)).save(render)
    logo = tmp_path / "logo.png"
    mark = Image.new("RGBA", (40, 50), (0, 0, 0, 0))
    mark.paste(Image.new("RGBA", (20, 30), (200, 10, 10, 255)), (10, 10))
    mark.save(logo)
    result = compose_logo_insert.compose(render, logo, (50, 50, 150, 150), tmp_path / "out.png")
    assert result["opaque_pixel_mismatches"] == 0
    assert result["insert_size"] == [80, 100]
    assert (tmp_path / "out.insert.json").is_file()

"""Place the official Boardy logo on a rendered slate as a literal flat insert.

The logo is never regenerated: it is resized proportionally (Lanczos) and alpha-
composited once. The destination box is first flattened to the slate colour so
nothing a diffusion model drew there shows through the logo's transparency.
Verification re-reads the result and checks every fully opaque logo pixel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compose(render: Path, logo: Path, box: tuple[int, int, int, int], output: Path) -> dict:
    left, top, right, bottom = box
    base = Image.open(render).convert("RGBA")
    mark = Image.open(logo).convert("RGBA")
    scale = min((right - left) / mark.width, (bottom - top) / mark.height)
    size = (max(1, round(mark.width * scale)), max(1, round(mark.height * scale)))
    scaled = mark.resize(size, Image.LANCZOS)
    x, y = left + (right - left - size[0]) // 2, top + (bottom - top - size[1]) // 2

    # Flat slate colour sampled from clean slate pixels beside the box.
    samples = [base.getpixel((px, py))[:3] for px in (left - 4, right + 4) for py in range(top, bottom, 8)]
    slate = tuple(sorted(channel)[len(channel) // 2] for channel in zip(*samples))
    base.paste(Image.new("RGBA", (right - left, bottom - top), slate + (255,)), (left, top))
    base.alpha_composite(scaled, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(output)

    result = Image.open(output).convert("RGBA")
    crop = result.crop((x, y, x + size[0], y + size[1]))
    mismatches = sum(
        1
        for a, b in zip(crop.getdata(), scaled.getdata())
        if b[3] == 255 and max(abs(a[i] - b[i]) for i in range(3)) > 1
    )
    record = {
        "render": str(render), "render_sha256": _sha(render),
        "logo": str(logo), "logo_sha256": _sha(logo), "logo_size": list(mark.size),
        "insert_box": list(box), "insert_position": [x, y], "insert_size": list(size),
        "slate_fill_rgb": list(slate), "output": str(output), "output_sha256": _sha(output),
        "opaque_pixel_mismatches": mismatches,
    }
    if mismatches:
        raise RuntimeError(f"Logo fidelity check failed: {mismatches} opaque pixels differ")
    output.with_suffix(".insert.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", required=True, type=Path)
    parser.add_argument("--logo", required=True, type=Path)
    parser.add_argument("--box", required=True, type=int, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(compose(args.render, args.logo, tuple(args.box), args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Rasterize a compact Stack-chan flag pack from the flag-icons SVG set."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Path to flag-icons flags/4x3 directory")
    parser.add_argument("--config", default="config/stackchan_flag_pack.json")
    parser.add_argument(
        "--output",
        default="mod/assets/flags",
    )
    args = parser.parse_args()

    source_dir = Path(args.source).expanduser().resolve()
    config = json.loads(Path(args.config).expanduser().resolve().read_text(encoding="utf-8"))
    output_dir = Path(args.output).expanduser().resolve()
    width = int(config.get("width") or 24)
    height = int(config.get("height") or 18)
    border = max(0, int(config.get("border") or 0))
    overlays = config.get("overlays") or {}
    if not isinstance(overlays, dict):
        raise ValueError("flag pack overlays must be an object")
    codes = [str(code).lower() for code in config.get("codes") or []]
    magick = shutil.which("magick")
    if not magick:
        raise RuntimeError("ImageMagick 'magick' is required")
    if not codes:
        raise ValueError("flag pack config must contain country codes")

    output_dir.mkdir(parents=True, exist_ok=True)
    for code in codes:
        source = source_dir / f"{code}.svg"
        if not source.exists():
            raise FileNotFoundError(f"flag source not found: {source}")
        target = output_dir / f"flag-{code}.png"
        command = [
            magick,
            str(source),
            "-background",
            "none",
            "-resize",
            f"{width}x{height}!",
            "-bordercolor",
            "white",
            "-border",
            str(border),
        ]
        overlay = overlays.get(code)
        if overlay:
            if not isinstance(overlay, dict) or not overlay.get("draw"):
                raise ValueError(f"{code}: overlay must contain a draw command")
            command.extend(
                [
                    "-stroke",
                    str(overlay.get("stroke") or "none"),
                    "-strokewidth",
                    str(float(overlay.get("stroke_width") or 1)),
                    "-fill",
                    str(overlay.get("fill") or "none"),
                    "-draw",
                    str(overlay["draw"]),
                ]
            )
        command.extend(["-alpha", "off", "-depth", "8", "-strip", f"PNG24:{target}"])
        subprocess.run(command, check=True)
        print(f"generated {target.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

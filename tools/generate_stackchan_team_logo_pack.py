#!/usr/bin/env python3
"""Rasterize team logos and PK-bar icons for the Stack-chan probability bar.

Team logos download from the ESPN CDN and land next to the country flags as
``flag-<code>.png`` (22x18 with transparency), so the device's existing flag
mechanism renders them without any mod change. PK-bar center icons (the
football replacement per sport) are drawn from vector recipes in the config.
Requires ImageMagick's ``magick``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

USER_AGENT = "stackchan-matchday-logos/1.0"


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        target.write_bytes(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/stackchan_team_logo_pack.json")
    parser.add_argument("--flags-output", default="mod/assets/flags")
    parser.add_argument("--icons-output", default="mod/assets/ui")
    parser.add_argument("--only", default="",
                        help="comma-separated codes to regenerate (default all)")
    args = parser.parse_args()

    magick = shutil.which("magick")
    if not magick:
        raise RuntimeError("ImageMagick 'magick' is required")
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    # Must match the device's FLAG_WIDTH x FLAG_HEIGHT (24x20): the flag skin
    # samples exactly that region, and an undersized texture reads out of
    # bounds and renders as garbage bars.
    width = int(config.get("width") or 24)
    height = int(config.get("height") or 20)
    inner_width = int(config.get("inner_width") or width)
    inner_height = int(config.get("inner_height") or height)
    template = str(config.get("source_template") or "")
    only = {code.strip() for code in args.only.split(",") if code.strip()}

    flags_dir = Path(args.flags_output)
    flags_dir.mkdir(parents=True, exist_ok=True)
    for code, path in (config.get("teams") or {}).items():
        if only and code not in only:
            continue
        url = template.format(path=path)
        target = flags_dir / f"flag-{code}.png"
        with tempfile.NamedTemporaryFile(suffix=".png") as raw:
            download(url, Path(raw.name))
            # Opaque white tiles on purpose, matching the country-flag format
            # (PNG24, no alpha): the device compiles low-color art into 2-bit
            # palette bitmaps, and palettized textures with an alpha channel
            # render blank on the CoreS3. Verified on device 2026-07-20.
            subprocess.run(
                [
                    magick,
                    Path(raw.name).as_posix(),
                    "-background", "white",
                    "-resize", f"{inner_width}x{inner_height}",
                    "-gravity", "center",
                    "-extent", f"{width}x{height}",
                    "-alpha", "remove",
                    "-alpha", "off",
                    "-strip",
                    f"PNG24:{target}",
                ],
                check=True,
            )
        print(f"generated {target.name}")

    icons_dir = Path(args.icons_output)
    icons_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in (config.get("icons") or {}).items():
        if only and name not in only:
            continue
        size = int(spec.get("size") or 24)
        command = [magick, "-size", f"{size}x{size}", "xc:none"]
        for layer in spec.get("draw") or []:
            command.extend(
                [
                    "-fill", str(layer.get("fill") or "none"),
                    "-stroke", str(layer.get("stroke") or "none"),
                    "-strokewidth", str(float(layer.get("stroke_width") or 1)),
                ]
            )
            for draw in layer.get("commands") or []:
                command.extend(["-draw", str(draw)])
        target = icons_dir / f"{name}.png"
        command.extend(["-strip", f"PNG32:{target}"])
        subprocess.run(command, check=True)
        print(f"generated {target.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

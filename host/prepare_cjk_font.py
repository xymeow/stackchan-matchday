#!/usr/bin/env python3
"""Prepare the optional StackChanCN-24 CJK font inside a stack-chan checkout.

The font TTF itself is never committed (system fonts are not redistributable):
this script copies one from the local machine and regenerates the GB2312
character file the host manifest references. Run it again after cloning a
fresh checkout.

Usage:
    python3 host/prepare_cjk_font.py /path/to/stack-chan [--ttf /path/to/font.ttf]

The default TTF is macOS Arial Unicode, which reproduces the original
Stack-chan Kalshi companion look. Any CJK-capable TTF works, for example
Noto Sans SC.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

DEFAULT_TTF_CANDIDATES = [
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
]


def make_gb2312_chars() -> str:
    chars: list[str] = []
    for high in range(0xA1, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                chars.append(bytes([high, low]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    extra = "".join(chr(code) for code in range(32, 127))
    extra += "，。！？、：；（）《》「」『』【】—…·￥～\n"
    return "".join(dict.fromkeys(extra + "".join(chars)))


def find_fonts_dir(root: Path) -> Path:
    for candidate in (root / "firmware" / "stackchan", root / "stackchan"):
        if (candidate / "manifest.json").exists():
            return candidate / "assets" / "fonts"
    raise SystemExit(f"error: {root} does not look like a stack-chan checkout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", help="Path to the stack-chan repo root")
    parser.add_argument("--ttf", help="CJK-capable TTF to use (default: macOS Arial Unicode)")
    args = parser.parse_args()

    fonts_dir = find_fonts_dir(Path(args.checkout).expanduser().resolve())
    fonts_dir.mkdir(parents=True, exist_ok=True)

    sources = [Path(args.ttf).expanduser()] if args.ttf else DEFAULT_TTF_CANDIDATES
    source = next((path for path in sources if path.exists()), None)
    if source is None:
        print("error: no usable CJK TTF found; pass --ttf /path/to/font.ttf", file=sys.stderr)
        return 2

    target = fonts_dir / "StackChanCN.ttf"
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copyfile(source, target)

    chars_path = fonts_dir / "gb2312-chars.txt"
    chars_path.write_text(make_gb2312_chars(), encoding="utf-8")

    print(f"font: {target} (from {source})")
    print(f"chars: {chars_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

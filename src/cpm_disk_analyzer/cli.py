"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .analyzer import analyze_image
from .containers import ImageFormatError
from .profiles import load_profiles
from .report import as_json, as_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpm-disk-analyzer",
        description="Identify and inspect CP/M disk images without modifying them.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="analyze one disk image")
    analyze.add_argument("image", type=Path)
    analyze.add_argument("--profile", help="force one known profile ID")
    analyze.add_argument("--show-files", action="store_true", help="include CP/M directory extents")
    analyze.add_argument("--json", dest="json_path", type=Path, help="write a JSON report")

    subparsers.add_parser("profiles", help="list known disk profiles")
    subparsers.add_parser("gui", help="open the desktop GUI")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "profiles":
        for profile in load_profiles():
            print(f"{profile.id:<18} {profile.image_size:>8} bytes  {profile.name}")
        return 0
    if args.command == "gui":
        from .gui import main as gui_main

        gui_main()
        return 0

    try:
        result = analyze_image(args.image, args.profile)
    except (OSError, ImageFormatError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(as_text(result, show_files=args.show_files))
    if args.json_path:
        args.json_path.write_text(as_json(result) + "\n", encoding="utf-8")
        print(f"\nJSON report written to {args.json_path}")
    return 0 if result.candidates else 1


if __name__ == "__main__":
    raise SystemExit(main())


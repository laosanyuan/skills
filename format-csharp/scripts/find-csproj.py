#!/usr/bin/env python3
"""List ancestor .csproj candidates for a .cs file; never claim Compile membership.

Exit 0: one unverified candidate. Exit 1: invalid input/read error.
Exit 2: no ancestor candidates. Exit 3: multiple candidates, selection required.
Linked files may belong to projects outside the ancestor chain; inspect evaluated
MSBuild Compile items before formatting. No project is selected arbitrarily.
Use --search-root to bound discovery to an authorized directory. Omitting it
preserves the legacy search through all ancestors, up to the filesystem root.
"""

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def find_candidates(cs_file: Path, search_root: Path | None = None) -> list[Path]:
    candidates = set()
    for directory in (cs_file.parent, *cs_file.parent.parents):
        for child in directory.iterdir():
            if child.is_file() and child.suffix.casefold() == ".csproj":
                candidate = child.resolve()
                if search_root is not None and not candidate.is_relative_to(search_root):
                    raise ValueError(f"Project candidate resolves outside --search-root: {child}")
                candidates.add(candidate)
        if directory == search_root:
            break
    return sorted(candidates, key=lambda path: (str(path).casefold(), str(path)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("cs_file", help="Existing .cs file")
    parser.add_argument("--json", action="store_true", help="Emit structured candidates")
    parser.add_argument("--search-root", help="Authorized ancestor directory (inclusive); "
                        "default preserves legacy search through all ancestors")
    args = parser.parse_args()
    try:
        path = Path(args.cs_file).resolve(strict=True)
        if not path.is_file() or path.suffix.casefold() != ".cs":
            raise ValueError(f"Expected a .cs file: {path}")
        if args.search_root is not None and not args.search_root.strip():
            raise ValueError("--search-root must name an existing directory")
        search_root = (Path(args.search_root).resolve(strict=True)
                       if args.search_root is not None else None)
        if search_root is not None:
            if not search_root.is_dir():
                raise ValueError(f"--search-root must be a directory: {search_root}")
            if not path.is_relative_to(search_root):
                raise ValueError(f"Input file is outside --search-root: {path}")
        candidates = find_candidates(path, search_root)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"file": str(path), "membership_verified": False,
                          "search_root": str(search_root) if search_root is not None else None,
                          "candidates": [str(candidate) for candidate in candidates]},
                         ensure_ascii=False, indent=2))
    else:
        for candidate in candidates:
            print(candidate)
    print("Candidates only: inspect evaluated Compile items, including Remove/Link "
          "and conditional items. Linked owners outside ancestor folders are not listed.",
          file=sys.stderr)
    if not candidates:
        return 2
    return 0 if len(candidates) == 1 else 3


if __name__ == "__main__":
    sys.exit(main())

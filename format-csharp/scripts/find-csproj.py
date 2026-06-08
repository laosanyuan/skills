#!/usr/bin/env python3
# Requires: Python 3.10+ (uses PEP 604 `X | None` type unions)
"""
Find the nearest .csproj that contains the given .cs file by walking up
the directory tree.

`dotnet format` cannot operate on a bare .cs file — it needs project context
(.editorconfig, analyzers, references). When the user gives a single .cs file,
this script returns the .csproj that should be passed to
`dotnet format <csproj> --include <cs-file>`.

Exit codes:
    0  found, .csproj path on stdout
    1  input path doesn't exist
    2  no .csproj found in any parent directory

Example:
    python find-csproj.py path/to/Foo.cs
    # → path/to/MyApp.csproj
"""

import argparse
import sys
from pathlib import Path


def find_csproj(cs_file: Path) -> Path | None:
    """Walk up from cs_file's directory and return the first .csproj found."""
    start = cs_file.parent if cs_file.is_file() else cs_file
    for d in [start, *start.parents]:
        for child in d.iterdir():
            if child.suffix.lower() == ".csproj":
                return child
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("cs_file", help="Path to the .cs file (absolute or relative)")
    args = p.parse_args()

    cs_file = Path(args.cs_file)
    if not cs_file.exists():
        print(f"Path not found: {cs_file}", file=sys.stderr)
        return 1

    csproj = find_csproj(cs_file.resolve())
    if csproj is None:
        print(f"No .csproj found in any parent directory of '{cs_file}'", file=sys.stderr)
        return 2

    print(csproj)
    return 0


if __name__ == "__main__":
    sys.exit(main())

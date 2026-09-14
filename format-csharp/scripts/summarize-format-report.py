#!/usr/bin/env python3
"""Validate and summarize a specific dotnet format JSON report.

Use --path for the report from the current invocation; a valid [] records no
changes, but does not establish that dotnet completed successfully.
Exit 0: valid report, 1: read/decode failure, 2: malformed JSON/schema.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from scan_scope import read_source

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def summarize(report: object) -> list[dict]:
    if not isinstance(report, list):
        raise ValueError("Report root must be a JSON array")
    by_file: dict[str, list[dict]] = {}
    for index, entry in enumerate(report):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry {index} must be an object")
        path = entry.get("FilePath")
        changes = entry.get("FileChanges")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"Entry {index}: FilePath must be a nonempty string")
        if not isinstance(changes, list):
            raise ValueError(f"Entry {index}: FileChanges must be an array")
        for number, change in enumerate(changes):
            if not isinstance(change, dict):
                raise ValueError(f"Entry {index} change {number} must be an object")
            diagnostic = change.get("DiagnosticId")
            if not isinstance(diagnostic, str) or not diagnostic.strip():
                raise ValueError(f"Entry {index} change {number}: invalid DiagnosticId")
        if changes:
            by_file.setdefault(path, []).extend(changes)
    grouped = []
    for path, changes in sorted(by_file.items()):
        counts = dict(sorted(Counter(change["DiagnosticId"] for change in changes).items()))
        grouped.append({"file": path, "total_changes": len(changes), "diagnostics": counts,
                        "diagnostics_summary": ", ".join(f"{key} x{count}" for key, count in counts.items())})
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--path", required=True, help="Report from the current dotnet invocation")
    parser.add_argument("--json", action="store_true", help="Emit structured grouped output")
    args = parser.parse_args()
    try:
        raw = read_source(Path(args.path))
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    try:
        grouped = summarize(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as error:
        print(f"Invalid format report: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(grouped, ensure_ascii=False, indent=2))
    elif not grouped:
        print("Report records no formatting changes; check the dotnet exit status separately")
    else:
        for group in grouped:
            print(f"{group['file']}  {group['diagnostics_summary']}")
        print(f"{sum(group['total_changes'] for group in grouped)} reported change(s) "
              f"across {len(grouped)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

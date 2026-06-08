#!/usr/bin/env python3
# Requires: Python 3.10+ (uses `list[X]` / PEP 604 unions in type hints)
"""
Summarize the JSON report from `dotnet format --report`.

`dotnet format` runs in three sub-stages (whitespace / style / analyzers) and
`--report` emits a JSON array where the same file can appear up to three times,
once per stage. This script:
    - reads format-report.json
    - groups entries by FilePath
    - aggregates DiagnosticIds per file ("WHITESPACE x12, ENDOFLINE x6")
    - prints a clean human-readable summary

Use this after `dotnet format <target> --verify-no-changes --report $env:TEMP`
(Windows) or `--report /tmp` (Linux/macOS) to show the user what would change
before confirming the actual format pass.

Example:
    python summarize-format-report.py                    # reads default path
    python summarize-format-report.py --path path/to/report.json
    python summarize-format-report.py --json             # structured output
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def default_report_path() -> Path:
    """TEMP/format-report.json — TEMP picked the OS-native way."""
    tmp = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
    return Path(tmp) / "format-report.json"


def summarize(report: list[dict]) -> list[dict]:
    """
    Group entries by FilePath, aggregate DiagnosticIds.
    Returns one dict per unique file, sorted by FilePath.
    """
    by_file: dict[str, list[dict]] = {}
    for entry in report:
        path = entry.get("FilePath", "")
        by_file.setdefault(path, []).extend(entry.get("FileChanges", []))

    result = []
    for path in sorted(by_file.keys()):
        changes = by_file[path]
        diag_counter = Counter(c.get("DiagnosticId", "?") for c in changes)
        result.append({
            "file": path,
            "total_changes": len(changes),
            "diagnostics": dict(diag_counter),
            "diagnostics_summary": ", ".join(
                f"{k} x{v}" for k, v in sorted(diag_counter.items())
            ),
        })
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--path", default=str(default_report_path()),
                   help="Path to format-report.json (default: $TEMP/format-report.json)")
    p.add_argument("--json", action="store_true",
                   help="Output structured JSON instead of human-readable text")
    args = p.parse_args()

    report_path = Path(args.path)
    if not report_path.exists():
        print(f"Report file not found: {report_path}", file=sys.stderr)
        return 1

    try:
        raw = report_path.read_text(encoding="utf-8-sig")  # tolerates optional BOM
        if not raw.strip() or raw.strip() == "[]":
            if args.json:
                print("[]")
            else:
                print("[OK] No formatting changes needed (report is empty).")
            return 0
        report = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Cannot parse JSON: {e}", file=sys.stderr)
        return 2

    grouped = summarize(report)

    if args.json:
        print(json.dumps(grouped, ensure_ascii=False, indent=2))
        return 0

    # Human-readable
    n_files = len(grouped)
    print()
    print(f"[*] {n_files} file(s) need formatting:")
    print()

    max_path_len = min(60, max((len(g["file"]) for g in grouped), default=0))
    for g in grouped:
        path = g["file"]
        if len(path) > max_path_len:
            shown = "..." + path[-(max_path_len - 3):]
        else:
            shown = path.ljust(max_path_len)
        print(f"  {shown}  {g['diagnostics_summary']}")

    total = sum(g["total_changes"] for g in grouped)
    print()
    print(f"Total: {total} diagnostic(s) across {n_files} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

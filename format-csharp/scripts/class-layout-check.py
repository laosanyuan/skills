#!/usr/bin/env python3
# Requires: Python 3.10+ (uses `list[X]` / PEP 604 unions in type hints)
"""
C# class-body layout and summary candidates, not verified violations.
Heuristic parser: braces in strings/comments, complex declarations, nested types,
partial aggregation and preprocessor branches require manual review.

    python class-layout-check.py --scope path/to/src
    python class-layout-check.py --scope path/to/src --include-from changed.txt --jobs 4
"""

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from scan_scope import add_scan_arguments, read_source, resolve_cs_files, write_report

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


# -- Canonical member order (must match 代码规范.md "代码布局") ---------------

CANONICAL_ORDER = [
    "PrivateFields",    # 1. 私有字段 (含 const / static readonly)
    "Properties",       # 2. 属性
    "Events",           # 3. 事件
    "Constructors",     # 4. 构造函数
    "PublicMethods",    # 5. 公有方法
    "PrivateMethods",   # 6. 私有方法
]

CANONICAL_REGION_NAME = {
    "PrivateFields":  "[Private Fields]",
    "Properties":     "[Properties]",
    "Events":         "[Events]",
    "Constructors":   "[Constructors]",
    "PublicMethods":  "[Public Methods]",
    "PrivateMethods": "[Private Methods]",
}

SPACE_REQUIRED_KINDS = {"Properties", "PublicMethods", "PrivateMethods"}


# -- Member kind classification (heuristic, line-level) ----------------------

# Strip leading attributes like [Obsolete] so they don't confuse classification.
_ATTR_PREFIX = re.compile(r"^\s*(\[[^\]]+\]\s*)+")
_MEMBER_MODIFIERS = "public|private|protected|internal|static|virtual|override|new|abstract|sealed|readonly|partial|async|extern|unsafe|volatile|const|required|file"


def classify_member(line: str, class_name: str) -> str | None:
    """
    Given a single line, return its group key (PrivateFields / Properties /
    Events / Constructors / PublicMethods / PrivateMethods) or None if the line
    isn't a member declaration.
    """
    # Strip leading whitespace + attributes
    stripped = _ATTR_PREFIX.sub("", line.lstrip())

    # Skip non-declarations
    if not stripped or stripped.startswith(("//", "/*", "*", "{", "}")):
        return None
    if stripped.startswith("#"):
        return "directive"  # #region/#endregion/#if etc

    # Event:  (modifiers)+ event Type Name
    if re.search(r"\bevent\b", stripped):
        return "Events"

    # Constructor:  (modifiers)*  ClassName(...
    # Static constructor:  static ClassName(...
    # Destructor:  ~ClassName(...
    if class_name:
        if re.match(rf"^\s*({_MEMBER_MODIFIERS}|\s)*\s*{re.escape(class_name)}\s*\(", stripped):
            return "Constructors"
        if re.match(rf"^\s*~{re.escape(class_name)}\s*\(", stripped):
            return "Constructors"

    # Property / Method / Field — strip leading modifiers, then disambiguate
    # by matching `TYPE NAME (body)` with lazy TYPE.
    # Modifiers may be omitted (implicit private class members and interface
    # declarations); parse_file limits classification to class-body depth.
    has_modifier = re.match(rf"^\s*(?:(?:{_MEMBER_MODIFIERS})\s+)+", stripped)
    after_modifiers = stripped[has_modifier.end():] if has_modifier else stripped
    if not after_modifiers:
        return None

    # `TYPE NAME body` — TYPE may contain anything (generics, tuples, nullable,
    # arrays); NAME is the LAST identifier before the body opener. Lazy `.+?`
    # ensures we don't grab `(` from inside a tuple type like
    # `List<(int x, int y)>? _foo;` as the body opener.
    match = re.match(
        r"^(.+?)\s+(\w+)\s*(\(|\{|=>|;|=(?!=|>))",
        after_modifiers,
    )
    if not match:
        # No body opener — may be a multi-line block property head
        # (handled in parse_file lookahead); not classifiable here.
        return None
    _type_part, _name, body = match.groups()
    if body == "(":
        # Method
        if re.search(r"\b(public|internal)\b", stripped):
            return "PublicMethods"
        return "PrivateMethods"
    if body in ("{", "=>"):
        return "Properties"
    # body in ("=", ";") → field declaration
    return "PrivateFields"

# -- Data structures ---------------------------------------------------------

@dataclass
class RegionBlock:
    name: str
    start_line: int
    end_line: int = 0


@dataclass
class Member:
    line: int
    kind: str
    region: str | None
    text: str
    end_line: int = 0


@dataclass
class ClassRecord:
    file: str
    class_name: str
    kind: str  # class/record/struct/interface
    start_line: int
    end_line: int = 0
    members: list[Member] = field(default_factory=list)
    region_blocks: list[RegionBlock] = field(default_factory=list)


# -- Parse one .cs file: return list of ClassRecord ---------------------------

_CLASS_DECL = re.compile(
    rf"^\s*({_MEMBER_MODIFIERS}|\s)*\s*(class|record|struct|interface)\s+(\w+)"
)


# Multi-line block property head: `public int Name` with nothing after the name —
# no `(` (would be method), no `{` / `=>` / `;` (would be auto-prop / expr-bodied /
# field). Next non-blank line is expected to be `{` (the property body opener).
_MULTILINE_PROP_HEAD = re.compile(
    rf"^\s*(?:{_MEMBER_MODIFIERS}\s+)+[\w<>,\[\]\?\s\.]+\s+\w+\s*$"
)


def parse_file(path: Path, lines: list[str] | None = None) -> list[ClassRecord]:
    if lines is None:
        lines = read_source(path).splitlines()

    classes: list[ClassRecord] = []
    current: ClassRecord | None = None
    # `pending` = (decl_line, name, kind, brace_depth_at_decl) — class declaration
    # detected but body not yet seen. Promoted to `current` on `{`; dropped on `;`
    # (positional `record Foo(...);` / forward `partial class Foo;`).
    pending: tuple[int, str, str, int] | None = None
    brace_depth = 0
    paren_depth = 0  # track `(` / `)` to skip multi-line method signature continuations
    in_class_depth = -1  # depth at which class body opened
    current_region: str | None = None
    region_stack: list[RegionBlock] = []
    # True when we're inside an expression-bodied member that spans multiple
    # lines (e.g. `public bool Equals(...) =>\n    other is not null && ...;`).
    # Lines while True are body continuations — DON'T classify them.
    in_expr_body = False

    for i, line in enumerate(lines):
        lineno = i + 1
        stripped = line.lstrip()
        opens = line.count("{")
        closes = line.count("}")
        # paren depth BEFORE this line — only classify when we're at the
        # top of a fresh declaration (paren_depth == 0 going in)
        paren_depth_at_line_start = paren_depth
        paren_depth += line.count("(") - line.count(")")

        # Phase 1: detect new class declaration (defer creation — wait for `{`)
        if current is None and pending is None:
            m = _CLASS_DECL.match(line)
            if m:
                pending = (lineno, m.group(3), m.group(2), brace_depth)

        # Phase 2: resolve pending
        #   `{` on this line → promote to current
        #   `;` on this line (no `{`) → drop pending (no-body declaration)
        if pending is not None and current is None:
            decl_line, decl_name, decl_kind, decl_depth = pending
            if opens > 0:
                current = ClassRecord(
                    file=str(path),
                    class_name=decl_name,
                    kind=decl_kind,
                    start_line=decl_line,
                )
                in_class_depth = decl_depth
                pending = None
            elif ";" in line:
                # Positional record `Foo(...);` or forward declaration — no body
                pending = None

        # #region / #endregion tracking (only inside current class)
        if current:
            if m := re.match(r"^\s*#region\s*(.*)$", line):
                block = RegionBlock(name=m.group(1).strip(), start_line=lineno)
                current.region_blocks.append(block)
                region_stack.append(block)
            elif re.match(r"^\s*#endregion", line) and region_stack:
                region_stack.pop().end_line = lineno
            current_region = next(
                (block.name for block in reversed(region_stack)
                 if block.name in CANONICAL_REGION_NAME.values()),
                region_stack[-1].name if region_stack else None,
            )

            # Classify member — only at class body TOP LEVEL (one depth deeper
            # than the class declaration), NOT inside method/property bodies,
            # AND only when we're not in the middle of a multi-line signature
            # (paren_depth_at_line_start == 0), AND not inside a multi-line
            # expression-bodied member's continuation (in_expr_body).
            if (
                brace_depth == in_class_depth + 1
                and paren_depth_at_line_start == 0
                and not in_expr_body
            ):
                kind = classify_member(line, current.class_name)
                if kind is None:
                    # Multi-line block property detection:
                    #   `public int WantedCount`   <-- this line, no `(`/`{`/`=>`/`;`
                    #   `{`                        <-- next non-blank line starts with `{`
                    if _MULTILINE_PROP_HEAD.match(line):
                        for j in range(i + 1, min(i + 4, len(lines))):
                            nxt = lines[j].lstrip()
                            if not nxt:
                                continue
                            if nxt.startswith("{"):
                                kind = "Properties"
                            break
                if kind and kind != "directive":
                    current.members.append(Member(
                        line=lineno,
                        kind=kind,
                        region=current_region,
                        text=stripped.rstrip(),
                    ))
                    if kind in ("Properties", "PublicMethods", "PrivateMethods"):
                        # Expression-bodied member spanning multiple lines:
                        # (a) `... =>` on this line, `;` later
                        # (b) `... ()` signature on this line, `=> ...;` on
                        #     the next non-blank line
                        if "=>" in line and not line.rstrip().endswith(";"):
                            in_expr_body = True
                        elif "=>" not in line and "{" not in line and not line.rstrip().endswith(";"):
                            # No body on this line — lookahead for `=>` start
                            for j in range(i + 1, min(i + 4, len(lines))):
                                nxt = lines[j].lstrip()
                                if not nxt:
                                    continue
                                if nxt.startswith("=>"):
                                    in_expr_body = True
                                break
            elif in_expr_body and ";" in line:
                in_expr_body = False

        # Track brace depth (after member classification, so depth at class-open
        # line is still in_class_depth)
        brace_depth += opens - closes
        if (current and current.members and paren_depth == 0
                and brace_depth == in_class_depth + 1
                and re.search(r"(?:;|})\s*(?://.*)?$", line)):
            current.members[-1].end_line = lineno

        # End of class body
        if current and brace_depth <= in_class_depth and closes > 0:
            current.end_line = lineno
            classes.append(current)
            current = None
            in_class_depth = -1
            current_region = None
            region_stack.clear()

    return classes


# -- Conceptual property units and declaration documentation -----------------

_FIELD_NAME = re.compile(r"\b(?:private|protected)\s+[^;{=]+\s+(_\w+)\s*[;=]")


def find_backing_pairs(members: list[Member], lines: list[str]) -> dict[int, int | None]:
    """Map a supported backing field to its property, or None for a generated property."""
    pairs: dict[int, int | None] = {}
    for i, member in enumerate(members):
        if member.kind != "PrivateFields":
            continue
        j = member.line - 2
        while j >= 0 and (not lines[j].strip() or lines[j].strip().startswith("[")):
            if re.search(r"\[ObservableProperty(?:\s|\]|\()", lines[j]):
                pairs[i] = None
                break
            j -= 1
        if i in pairs or i + 1 >= len(members) or members[i + 1].kind != "Properties":
            continue
        name = _FIELD_NAME.search(member.text)
        if not name:
            continue
        prop = members[i + 1]
        end = prop.end_line or (members[i + 2].line - 1 if i + 2 < len(members) else len(lines))
        body = "\n".join(lines[prop.line - 1:end])
        if re.search(rf"(?<!\w){re.escape(name.group(1))}(?!\w)", body):
            pairs[i] = i + 1
    return pairs


def has_summary_before(lines: list[str], declaration_line: int) -> bool:
    """Recognize an adjacent /// summary block, skipping simple attribute lines."""
    j = declaration_line - 2
    while j >= 0 and (not lines[j].strip()
                      or (lines[j].strip().startswith("[") and lines[j].strip().endswith("]"))):
        j -= 1
    comments = []
    while j >= 0 and re.match(r"^\s*///(?!/)", lines[j]):
        comments.append(lines[j])
        j -= 1
    text = "\n".join(reversed(comments))
    return bool(re.search(r"<summary\s*>", text) and re.search(r"</summary\s*>", text))


def check_class(cls: ClassRecord, file_lines: list[str]) -> tuple[list[dict], dict]:
    findings: list[dict] = []

    def add(rule: str, line: int, message: str, **extra: object) -> None:
        findings.append({"rule": rule, "file": cls.file, "line": line,
                         "class": cls.class_name, "message": message,
                         "requires_review": True, **extra})

    pairs = find_backing_pairs(cls.members, file_lines)
    # (first member index, last member index, effective kind); a backing field
    # and its actual property count once, and only their internal gap is exempt.
    units: list[tuple[int, int, str]] = []
    i = 0
    while i < len(cls.members):
        if i in pairs:
            end = pairs[i] if pairs[i] is not None else i
            units.append((i, end, "Properties"))
            i = end + 1
        else:
            units.append((i, i, cls.members[i].kind))
            i += 1

    grouped: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for unit in units:
        grouped[unit[2]].append(unit)
    layout = {"file": cls.file, "class": cls.class_name, "line": cls.start_line,
              "member_declarations": len(cls.members), "groups": []}
    non_empty = sum(bool(grouped.get(kind)) for kind in CANONICAL_ORDER)
    for kind in CANONICAL_ORDER:
        group = grouped.get(kind, [])
        if not group:
            continue
        expected = CANONICAL_REGION_NAME[kind]
        wrapped = sum(all(cls.members[j].region == expected for j in range(start, end + 1))
                      for start, end, _ in group)
        required = len(group) >= 2 and non_empty >= 2
        status = ("wrapped" if wrapped == len(group) else
                  "optional" if not required else "missing" if wrapped == 0 else "partial")
        layout["groups"].append({"kind": kind, "count": len(group), "in_region": wrapped,
                                 "expected_region": expected, "status": status})
        if required and wrapped < len(group):
            add("region_missing", cls.members[group[0][0]].line,
                f"Group '{kind}' has {len(group)} units; some are outside #region {expected}")

    seen = []
    for _, _, kind in units:
        if not seen or seen[-1] != kind:
            seen.append(kind)
    order = [CANONICAL_ORDER.index(kind) for kind in seen if kind in CANONICAL_ORDER]
    if any(left >= right for left, right in zip(order, order[1:])):
        add("members_out_of_order", cls.start_line,
            f"Expected {' → '.join(CANONICAL_ORDER)}; actual {' → '.join(seen)}")

    for previous, current in zip(cls.region_blocks, cls.region_blocks[1:]):
        if not previous.end_line or previous.end_line >= current.start_line:
            continue  # unfinished or nested regions are not adjacent siblings
        if not any(not line.strip() for line in file_lines[previous.end_line:current.start_line - 1]):
            add("region_no_blank_between", current.start_line,
                f"Regions '{previous.name}' and '{current.name}' need a blank line")

    for previous, current in zip(units, units[1:]):
        if previous[2] != current[2] or current[2] not in SPACE_REQUIRED_KINDS:
            continue
        last = cls.members[previous[1]]
        first = cls.members[current[0]]
        end = last.end_line or last.line
        if not any(not line.strip() for line in file_lines[end:first.line - 1]):
            add("members_no_blank_line", first.line,
                f"Consecutive {current[2]} units at L{last.line} and L{first.line} need a blank line")

    # Empty/single-member classes still need declaration documentation.
    if not has_summary_before(file_lines, cls.start_line):
        add("summary_missing", cls.start_line, f"{cls.kind} '{cls.class_name}' lacks a summary")
    for index, member in enumerate(cls.members):
        if index in pairs or member.kind not in {"Properties", "PublicMethods", "PrivateMethods"}:
            continue  # fields, events and constructors are not checked here
        if not has_summary_before(file_lines, member.line):
            add("summary_missing", member.line,
                f"{member.kind} declaration lacks a summary: {member.text[:80]}", kind=member.kind)
    return findings, layout


# -- Per-file worker and command line ----------------------------------------


def _process_file(item: tuple[Path, str]) -> tuple[list[dict], list[dict], int, int]:
    path, encoding = item
    lines = read_source(path, encoding).splitlines()
    classes = parse_file(path, lines)
    findings, layouts = [], []
    without_findings = 0
    for cls in classes:
        found, layout = check_class(cls, lines)
        findings.extend(found)
        layouts.append(layout)
        without_findings += not found
    return findings, layouts, len(classes), without_findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    add_scan_arguments(parser)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be a positive integer")
    scope = Path(args.scope).resolve()
    manifest = Path(args.include_from) if args.include_from else None
    try:
        files = resolve_cs_files(scope, manifest, args.include_generated, args.encoding)
        if not files:
            print(f"No eligible .cs files in scope: {scope}", file=sys.stderr)
            return 2
        items = [(path, args.encoding) for path in files]
        if args.jobs > 1 and len(files) > 50:
            from multiprocessing import Pool
            with Pool(args.jobs) as pool:
                results = list(pool.imap(_process_file, items, chunksize=20))
        else:
            results = map(_process_file, items)
        all_findings, all_layouts = [], []
        total = without_findings = 0
        for findings, layouts, count, no_findings in results:
            all_findings.extend(findings)
            all_layouts.extend(layouts)
            total += count
            without_findings += no_findings
        all_findings.sort(key=lambda finding: (finding["file"], finding["line"], finding["rule"]))
        all_layouts.sort(key=lambda layout: (layout["file"], layout["line"]))
        output = {
            "schema_version": 2, "scope": str(scope), "scanned_files": len(files),
            "files": [str(path) for path in files], "scanned_classes": total,
            "classes_without_findings": without_findings, "requires_review": True,
            "note": "Heuristic candidates only. Braces in strings/comments, nested types, "
                    "partial aggregation, complex signatures/attributes, record declarations "
                    "and preprocessor branches can cause missed or spurious findings. "
                    "Review relevant source manually; zero findings does not prove compliance.",
            "findings": all_findings, "class_layouts": all_layouts,
        }
        output_path = write_report(output, args.output, "class-layout-audit.json")
    except (OSError, ValueError, UnicodeError) as error:
        print(f"Audit failed: {error}", file=sys.stderr)
        return 1
    if not args.quiet and args.output != "-":
        print(f"Scanned {total} class declaration(s); {len(all_findings)} candidate(s)")
    if output_path is not None:
        print(output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# Requires: Python 3.10+ (uses `list[X]` / PEP 604 unions in type hints)
"""
C# class-body layout audit. 5 sub-rules: members_out_of_order / region_missing
/ region_no_blank_between / members_no_blank_line / summary_missing_on_public.
Regex state-machine, not full AST — misses nested/partial classes, multi-line
attribute decorations, multi-line expression bodies (LLM should spot-check).

    python class-layout-check.py --scope path/to/src
    python class-layout-check.py --scope path/to/src --include-from changed.txt --jobs 4
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


# -- Canonical member order (must match 代码规范.md "代码布局") ---------------

CANONICAL_ORDER = [
    "PrivateFields",    # 1. 私有字段 (含 const / static readonly)
    "Properties",       # 2. 属性
    "Events",           # 3. 事件
    "Commands",         # 4. 命令 ([RelayCommand] methods / ICommand properties)
    "Constructors",     # 5. 构造函数
    "PublicMethods",    # 6. 公有方法
    "PrivateMethods",   # 7. 私有方法
]

CANONICAL_REGION_NAME = {
    "PrivateFields":  "[Private Fields]",
    "Properties":     "[Properties]",
    "Events":         "[Events]",
    "Commands":       "[Commands]",
    "Constructors":   "[Constructors]",
    "PublicMethods":  "[Public Methods]",
    "PrivateMethods": "[Private Methods]",
}

SPACE_REQUIRED_KINDS = {"Properties", "Commands", "PublicMethods", "PrivateMethods", "Events"}


# -- Member kind classification (heuristic, line-level) ----------------------

# Strip leading attributes like [Obsolete] so they don't confuse classification.
_ATTR_PREFIX = re.compile(r"^\s*(\[[^\]]+\]\s*)+")
_MEMBER_MODIFIERS = "public|private|protected|internal|static|virtual|override|new|abstract|sealed|readonly|partial|async|extern|unsafe|volatile"


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
    # MUST have at least one modifier (public/private/etc) — bare lines without
    # modifiers are continuations (e.g. `.Cast<X>()` chained call), NOT new
    # declarations.
    has_modifier = re.match(rf"^\s*(?:(?:{_MEMBER_MODIFIERS})\s+)+", stripped)
    if not has_modifier:
        return None
    after_modifiers = stripped[has_modifier.end():]
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

    return None


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


def _has_relay_command_attr(lines: list[str], decl_line_idx: int) -> bool:
    """Walk up to 5 non-blank lines above to check for [RelayCommand]."""
    steps = 0
    for j in range(decl_line_idx - 1, -1, -1):
        text = lines[j].strip()
        if not text:
            continue
        steps += 1
        if steps > 5:
            return False
        if text.startswith("[RelayCommand"):
            return True
        if text.startswith("[") and text.endswith("]"):
            continue  # other attribute, keep looking
        return False
    return False


_COMMAND_TYPE_RE = re.compile(
    r"\b(I?Async)?(I?RelayCommand|ICommand)\b"
)


def _looks_like_command_property(line: str) -> bool:
    """Property whose type is ICommand / IRelayCommand / RelayCommand / AsyncRelayCommand."""
    return bool(_COMMAND_TYPE_RE.search(line))

# Multi-line block property head: `public int Name` with nothing after the name —
# no `(` (would be method), no `{` / `=>` / `;` (would be auto-prop / expr-bodied /
# field). Next non-blank line is expected to be `{` (the property body opener).
_MULTILINE_PROP_HEAD = re.compile(
    rf"^\s*(?:{_MEMBER_MODIFIERS}\s+)+[\w<>,\[\]\?\s\.]+\s+\w+\s*$"
)


def parse_file(path: Path) -> list[ClassRecord]:
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []

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
                name = m.group(1).strip()
                current.region_blocks.append(RegionBlock(name=name, start_line=lineno))
                current_region = name
            elif re.match(r"^\s*#endregion", line):
                if current.region_blocks and current.region_blocks[-1].end_line == 0:
                    current.region_blocks[-1].end_line = lineno
                current_region = None

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
                    # Reclassify as Commands if decorated with [RelayCommand] —
                    # CommunityToolkit.Mvvm generates an ICommand named
                    # XxxCommand for partial methods so marked. Also catches
                    # Property whose type/name indicates ICommand.
                    if kind in ("PrivateMethods", "PublicMethods", "Properties"):
                        if _has_relay_command_attr(lines, i):
                            kind = "Commands"
                        elif kind == "Properties" and _looks_like_command_property(line):
                            kind = "Commands"
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

        # End of class body
        if current and brace_depth <= in_class_depth and closes > opens:
            current.end_line = lineno
            classes.append(current)
            current = None
            in_class_depth = -1
            current_region = None

    return classes


# -- Backing field detection -------------------------------------------------
# A "backing field" is a PrivateFields member that's either:
#   (a) decorated with [ObservableProperty] attribute (CommunityToolkit.Mvvm) —
#       source generator emits a partial property `PascalName` for it
#   (b) positionally placed inside the [Properties] region, OR
#   (c) named `_camelCase` and immediately followed by a property whose name
#       is the PascalCase version (fallback for plain manual backing fields)
#
# Backing fields are RECLASSIFIED into the Properties group (per user direction —
# they ARE properties semantically, just declared as fields):
#   - Counted as Properties (not PrivateFields) in `grouped` & class_layouts
#   - Properties group containing ANY backing field is exempt from region_missing
#     (ObservableProperty pattern keeps field+generated-property adjacent; forcing
#     a #region [Properties] around them would split the pattern artificially)
#   - Order check uses effective kind = Properties (already done in seen_kinds)
#   - Blank-line check skips backing-field neighbors (already done)

_BACKING_FIELD_NAME = re.compile(r"\bprivate\s+[^;{=]+\s+_([a-z]\w*)\s*[;=]")


def find_backing_field_indices(
    members: list["Member"], file_lines: list[str]
) -> set[int]:
    """Return set of member indices in `members` that count as backing fields."""
    backing: set[int] = set()
    for i, m in enumerate(members):
        if m.kind != "PrivateFields":
            continue

        # (a) Direct attribute check — most reliable for CommunityToolkit.Mvvm.
        # Walk up to 5 non-blank lines above; if any is `[ObservableProperty...]`
        # (possibly bracketing other attributes like [NotifyPropertyChangedFor]),
        # treat as backing field.
        line_idx = m.line - 1  # convert to 0-based file_lines index
        steps = 0
        for j in range(line_idx - 1, -1, -1):
            text = file_lines[j].strip()
            if not text:
                continue
            steps += 1
            if steps > 5:
                break
            if text.startswith("[ObservableProperty"):
                backing.add(i)
                break
            if text.startswith("[") and text.endswith("]"):
                continue  # another attribute (e.g. [NotifyPropertyChangedFor]) — keep looking
            break  # non-attribute, non-blank line — stop
        if i in backing:
            continue

        # (b) Position-based: inside [Properties] region (manual backing fields)
        if m.region == "[Properties]":
            backing.add(i)
            continue

        # (c) Removed: name-pair fallback `_xxx` ↔ `Xxx` was producing false
        # positives — ScreenColorPicker has `_wantedCount` + `WantedCount` as
        # ordinary field+property, NOT an ObservableProperty pattern. Use only
        # explicit [ObservableProperty] attribute or [Properties] region
        # placement as backing-field signals.
    return backing


# -- Check a single class against the 4 sub-rules ----------------------------

def check_class(cls: ClassRecord, file_lines: list[str]) -> tuple[list[dict], dict | None]:
    """
    Returns (findings, layout_summary).
    layout_summary is a per-class full snapshot of all non-empty member groups
    (kind / count / in_region / status). LLM uses it to spot audit blind spots
    (e.g. group count under-reported because of parse misses).
    """
    findings: list[dict] = []

    if len(cls.members) <= 1:
        return findings, None  # trivial class, no layout to enforce

    # Detect backing fields (computed once for the whole class)
    backing_indices = find_backing_field_indices(cls.members, file_lines)
    def is_bf(i: int) -> bool:
        return i in backing_indices

    # Group members by kind — backing fields reclassified into Properties group
    # (they ARE properties semantically; [ObservableProperty] field IS the property's
    # storage). Tracked separately so region_missing can exempt mixed groups.
    grouped: dict[str, list[Member]] = defaultdict(list)
    properties_has_backing = False
    for i, m in enumerate(cls.members):
        if is_bf(i):
            grouped["Properties"].append(m)
            properties_has_backing = True
        else:
            grouped[m.kind].append(m)

    # Build per-class layout snapshot (every non-empty group, regardless of
    # whether it triggers any finding). LLM reads this to see if audit's
    # member-count matches the actual file (e.g. extern methods, partial-class
    # members, unusual modifier combos may make audit under-count a group).
    layout_summary: dict = {
        "file": cls.file,
        "class": cls.class_name,
        "line": cls.start_line,
        "groups": [],
    }
    for kind in CANONICAL_ORDER:
        members = grouped.get(kind, [])
        if not members:
            continue
        expected_name = CANONICAL_REGION_NAME[kind]
        in_region = sum(1 for m in members if m.region == expected_name)
        if in_region == len(members):
            status = "wrapped"
        elif in_region == 0:
            status = "missing"
        else:
            status = "partial"
        layout_summary["groups"].append({
            "kind": kind,
            "count": len(members),
            "in_region": in_region,
            "expected_region": expected_name,
            "status": status,
        })

    # op_exempt removed (per user direction): all classes report region_missing
    # by canonical rules. Large ObservableProperty VMs are expected to be
    # mechanically reorganized into canonical region order (Commands region
    # added in CANONICAL_ORDER to group [RelayCommand] methods).
    _canonical_region_names = set(CANONICAL_REGION_NAME.values())
    any_region_present = any(m.region in _canonical_region_names for m in cls.members)
    op_exempt = False

    # --- Sub-rule 1: members_out_of_order ---
    # Backing fields counted as Properties for ordering purposes
    if not op_exempt:
        seen_kinds: list[str] = []
        for i, m in enumerate(cls.members):
            effective = "Properties" if is_bf(i) else m.kind
            if not seen_kinds or seen_kinds[-1] != effective:
                seen_kinds.append(effective)

        canonical_idx = -1
        order_violation = None
        for kind in seen_kinds:
            try:
                idx = CANONICAL_ORDER.index(kind)
            except ValueError:
                continue
            if idx <= canonical_idx:
                order_violation = (
                    f"Members are interleaved or out of order. "
                    f"Expected: {' → '.join(CANONICAL_ORDER)}. "
                    f"Actual: {' → '.join(seen_kinds)}"
                )
                break
            canonical_idx = idx

        if order_violation:
            findings.append({
                "rule": "members_out_of_order",
                "file": cls.file,
                "line": cls.start_line,
                "class": cls.class_name,
                "message": order_violation,
            })

    # --- Sub-rule 2: region_missing ---
    # Two exemptions (per 代码规范.md):
    #   (a) whole class has only ONE non-empty group — no other groups to separate
    #   (b) single-member group, KEPT for everything EXCEPT PrivateMethods
    #
    # Only PrivateMethods has no single-member exemption:
    #   - Parser is most likely to under-count this group (extern / [DllImport] /
    #     complex modifier combos) — a count=1 here may actually be count=2+ in
    #     the source, and missing the region misses a real consistency issue
    #     (see DwmDarkChrome case: extern method dropped, real count was 2)
    #   - The cost of "wrap 1 method" is low compared to a missed extern
    non_empty_groups = sum(1 for k in CANONICAL_ORDER if grouped.get(k))
    if non_empty_groups >= 2 and not op_exempt:
        for kind in CANONICAL_ORDER:
            members = grouped.get(kind, [])
            if not members:
                continue
            # Single-member exemption (unconditional): wrapping a single member
            # in its own #region/#endregion is noisy — 1 member doesn't need
            # navigation help.
            if len(members) < 2:
                # If user wrapped a single-member group anyway, report as
                # redundant (so they can simplify).
                expected_name = CANONICAL_REGION_NAME[kind]
                if any(m.region == expected_name for m in members):
                    findings.append({
                        "rule": "region_single_member_redundant",
                        "file": cls.file,
                        "line": members[0].line,
                        "class": cls.class_name,
                        "message": (
                            f"Group '{kind}' has only 1 member but is wrapped "
                            f"in '#region {expected_name}' — single-member "
                            f"regions add noise without navigation benefit; "
                            f"consider removing the #region/#endregion pair"
                        ),
                    })
                continue
            expected_name = CANONICAL_REGION_NAME[kind]
            in_region_count = sum(1 for m in members if m.region == expected_name)
            if in_region_count < len(members):
                findings.append({
                    "rule": "region_missing",
                    "file": cls.file,
                    "line": members[0].line,
                    "class": cls.class_name,
                    "message": (
                        f"Group '{kind}' has {len(members)} members but is not "
                        f"wrapped in '#region {expected_name}' / '#endregion' "
                        f"(or some members are outside the region)"
                    ),
                })

    # --- Sub-rule 3: region_no_blank_between ---
    for i in range(1, len(cls.region_blocks)):
        prev = cls.region_blocks[i - 1]
        curr = cls.region_blocks[i]
        if prev.end_line == 0 or curr.start_line == 0:
            continue
        # Need at least one blank line between prev.end_line and curr.start_line
        has_blank = any(
            file_lines[j].strip() == ""
            for j in range(prev.end_line, curr.start_line - 1)
        )
        if not has_blank:
            findings.append({
                "rule": "region_no_blank_between",
                "file": cls.file,
                "line": curr.start_line,
                "class": cls.class_name,
                "message": (
                    f"Region '{prev.name}' (ends L{prev.end_line}) and "
                    f"'{curr.name}' (starts L{curr.start_line}) need a "
                    f"blank line between them"
                ),
            })

    # --- Sub-rule 4: members_no_blank_line ---
    for i in range(1, len(cls.members)):
        prev = cls.members[i - 1]
        curr = cls.members[i]
        # Backing fields are tightly coupled — no blank line required between
        # backing-field and adjacent member (its property)
        if is_bf(i - 1) or is_bf(i):
            continue
        if prev.kind != curr.kind:
            continue  # different kind: cross-group, handled by region rule
        if prev.kind not in SPACE_REQUIRED_KINDS:
            continue
        has_blank = any(
            file_lines[j].strip() == ""
            for j in range(prev.line, curr.line - 1)
        )
        if not has_blank:
            findings.append({
                "rule": "members_no_blank_line",
                "file": cls.file,
                "line": curr.line,
                "class": cls.class_name,
                "message": (
                    f"Two consecutive {curr.kind} members "
                    f"(L{prev.line} and L{curr.line}) need a blank line between them"
                ),
            })

    # --- Sub-rule 5: summary_missing_on_public ---
    # Check that each public class/method/property/event has a /// <summary>
    # block directly above. Skip private/internal members and backing fields.
    # Attribute lines like [Obsolete] between summary and declaration are OK.
    for i, m in enumerate(cls.members):
        if is_bf(i):
            continue
        if not re.search(r"\bpublic\b", m.text):
            continue
        # Only Properties / PublicMethods / Events need summary
        # (Constructors and fields skipped — Ctor summaries are optional in practice)
        if m.kind not in ("Properties", "PublicMethods", "Events"):
            continue
        # Walk up from member's line, skipping blank lines and attribute lines
        j = m.line - 2  # 0-indexed; line above declaration
        while j >= 0:
            stripped = file_lines[j].strip()
            if stripped == "":
                j -= 1
                continue
            # Attribute line like [Obsolete] or [Description("...")]
            if stripped.startswith("[") and stripped.endswith("]"):
                j -= 1
                continue
            break
        # `j` now points to the first non-blank non-attribute line above
        if j < 0 or not file_lines[j].strip().startswith("///"):
            # Effective region for grouping — backing fields conceptually live
            # in [Properties] even if physically outside any region.
            region_label = m.region if m.region else "(no region)"
            findings.append({
                "rule": "summary_missing_on_public",
                "file": cls.file,
                "line": m.line,
                "class": cls.class_name,
                "kind": m.kind,
                "region": region_label,
                "message": (
                    f"Public {m.kind} at L{m.line} (region: {region_label}) has "
                    f"no /// <summary> documentation. "
                    f"Declaration: `{m.text[:60]}{'...' if len(m.text) > 60 else ''}`"
                ),
            })

    return findings, layout_summary


# -- Per-file worker (module-level so multiprocessing.Pool can pickle it) ----

def _process_file(f: Path) -> tuple[list[dict], list[dict], int, int]:
    """Returns (findings, layouts, total_classes, clean_classes) for one file."""
    try:
        classes = parse_file(f)
        file_lines = f.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return [], [], 0, 0
    findings_local: list[dict] = []
    layouts_local: list[dict] = []
    total = 0
    clean = 0
    for cls in classes:
        total += 1
        cls_findings, cls_layout = check_class(cls, file_lines)
        if cls_findings:
            findings_local.extend(cls_findings)
        else:
            clean += 1
        if cls_layout is not None:
            layouts_local.append(cls_layout)
    return findings_local, layouts_local, total, clean


# -- Scope resolution + main -------------------------------------------------

_GENERATED_DIRS = {"obj", "bin", "Generated", ".vs"}
_GENERATED_SUFFIXES = (
    ".g.cs", ".g.i.cs", ".Designer.cs", ".designer.cs",
    ".AssemblyInfo.cs", ".AssemblyAttributes.cs", ".GlobalUsings.g.cs",
)


def _is_generated(p: Path) -> bool:
    """Filter out compiler-generated / build artifact .cs files."""
    name = p.name
    if any(name.endswith(s) for s in _GENERATED_SUFFIXES):
        return True
    # Walk parents, skip if any segment is obj/bin/etc.
    return any(part in _GENERATED_DIRS for part in p.parts)


def resolve_cs_files(scope: Path, include_from: Path | None = None) -> list[Path]:
    """If `include_from` is given, filter universe to files listed (one per line)."""
    if scope.is_file():
        if scope.suffix.lower() == ".cs":
            universe = [scope]
        elif scope.suffix.lower() in (".csproj", ".sln"):
            universe = [p for p in sorted(scope.parent.rglob("*.cs")) if not _is_generated(p)]
        else:
            raise ValueError(f"Scope must be .cs / .csproj / .sln, got: {scope.suffix}")
    elif scope.is_dir():
        universe = [p for p in sorted(scope.rglob("*.cs")) if not _is_generated(p)]
    else:
        raise ValueError(f"Scope not found: {scope}")

    if include_from is None:
        return universe

    if not include_from.exists():
        raise ValueError(f"--include-from file not found: {include_from}")
    wanted_text = include_from.read_text(encoding="utf-8-sig", errors="replace")
    wanted = set()
    for line in wanted_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            wanted.add(Path(line).resolve())
        except OSError:
            continue

    universe_resolved = {f.resolve(): f for f in universe}
    return [universe_resolved[w] for w in wanted if w in universe_resolved]


def default_output_path() -> Path:
    tmp = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
    return Path(tmp) / "class-layout-audit.json"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--scope", default=".",
                   help="File / directory / .csproj / .sln")
    p.add_argument("--include-from", default=None,
                   help="Path to a text file listing .cs files to audit (one per line). "
                        "Only files in this list AND under --scope are scanned.")
    p.add_argument("--output", default=str(default_output_path()),
                   help="Output JSON path (default: $TEMP/class-layout-audit.json)")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--jobs", type=int, default=1,
                   help="Parallel workers for file parsing (default: 1, single-process). "
                        "Use 4-8 on large projects (5k+ files) for 3-5x speedup.")
    args = p.parse_args()

    scope = Path(args.scope).resolve()
    include_from = Path(args.include_from).resolve() if args.include_from else None
    try:
        files = resolve_cs_files(scope, include_from)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not files:
        print(f"No .cs files found under: {scope}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"[*] Scanning {len(files)} .cs file(s) under {scope}")

    all_findings: list[dict] = []
    all_layouts: list[dict] = []
    total_classes = 0
    clean_classes = 0

    if args.jobs > 1 and len(files) > 50:
        # Parallelize only when worthwhile (large file set). Worker function
        # `_process_file` lives at module level so it's picklable on Windows.
        from multiprocessing import Pool
        with Pool(args.jobs) as pool:
            for findings_local, layouts_local, total, clean in pool.imap_unordered(_process_file, files, chunksize=20):
                all_findings.extend(findings_local)
                all_layouts.extend(layouts_local)
                total_classes += total
                clean_classes += clean
    else:
        for f in files:
            findings_local, layouts_local, total, clean = _process_file(f)
            all_findings.extend(findings_local)
            all_layouts.extend(layouts_local)
            total_classes += total
            clean_classes += clean

    by_rule: dict[str, int] = defaultdict(int)
    for f in all_findings:
        by_rule[f["rule"]] += 1

    output = {
        "scope": str(scope),
        "scanned_files": len(files),
        "scanned_classes": total_classes,
        "clean_classes": clean_classes,
        "generated_at_note": (
            "Run by class-layout-check.py. Heuristic regex parser — may miss "
            "nested/partial classes, multi-line attribute decorations, multi-line "
            "expression bodies. LLM should spot-check a few findings against the "
            "actual file before batch-applying fixes. The `class_layouts` array "
            "gives per-class group snapshots so LLM can verify audit's member "
            "counts match the actual file — flag any discrepancy as a likely "
            "parser miss."
        ),
        "findings": all_findings,
        "class_layouts": all_layouts,
    }

    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not args.quiet:
        print()
        print(f"[OK] Class layout audit written to: {args.output}")
        print()
        print(f"Scanned: {total_classes} class(es) in {len(files)} file(s)")
        print(f"Clean:   {clean_classes} class(es)")
        print(f"Issues:  {len(all_findings)} finding(s) in {total_classes - clean_classes} class(es)")
        if all_findings:
            print()
            print("Per-rule:")
            for rule in sorted(by_rule):
                print(f"  {rule:<25} {by_rule[rule]:>5}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

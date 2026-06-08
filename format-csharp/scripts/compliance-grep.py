#!/usr/bin/env python3
# Requires: Python 3.10+ (uses `list[X]` / PEP 604 unions in type hints)
"""
C# style compliance grep audit. 11 rules (Cmd suffix / Attribute/Exception
suffix / I prefix / Chinese identifier / private field _ / anonymous delegate
/ if-no-braces / float == / enum.ToString suspect / summary inline).
Deterministic filters applied; `violations` is the actionable list.
`enum_tostring_suspect` still needs LLM context-reading to confirm.

    python compliance-grep.py --scope path/to/src
    python compliance-grep.py --scope src --include-from changed.txt --jobs 4
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows where the console codepage may be CP936 etc.
# (data file output is UTF-8 anyway; this only affects what user sees in terminal)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


# -- Rule set: mirrors compliance-check.md Section 3 -------------------------

RULES = [
    {
        "key": "cmd_suffix",
        "name": "Cmd 后缀禁用",
        "rule_source": "良好习惯表: 绑定命令属性统一以 Command 结尾,禁止使用 Cmd 缩写",
        "filter_hint": "无需过滤,全部是违规",
        "pattern": r"\b\w+Cmd\b\s*(\{|=>|;)",
    },
    {
        "key": "attribute_no_suffix",
        "name": "自定义 Attribute 缺后缀",
        "rule_source": "良好习惯表: 自定义的属性以 Attribute 结尾",
        "filter_hint": "丢弃: 类名以 \"Attribute\" 结尾的(合规)。保留: 不以 Attribute 结尾的(违规)",
        "pattern": r"\bclass\s+\w+\s*:\s*[^;{]*Attribute\b",
    },
    {
        "key": "exception_no_suffix",
        "name": "自定义 Exception 缺后缀",
        "rule_source": "良好习惯表: 自定义的异常以 Exception 结尾",
        "filter_hint": "丢弃: 类名以 \"Exception\" 结尾的(合规)。保留: 不以 Exception 结尾的(违规)",
        "pattern": r"\bclass\s+\w+\s*:\s*[^;{]*Exception\b",
    },
    {
        "key": "interface_no_i_prefix",
        "name": "接口缺 I 前缀",
        "rule_source": "良好习惯表: 接口的名称加前缀 I",
        "filter_hint": "丢弃: 接口名匹配 ^I[A-Z](合规)。保留: 不匹配的(违规)",
        "pattern": r"\binterface\s+\w+",
    },
    {
        "key": "chinese_identifier",
        "name": "中文标识符",
        "rule_source": "良好习惯表: 所有标识符必须使用英文,禁止中文",
        "filter_hint": "无需过滤,全部是违规。字符串内容和 // 注释里的中文允许,grep 已限定标识符位置",
        "pattern": r"\b(public|private|protected|internal|static|class|interface|enum|void|var|string|int|bool|double|float|decimal|object|dynamic)\s+\w*[一-鿿]\w*",
    },
    {
        "key": "private_field_no_underscore",
        "name": "私有字段缺 _ 前缀",
        "rule_source": "大小写表 + 良好习惯表: 私有成员变量前加前缀 _",
        "filter_hint": "丢弃: const/static readonly(常量风格),event/delegate(不是字段),字段名以 _ 开头(合规)",
        "pattern": r"\bprivate\s+[^;{=]+\s+\w+\s*[;=]",
    },
    {
        "key": "anonymous_delegate",
        "name": "匿名委托",
        "rule_source": "表达式与语句 6: 禁止使用匿名委托,全部换成具名函数",
        "filter_hint": "无需过滤,全部是违规",
        "pattern": r"\bdelegate\s*\(",
    },
    {
        "key": "no_braces_on_control_flow",
        "name": "if/for/while 单语句不加 { }",
        "rule_source": "良好习惯表: 始终使用 \"{ }\" 包含 if 下的语句",
        "filter_hint": "丢弃: 单行 inline return/throw/continue/break/yield,以及 Allman 风格(`)` 后无内容下一行 `{`)",
        "pattern": r"\b(if|for|while|foreach)\s*\([^)]*\)\s*[^\s{/]",
    },
    {
        "key": "float_equality",
        "name": "浮点 == / != 比较",
        "rule_source": "表达式与语句 4: 不可将浮点变量用 == 或 != 与任何数字比较",
        "filter_hint": "丢弃: == null / != null 比较(合法)",
        "pattern": r"\b(float|double|decimal)\b[^={]*[=!]=",
    },
    {
        "key": "enum_tostring_suspect",
        "name": "enum.ToString() 嫌疑",
        "rule_source": "特殊事项: 禁止枚举 ToString() 用于赋值或判断",
        "filter_hint": "【高误判率】丢弃 int/DateTime/double/decimal/GUID/bool 等非 enum ToString。保留真 enum ToString。**必须逐个开文件确认左侧类型**",
        "pattern": r"\.ToString\(\)",
    },
    {
        "key": "summary_inline",
        "name": "<summary> 标签和内容写在同一行",
        "rule_source": "方法注释规范 10: <summary>/</summary> 必须各占一行,内容夹中间独占一行",
        "filter_hint": "无需过滤,命中即违规。少数情况下规范示例可能误中,LLM 读上下文跳过",
        "pattern": r"(?:///\s*<summary>\s*\S|///\s*\S.*</summary>)",
    },
]


# -- Per-rule deterministic filters ------------------------------------------
# Each filter: takes the candidate's line text, returns True if it's a real
# violation (keep), False if it's a deterministic false-positive (drop).

NO_FILTER_RULES = {"cmd_suffix", "chinese_identifier", "anonymous_delegate",
                   "summary_inline", "enum_tostring_suspect"}


def _filter_attribute_no_suffix(text: str) -> bool:
    m = re.search(r"\bclass\s+(\w+)\s*:", text)
    if m:
        return not m.group(1).endswith("Attribute")
    return True


def _filter_exception_no_suffix(text: str) -> bool:
    m = re.search(r"\bclass\s+(\w+)\s*:", text)
    if m:
        return not m.group(1).endswith("Exception")
    return True


def _filter_interface_no_i_prefix(text: str) -> bool:
    m = re.search(r"\binterface\s+(\w+)", text)
    if m:
        # Compliant if name matches ^I[A-Z]
        return re.match(r"^I[A-Z]", m.group(1)) is None
    return True


def _filter_private_field_no_underscore(text: str) -> bool:
    # Drop const / static readonly (constants use Pascal naming)
    if re.search(r"\bprivate\s+(const|static\s+readonly)\b", text):
        return False
    # Drop events and delegates (not fields)
    if re.search(r"\bprivate\s+(event|delegate)\s+", text):
        return False
    # Drop names starting with _ (already compliant)
    if re.search(r"\s_\w+\s*[;=]", text):
        return False
    return True


def _filter_no_braces_on_control_flow(text: str) -> bool:
    # Drop inline early-exit / yield forms
    if re.search(r"\b(if|for|while|foreach)\s*\([^)]*\)\s*(return|throw|continue|break|yield\s+(return|break))\b", text):
        return False
    # Drop Allman-style — line starts with control keyword + balanced parens + nothing meaningful after
    if re.match(r"^\s*\b(if|for|while|foreach)\b\s*\(.*\)\s*$", text):
        opens = text.count("(")
        closes = text.count(")")
        if opens > 0 and opens == closes:
            return False
    return True


def _filter_float_equality(text: str) -> bool:
    # Drop == null / != null
    if re.search(r"[=!]=\s*null\b", text):
        return False
    return True


FILTERS = {
    "attribute_no_suffix":           _filter_attribute_no_suffix,
    "exception_no_suffix":           _filter_exception_no_suffix,
    "interface_no_i_prefix":         _filter_interface_no_i_prefix,
    "private_field_no_underscore":   _filter_private_field_no_underscore,
    "no_braces_on_control_flow":     _filter_no_braces_on_control_flow,
    "float_equality":                _filter_float_equality,
}


# -- Scope resolution + file iteration ---------------------------------------

def resolve_cs_files(scope: Path, include_from: Path | None = None) -> list[Path]:
    """
    Return list of .cs files under the given scope.

    If `include_from` is given, treat that file as a newline-separated list of
    .cs paths and use only those (filtered to ones under `scope`). This lets
    callers limit the audit to a specific file set — e.g., "only the files
    that dotnet format just touched" using `dotnet format --report`'s JSON.
    """
    # Build the universe of .cs files under scope
    if scope.is_file():
        if scope.suffix.lower() == ".cs":
            universe = [scope]
        elif scope.suffix.lower() in (".csproj", ".sln"):
            universe = sorted(scope.parent.rglob("*.cs"))
        else:
            raise ValueError(f"Scope must be .cs / .csproj / .sln, got: {scope.suffix}")
    elif scope.is_dir():
        universe = sorted(scope.rglob("*.cs"))
    else:
        raise ValueError(f"Scope path not found or invalid: {scope}")

    if include_from is None:
        return universe

    # Read include-from file: one .cs path per line (empty / # comment lines skipped)
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

    # Intersect with universe (avoid auditing files outside scope by accident)
    universe_resolved = {f.resolve(): f for f in universe}
    return [universe_resolved[w] for w in wanted if w in universe_resolved]


# -- Run regex over files ----------------------------------------------------

def run_pattern(pattern: str, files: list[Path]) -> list[dict]:
    """
    Return list of {file, line, text} for matches of `pattern` across `files`.
    Case-sensitive (C# is case-sensitive — `ForEach` LINQ ≠ `foreach` keyword).
    """
    rx = re.compile(pattern)
    hits = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            if rx.search(line):
                hits.append({"file": str(f), "line": lineno, "text": line.strip()})
    return hits


# -- Per-file scan (single-pass: scan all rules on each file once) -----------
# Module-level so multiprocessing.Pool can pickle it on Windows.

_COMPILED_RULES: list[tuple[str, "re.Pattern"]] = []


def _init_compiled_rules() -> None:
    """Initialize once (called per-process in main + worker)."""
    global _COMPILED_RULES
    if not _COMPILED_RULES:
        _COMPILED_RULES = [(r["key"], re.compile(r["pattern"])) for r in RULES]


def _scan_file(f: Path) -> dict[str, list[dict]]:
    """Scan a single .cs file against all rules in one pass. Returns {key: [hits]}."""
    _init_compiled_rules()
    hits_by_key: dict[str, list[dict]] = {key: [] for key, _ in _COMPILED_RULES}
    try:
        content = f.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return hits_by_key
    f_str = str(f)
    for lineno, line in enumerate(content.splitlines(), start=1):
        text = line.strip()
        for key, rx in _COMPILED_RULES:
            if rx.search(line):
                hits_by_key[key].append({"file": f_str, "line": lineno, "text": text})
    return hits_by_key


# -- Main --------------------------------------------------------------------

def default_output_path() -> Path:
    tmp = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
    return Path(tmp) / "compliance-audit.json"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--scope", default=".",
                   help="File / directory / .csproj / .sln (default: current dir)")
    p.add_argument("--include-from", default=None,
                   help="Path to a text file listing .cs files to audit (one per line). "
                        "Only files in this list AND under --scope are scanned. Useful for "
                        "auditing just the files dotnet format touched.")
    p.add_argument("--output", default=str(default_output_path()),
                   help="Output JSON path (default: $TEMP/compliance-audit.json)")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-rule progress output")
    p.add_argument("--jobs", type=int, default=1,
                   help="Parallel workers for file scanning (default: 1, single-process). "
                        "Use 4-8 on large projects (5k+ files) for 3-5x speedup. Auto-disabled "
                        "if fewer than 50 files.")
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
        print()

    # Single-pass per file (all rules scanned at once), optionally parallel.
    # Each file produces {rule_key: [hits]}, aggregated to per-rule candidates.
    _init_compiled_rules()
    candidates_by_key: dict[str, list[dict]] = {r["key"]: [] for r in RULES}

    if args.jobs > 1 and len(files) > 50:
        from multiprocessing import Pool
        with Pool(args.jobs) as pool:
            for hits_by_key in pool.imap_unordered(_scan_file, files, chunksize=20):
                for key, hits in hits_by_key.items():
                    candidates_by_key[key].extend(hits)
    else:
        for f in files:
            hits_by_key = _scan_file(f)
            for key, hits in hits_by_key.items():
                candidates_by_key[key].extend(hits)

    results = []
    for rule in RULES:
        candidates = candidates_by_key[rule["key"]]
        if rule["key"] in NO_FILTER_RULES:
            violations = candidates
            filter_applied = False
        else:
            flt = FILTERS.get(rule["key"], lambda _: True)
            violations = [c for c in candidates if flt(c["text"])]
            filter_applied = True

        results.append({
            "key": rule["key"],
            "name": rule["name"],
            "rule_source": rule["rule_source"],
            "filter_hint": rule["filter_hint"],
            "pattern": rule["pattern"],
            "candidate_count": len(candidates),
            "violation_count": len(violations),
            "deterministic_filter_applied": filter_applied,
            "violations": violations,
        })

        if not args.quiet:
            msg = f"  [..] {rule['name']:<30} {len(violations)} violation(s)"
            if len(candidates) != len(violations):
                msg += f" (filtered out {len(candidates) - len(violations)} of {len(candidates)} candidates)"
            print(msg)

    output = {
        "scope": str(scope),
        "scanned_files": len(files),
        "generated_at_note": (
            "Run by compliance-grep.py. Deterministic filters already applied — "
            "`violations` is the LLM-actionable list. For `enum_tostring_suspect`, "
            "LLM still needs to read each candidate to confirm left-side is enum "
            "type (high false-positive rate)."
        ),
        "rules": results,
    }

    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not args.quiet:
        print()
        print(f"[OK] Audit results written to: {args.output}")
        print()
        print("Per-rule violation counts (after deterministic filter):")
        for r in results:
            extra = ""
            if r["candidate_count"] != r["violation_count"]:
                extra = f" (was {r['candidate_count']} before filter)"
            print(f"  {r['name']:<40} {r['violation_count']:>5}{extra}")
        print()
        print("Next: LLM reads the JSON, treats `violations` as the actionable list. "
              "Only `enum_tostring_suspect` survivors need LLM context-reading.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

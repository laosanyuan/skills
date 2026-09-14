#!/usr/bin/env python3
# Requires: Python 3.10+ (uses `list[X]` / PEP 604 unions in type hints)
"""
C# style compliance grep audit. 12 rules (Cmd suffix / Attribute/Exception
suffix / I prefix / Chinese identifier / private field _ / anonymous delegate
/ if-no-braces / float == / enum.ToString suspect / summary inline /
comment terminal period).
All findings are candidates, including matches retained by filters. Confirm
syntax context and applicable semantics before any edit; this is not a C# parser.

    python compliance-grep.py --scope path/to/src
    python compliance-grep.py --scope src --include-from changed.txt --jobs 4
    python compliance-grep.py --scope One.cs --rules summary_inline comment_terminal_period --output -
"""

import argparse
import re
import sys
from pathlib import Path

from scan_scope import add_scan_arguments, read_source, resolve_cs_files, write_report

# Force UTF-8 stdout on Windows where the console codepage may be CP936 etc.
# (data file output is UTF-8 anyway; this only affects what user sees in terminal)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


# -- Rule set: mirrors compliance-check.md text candidate table --------------

RULES = [
    {
        "key": "cmd_suffix",
        "name": "Cmd 后缀禁用",
        "rule_source": "命名与基本格式: 绑定命令属性统一以 Command 结尾,不用 Cmd",
        "filter_hint": "保留候选；须确认真实绑定命令属性，不是普通变量、外部 API 或字符串",
        "pattern": r"\b\w+Cmd\b\s*(\{|=>|;)",
    },
    {
        "key": "attribute_no_suffix",
        "name": "自定义 Attribute 缺后缀",
        "rule_source": "命名与基本格式: 自定义特性使用 Attribute 后缀",
        "filter_hint": "过滤已有 Attribute 后缀的声明，其余候选需确认真实类型和继承关系",
        "pattern": r"\bclass\s+\w+\s*:\s*[^;{]*Attribute\b",
    },
    {
        "key": "exception_no_suffix",
        "name": "自定义 Exception 缺后缀",
        "rule_source": "命名与基本格式: 自定义异常使用 Exception 后缀",
        "filter_hint": "过滤已有 Exception 后缀的声明，其余候选需确认真实类型和继承关系",
        "pattern": r"\bclass\s+\w+\s*:\s*[^;{]*Exception\b",
    },
    {
        "key": "interface_no_i_prefix",
        "name": "接口缺 I 前缀",
        "rule_source": "命名与基本格式: 接口使用 I + PascalCase",
        "filter_hint": "过滤接口名匹配 ^I[A-Z] 的候选；其余需确认真实接口声明",
        "pattern": r"\binterface\s+\w+",
    },
    {
        "key": "chinese_identifier",
        "name": "中文标识符",
        "rule_source": "命名与基本格式: 标识符使用英文，不用中文",
        "filter_hint": "保留候选；须确认真实标识符，排除字符串和注释中的同形文本",
        "pattern": r"\b(public|private|protected|internal|static|class|interface|enum|void|var|string|int|bool|double|float|decimal|object|dynamic)\s+\w*[一-鿿]\w*",
    },
    {
        "key": "private_field_no_underscore",
        "name": "私有字段缺 _ 前缀",
        "rule_source": "命名与基本格式: 私有字段含 static/readonly 使用 _camelCase",
        "filter_hint": "丢弃 const、event/delegate 和已有 _ 前缀；static readonly 仍是字段；其余候选须确认声明上下文",
        "pattern": r"\bprivate\s+[^;{=]+\s+\w+\s*[;=]",
    },
    {
        "key": "anonymous_delegate",
        "name": "匿名委托",
        "rule_source": "需语义评估的写法建议: 匿名 delegate 提取需检查闭包与委托身份",
        "filter_hint": "先确认真实匿名委托与捕获、实例身份、订阅/退订和生命周期；候选不代表必须提取具名函数",
        "pattern": r"\bdelegate\s*\(",
    },
    {
        "key": "no_braces_on_control_flow",
        "name": "if/for/while 单语句不加 { }",
        "rule_source": "表达式与语句 1: 控制语句的受控语句使用大括号",
        "filter_hint": "inline return/throw/continue/break/yield 同样需要大括号；嵌套括号和跨行控制体须人工确认",
        "pattern": r"\b(if|for|while|foreach)\s*\([^)]*\)\s*[^\s{/]",
    },
    {
        "key": "float_equality",
        "name": "浮点 == / != 比较",
        "rule_source": "需语义评估的写法建议: 浮点比较按业务精度与契约评估",
        "filter_hint": "排除 null 比较；确认实际类型、NaN/无穷大和精度契约，不能自动改成 epsilon 比较",
        "pattern": r"\b(float|double|decimal)\b[^={]*[=!]=",
    },
    {
        "key": "enum_tostring_suspect",
        "name": "enum.ToString() 嫌疑",
        "rule_source": "需语义评估的写法建议: 枚举文本用于外部协议或持久化时评估稳定性",
        "filter_hint": "先确认左侧类型与文本用途；枚举 ToString 不等于违规，不自动替换 Description；确认值、Flags、未定义值及本地化契约",
        "pattern": r"\.ToString\(\)",
    },
    {
        "key": "summary_inline",
        "name": "<summary> 标签未独占行",
        "rule_source": "标签布局与标点: <summary>/</summary> 必须各占物理行，内容放中间",
        "filter_hint": "候选限定为行首 /// 并排除 ////;打开上下文确认不在块注释/多行字符串内。真实文档注释只放行整行内容恰好为 <summary> 或 </summary>",
        "pattern": r"^\s*///(?!/).*?</?summary(?=[\s/>]|$)",
    },
    {
        "key": "comment_terminal_period",
        "name": "注释内容以句号结尾",
        "rule_source": "标签布局与标点: 注释结尾省略中英文句末句号",
        "filter_hint": "覆盖常见 //、///、行尾注释和块注释形态;英文省略号已过滤。打开上下文确认真实注释边界，并确认 ASCII 点不是缩写或字面数据的一部分",
        "pattern": r"(?:^\s*\*|/\*|//).*?(?:。|\.)(?:\s*</[A-Za-z_][\w:.-]*\s*>)*\s*(?:\*/)?\s*$",
    },
]


# -- Per-rule deterministic filters ------------------------------------------
# Each filter reduces obvious false positives; True still means candidate only.

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
    # const is not a field; static readonly still follows private field naming
    if re.search(r"\bconst\b", text.split("=", 1)[0]):
        return False
    # Drop events and delegates (not fields)
    if re.search(r"\bprivate\s+(event|delegate)\s+", text):
        return False
    # Drop names starting with _ (already compliant)
    if re.search(r"\s_\w+\s*[;=]", text):
        return False
    return True


def _filter_no_braces_on_control_flow(text: str) -> bool:
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


def _filter_summary_inline(text: str) -> bool:
    """Keep summary-tag candidates unless the tag is the whole line payload."""
    match = re.match(r"^\s*///(?!/)\s*(.*?)\s*$", text)
    if match is None:
        return False
    return match.group(1) not in {"<summary>", "</summary>"}


def _filter_comment_terminal_period(text: str) -> bool:
    """Keep comment-shaped text ending in a period, excluding ellipses."""
    payload = re.sub(r"\s*\*/\s*$", "", text).rstrip()
    payload = re.sub(
        r"(?:\s*</[A-Za-z_][\w:.-]*\s*>)+\s*$",
        "",
        payload,
    ).rstrip()
    if payload.endswith("。"):
        return True
    return payload.endswith(".") and not payload.endswith("..")


FILTERS = {
    "attribute_no_suffix":           _filter_attribute_no_suffix,
    "exception_no_suffix":           _filter_exception_no_suffix,
    "interface_no_i_prefix":         _filter_interface_no_i_prefix,
    "private_field_no_underscore":   _filter_private_field_no_underscore,
    "no_braces_on_control_flow":     _filter_no_braces_on_control_flow,
    "float_equality":                _filter_float_equality,
    "summary_inline":                _filter_summary_inline,
    "comment_terminal_period":       _filter_comment_terminal_period,
}


# -- Per-file scan -----------------------------------------------------------

_COMPILED_RULES: list[tuple[str, "re.Pattern"]] = []


def _init_compiled_rules() -> None:
    global _COMPILED_RULES
    if not _COMPILED_RULES:
        _COMPILED_RULES = [(r["key"], re.compile(r["pattern"])) for r in RULES]


def _scan_file(item: tuple[Path, str, tuple[str, ...]]) -> dict[str, list[dict]]:
    path, encoding, rule_keys = item
    _init_compiled_rules()
    compiled = [(key, pattern) for key, pattern in _COMPILED_RULES if key in rule_keys]
    hits_by_key: dict[str, list[dict]] = {key: [] for key, _ in compiled}
    for lineno, line in enumerate(read_source(path, encoding).splitlines(), 1):
        for key, pattern in compiled:
            if pattern.search(line):
                hits_by_key[key].append({"file": str(path), "line": lineno, "text": line.strip()})
    return hits_by_key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    add_scan_arguments(parser)
    parser.add_argument("--rules", nargs="+", choices=[rule["key"] for rule in RULES],
                        help="Check only these rule keys; omitted means all rules. Selection does not prove overall compliance")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be a positive integer")
    scope = Path(args.scope).resolve()
    manifest = Path(args.include_from) if args.include_from else None
    selected_rules = [rule for rule in RULES if args.rules is None or rule["key"] in args.rules]
    rule_keys = tuple(rule["key"] for rule in selected_rules)
    try:
        files = resolve_cs_files(scope, manifest, args.include_generated, args.encoding)
        if not files:
            print(f"No eligible .cs files in scope: {scope}", file=sys.stderr)
            return 2

        candidates_by_key: dict[str, list[dict]] = {key: [] for key in rule_keys}
        items = [(path, args.encoding, rule_keys) for path in files]
        if args.jobs > 1 and len(files) > 50:
            from multiprocessing import Pool
            with Pool(args.jobs) as pool:
                scanned = list(pool.imap(_scan_file, items, chunksize=20))
        else:
            scanned = map(_scan_file, items)
        for hits_by_key in scanned:
            for key, hits in hits_by_key.items():
                candidates_by_key[key].extend(hits)

        results = []
        for rule in selected_rules:
            raw = sorted(candidates_by_key[rule["key"]],
                         key=lambda hit: (hit["file"], hit["line"], hit["text"]))
            flt = FILTERS.get(rule["key"])
            candidates = [hit for hit in raw if flt is None or flt(hit["text"])]
            results.append({
                **rule,
                "raw_candidate_count": len(raw),
                "candidate_count": len(candidates),
                "filter_applied": flt is not None,
                "requires_review": True,
                "candidates": candidates,
            })
            if not args.quiet and args.output != "-":
                print(f"  {rule['name']}: {len(candidates)} candidate(s)")
        output = {
            "schema_version": 2,
            "scope": str(scope),
            "scanned_files": len(files),
            "files": [str(path) for path in files],
            "requires_review": True,
            "note": "Regex candidates only; review all matches and relevant unmatched code. "
                    "A zero candidate count is not proof of compliance. Exit 0 means scan completed.",
            "rules": results,
        }
        if args.rules is not None:
            output["selected_rules"] = list(rule_keys)
            output["unchecked_rules"] = [rule["key"] for rule in RULES if rule["key"] not in rule_keys]
        output_path = write_report(output, args.output, "compliance-audit.json")
    except (OSError, ValueError, UnicodeError) as error:
        print(f"Audit failed: {error}", file=sys.stderr)
        return 1
    if output_path is not None:
        print(output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

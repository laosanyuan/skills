#!/usr/bin/env python3
"""Read-only PRD structural checks, with optional baseline change review."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path


HEADING = re.compile(r"^(#{2,6})[ \t]+(F[0-9]+)(?:[ \t]*[-–—:：][ \t]*|[ \t]+)(\S.*?)\s*#*\s*$")
ANY_HEADING = re.compile(r"^(#{1,6})\s+")
FIELD = re.compile(r"^\s*\*\*(前置依赖|优先级)\*\*\s*[:：]\s*(.*?)\s*$")
LEGACY_FIELD = re.compile(r"^\s*\*\*(前置依赖|优先级)\*\*")
WATERMARK = re.compile(r"^\s*<!--\s*prd-id-high-watermark:\s*(F[0-9]+)\s*-->\s*$")
VALID_ID = re.compile(r"F(?:0[0-9]{2}|[1-9][0-9]{2,})$")
EXISTING_ID = re.compile(r"F[0-9]+$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
LIST_FENCE = re.compile(r"^ {0,3}(?:[-+*]|[0-9]{1,9}[.)]) +(`{3,}|~{3,})(.*)$")
AC_DEFINITION = re.compile(r"^ {0,3}(?:[-+*][ \t]+|[0-9]{1,9}[.)][ \t]+)?(AC-(F[0-9]+)-([0-9]+))[ \t]*[:：][ \t]*(\S.*?)\s*$")


def inspect(path: Path, id_style: str = "canonical", *, inventory: dict | None = None) -> dict:
    if id_style not in {"canonical", "existing"}:
        raise ValueError("id_style must be canonical or existing")
    id_pattern = VALID_ID if id_style == "canonical" else EXISTING_ID
    source = path.read_text(encoding="utf-8-sig")
    errors: list[dict] = []
    warnings: list[dict] = []
    features: dict[str, dict] = {}
    watermarks: list[tuple[str, int]] = []
    current = None
    fence_char = None
    fence_size = 0
    fence_start = 0
    fences_checked = 0
    list_fence = None
    if inventory is not None:
        inventory.update(features={}, acceptance={}, lines=[], watermarks=watermarks, errors=[])

    def issue(bucket: list, code: str, line: int, message: str) -> None:
        bucket.append({"code": code, "line": line, "message": message})

    for line_no, line in enumerate(source.splitlines(), 1):
        expanded = line.expandtabs(4)
        # A list-marker-prefixed opener is not a top-level fence. Its indented
        # closer must not become a new opener. Skip this unsupported container
        # instead of upgrading it to a false unclosed-fence error.
        if list_fence:
            marker, size, indent = list_fence
            if expanded.strip() and len(expanded) - len(expanded.lstrip(" ")) < indent:
                list_fence = None
            else:
                closing = FENCE.match(expanded[indent:])
                if closing and closing[1][0] == marker and len(closing[1]) >= size and not closing[2].strip():
                    list_fence = None
                continue
        fence = FENCE.match(line)
        if fence_char:
            if fence and fence[1][0] == fence_char and len(fence[1]) >= fence_size and not fence[2].strip():
                fence_char = None
                fences_checked += 1
            continue
        if fence:
            # Backtick info strings cannot contain backticks in CommonMark.
            if fence[1][0] != "`" or "`" not in fence[2]:
                fence_char, fence_size = fence[1][0], len(fence[1])
                fence_start = line_no
                continue

        container = LIST_FENCE.match(expanded)
        if container and (container[1][0] != "`" or "`" not in container[2]):
            list_fence = (container[1][0], len(container[1]), container.start(1))
            issue(warnings, "unchecked_container_fence", line_no, "列表内代码围栏未作结构验证，已跳过其代码内容")
            continue

        if inventory is not None:
            if line.strip():
                inventory["lines"].append({"line": line_no, "text": line})
            acceptance = AC_DEFINITION.match(line)
            if acceptance:
                ac_id, owner, number, text = acceptance.groups()
                if not id_pattern.fullmatch(owner) or int(owner[1:]) == 0 or int(number) == 0:
                    issue(inventory["errors"], "invalid_acceptance_id", line_no, f"验收编号 {ac_id} 不符合功能编号模式或正数序号")
                if ac_id in inventory["acceptance"]:
                    issue(inventory["errors"], "duplicate_acceptance_id", line_no, f"{ac_id} 重复定义，不能可靠比较")
                else:
                    inventory["acceptance"][ac_id] = {"id": ac_id, "line": line_no, "text": text}

        watermark = WATERMARK.match(line)
        if watermark:
            watermarks.append((watermark[1], line_no))
            continue
        if "<!--" in line and "prd-id-high-watermark:" in line:
            issue(errors, "invalid_watermark", line_no, "编号元数据格式应为 <!-- prd-id-high-watermark: F001 -->")

        heading = ANY_HEADING.match(line)
        definition = HEADING.match(line)
        if definition and definition[3].strip() in {"-", "–", "—", ":", "："}:
            definition = None
        if definition:
            feature_id = definition[2]
            if not id_pattern.fullmatch(feature_id) or int(feature_id[1:]) == 0:
                expected = "F001 起、至少三位数字" if id_style == "canonical" else "F 后跟正整数数字，保留原有补零形式"
                issue(errors, "invalid_id", line_no, f"功能编号 {feature_id} 应为{expected}")
            feature = {"id": feature_id, "line": line_no, "level": len(definition[1]), "dependencies": [], "dependency_line": None, "dependencies_parsed": False, "priority": None, "priority_line": None}
            if feature_id in features:
                issue(errors, "duplicate_id", line_no, f"{feature_id} 重复定义，首次位于第 {features[feature_id]['line']} 行")
                current = None
            else:
                features[feature_id] = feature
                if inventory is not None:
                    inventory["features"][feature_id] = {"id": feature_id, "line": line_no, "text": definition[3]}
                current = feature
            continue
        if heading and current and len(heading[1]) <= current["level"]:
            current = None

        match = FIELD.match(line)
        legacy = LEGACY_FIELD.match(line)
        if legacy and not current:
            issue(warnings, "unscoped_field", line_no, "字段未位于可识别的功能定义下，未检查")
        elif legacy and not match:
            issue(warnings, "unsupported_field", line_no, "非单行字段格式，需人工核对依赖或优先级")
        elif match and current:
            kind, value = match.groups()
            key = "dependency_line" if kind == "前置依赖" else "priority_line"
            if current[key] is not None:
                issue(errors, "duplicate_field", line_no, f"{current['id']} 的{kind}字段重复")
                continue
            current[key] = line_no
            if kind == "前置依赖":
                if value == "无":
                    current["dependencies_parsed"] = True
                    continue
                if not value:
                    issue(warnings, "unsupported_dependencies", line_no, "空字段或多行依赖未解析，需人工核对")
                    continue
                items = re.split(r"\s*[,，、]\s*", value)
                if not all(id_pattern.fullmatch(item) and int(item[1:]) > 0 for item in items):
                    issue(errors, "invalid_dependencies", line_no, "依赖字段仅接受以逗号分隔的功能 ID 或 无；说明另起一段")
                    continue
                current["dependencies"] = list(dict.fromkeys(items))
                current["dependencies_parsed"] = True
                if len(items) != len(current["dependencies"]):
                    issue(warnings, "repeated_dependency", line_no, f"{current['id']} 的依赖重复列出")
            elif re.fullmatch(r"P[0-3]", value):
                current["priority"] = int(value[1])
            else:
                issue(warnings, "unsupported_priority", line_no, "只检查 P0 至 P3 的优先级；其他约定需人工核对")

    if fence_char:
        issue(errors, "unclosed_fence", fence_start, "此顶层代码围栏未关闭，其后内容未作为功能解析")
    if not features:
        issue(errors, "no_features", 0, "未找到受支持的功能定义标题；不能把此结果当作需求检查通过")

    if not watermarks:
        issue(warnings, "missing_watermark", 0, "没有最高编号元数据；需依据既有编号记录维护不复用约束")
    elif len(watermarks) > 1:
        issue(errors, "duplicate_watermark", watermarks[1][1], "最高编号元数据重复")
    if watermarks:
        value, line_no = watermarks[0]
        if not id_pattern.fullmatch(value):
            issue(errors, "invalid_watermark", line_no, "最高编号格式不符合所选编号模式")
        if features and int(value[1:]) < max(int(item[1:]) for item in features):
            issue(errors, "watermark_too_low", line_no, "最高编号小于当前已定义的最大功能号")

    for feature_id, feature in features.items():
        if feature["dependency_line"] is None:
            issue(warnings, "missing_dependency_field", feature["line"], f"{feature_id} 无可检查的单行依赖字段，正文中的依赖未验证")
        for dependency in feature["dependencies"]:
            if dependency not in features:
                issue(errors, "undefined_dependency", feature["dependency_line"], f"{feature_id} 依赖未定义的 {dependency}")
            elif dependency == feature_id:
                issue(errors, "self_dependency", feature["dependency_line"], f"{feature_id} 不能依赖自身")
            elif feature["priority"] is not None and features[dependency]["priority"] is not None and features[dependency]["priority"] > feature["priority"]:
                issue(warnings, "priority_inversion", feature["dependency_line"], f"{feature_id} 的前置项 {dependency} 优先级更低，需确认其不晚于依赖方可用")

    # Iterative DFS avoids recursion limits on large documents. Report each back
    # edge, not every possible path through a strongly connected component.
    color = {feature_id: 0 for feature_id in features}
    for root in features:
        if color[root]:
            continue
        path_ids = [root]
        positions = {root: 0}
        color[root] = 1
        stack = [(root, iter(features[root]["dependencies"]))]
        while stack:
            feature_id, outgoing = stack[-1]
            dependency = next(outgoing, None)
            if dependency is None:
                color[feature_id] = 2
                stack.pop()
                positions.pop(feature_id)
                path_ids.pop()
            elif dependency in features and dependency != feature_id:
                if color[dependency] == 0:
                    color[dependency] = 1
                    positions[dependency] = len(path_ids)
                    path_ids.append(dependency)
                    stack.append((dependency, iter(features[dependency]["dependencies"])))
                elif color[dependency] == 1:
                    cycle = path_ids[positions[dependency]:] + [dependency]
                    issue(errors, "dependency_cycle", features[feature_id]["dependency_line"], "依赖循环：" + " -> ".join(cycle))

    return {
        "path": str(path.resolve()),
        "id_style": id_style,
        "feature_count": len(features),
        "dependency_fields_checked": sum(feature["dependencies_parsed"] for feature in features.values()),
        "closed_top_level_fences": fences_checked,
        "checks": ["feature_ids", "single_line_dependencies", "priority_order", "id_high_watermark", "top_level_fences"],
        "limitations": [
            "编号按原文精确匹配，F1 与 F001 不自动互换；不验证历史编号是否复用",
            "不验证业务语义、验收覆盖、任意正文引用或本地/远程链接",
            "围栏检查限顶层及最多三空格缩进；不解析列表/引用块内的 Markdown 或验证 Mermaid 语法",
        ],
        "errors": errors,
        "warnings": warnings,
    }


def compare_baseline(result: dict, current: dict, baseline_path: Path, id_style: str) -> None:
    """Attach bounded, syntactic change evidence; never infer business approval."""
    previous: dict = {}
    baseline_result = inspect(baseline_path, id_style, inventory=previous)
    baseline_errors = baseline_result["errors"] + previous["errors"]
    result["errors"].extend(current["errors"])
    comparison = {
        "path": baseline_result["path"],
        "status": "not_compared",
        "errors": baseline_errors,
        "warnings": baseline_result["warnings"],
    }
    result["baseline"] = comparison
    result["checks"].extend(["baseline_feature_ids", "baseline_acceptance_ids", "baseline_high_watermark", "changed_passage_candidates"])
    result["limitations"][0] = "编号按原文精确匹配，F1 与 F001 不自动互换；只能检查所给基线的编号边界，不能证明更早历史未复用"
    result["limitations"].extend([
        "增量比较仅识别受支持的功能标题及单行 AC-F数字-正整数：文本；表格、嵌套列表等布局的验收 ID 需人工核对",
        "变更候选是围栏外非空行的文本差异，不判断业务语义、变更授权、验收覆盖或未改旧条款与新增内容是否冲突",
    ])
    if baseline_errors:
        comparison["status"] = "invalid_baseline"
        result["errors"].append({"code": "invalid_baseline", "line": 0, "message": "基线存在结构错误，未进行增量比较；见 baseline.errors，不能视为保留检查通过"})
        return
    if result["errors"]:
        comparison["status"] = "invalid_current"
        return
    comparison["status"] = "compared"

    for key in ("features", "acceptance"):
        old, new = previous[key], current[key]
        comparison[key] = {
            "added": [item for item_id, item in new.items() if item_id not in old],
            "removed": [item for item_id, item in old.items() if item_id not in new],
            "modified": [
                {"id": item_id, "baseline_line": old[item_id]["line"], "line": item["line"], "before": old[item_id]["text"], "after": item["text"]}
                for item_id, item in new.items() if item_id in old and item["text"] != old[item_id]["text"]
            ],
        }

    old_floor = max(int(item_id[1:]) for item_id in previous["features"])
    if previous["watermarks"]:
        old_floor = max(old_floor, int(previous["watermarks"][0][0][1:]))
        if not current["watermarks"]:
            result["errors"].append({"code": "baseline_watermark_removed", "line": 0, "message": "基线已有最高号元数据，当前文档已移除，历史编号上界未保留"})
    if current["watermarks"] and int(current["watermarks"][0][0][1:]) < old_floor:
        result["errors"].append({"code": "baseline_watermark_lowered", "line": current["watermarks"][0][1], "message": f"最高号低于基线已知上界 F{old_floor}，删除功能也不能降低历史最高号"})
    for item in comparison["features"]["added"]:
        if int(item["id"][1:]) <= old_floor:
            result["errors"].append({"code": "new_id_within_baseline_range", "line": item["line"], "message": f"新增 {item['id']} 未超过基线已知上界 F{old_floor}；违反追加编号约定，可能是重编号、补空号或旧号复用，不能仅凭两份文档断定原因"})
    comparison["known_feature_number_floor"] = old_floor

    # Keep source lines rather than keyword-selected "constraints": a checker
    # cannot decide whether a changed sentence is an authorized business rule.
    old_lines, new_lines = previous["lines"], current["lines"]
    matcher = difflib.SequenceMatcher(None, [item["text"].strip() for item in old_lines],
                                     [item["text"].strip() for item in new_lines], autojunk=False)
    comparison["review_candidates"] = [
        {"kind": tag, "before": old_lines[i1:i2], "after": new_lines[j1:j2]}
        for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag in {"replace", "delete"}
    ]
    if any(comparison[key][kind] for key in ("features", "acceptance") for kind in ("removed", "modified")) or comparison["review_candidates"]:
        result["warnings"].append({"code": "baseline_changes_require_review", "line": 0, "message": "存在修改/删除候选；按本次授权核对正文与验收是否同步，并逐项确认原约束应保留还是已获准替换；候选本身不是业务错误"})
    if not previous["acceptance"]:
        result["warnings"].append({"code": "baseline_acceptance_unchecked", "line": 0, "message": "基线未识别到单行 AC 定义，未覆盖验收 ID 保留；不要为适配检查器强制改写旧文档"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="UTF-8 Markdown PRD")
    parser.add_argument("--json", action="store_true", help="Print structured results to stdout")
    parser.add_argument("--id-style", choices=("canonical", "existing"), default="canonical",
                        help="canonical: F001+ convention; existing: preserve positive F-number IDs such as F1/F01")
    parser.add_argument("--baseline", type=Path, help="Optional original PRD; compare IDs, historical number floor and changed passage candidates")
    args = parser.parse_args()
    # Delta output includes arbitrary original text; keep legacy no-baseline
    # stream behavior while making this new mode safe under Windows code pages.
    if args.baseline is not None:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding="utf-8")
    try:
        inventory = {} if args.baseline is not None else None
        result = inspect(args.path, args.id_style, inventory=inventory)
        if args.baseline is not None:
            compare_baseline(result, inventory, args.baseline, args.id_style)
    except (OSError, UnicodeError) as exc:
        failure = {"path": str(args.path), "error": str(exc)}
        if args.baseline is not None:
            failure["baseline_path"] = str(args.baseline)
        print(json.dumps(failure, ensure_ascii=False) if args.json else f"读取失败：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"检查 {result['feature_count']} 个功能、{result['dependency_fields_checked']} 个依赖字段；{len(result['errors'])} 个错误、{len(result['warnings'])} 个警告")
        for level in ("errors", "warnings"):
            for item in result[level]:
                print(f"{level}:{item['line']} [{item['code']}] {item['message']}")
        if "baseline" in result:
            comparison = result["baseline"]
            print(f"基线比较：{comparison['status']}（{comparison['path']}）")
            if comparison["status"] == "compared":
                for key, label in (("features", "功能"), ("acceptance", "验收")):
                    for kind, action in (("added", "新增"), ("removed", "删除"), ("modified", "修改")):
                        for item in comparison[key][kind]:
                            print(f"{label}{action} [{item['id']}] {json.dumps(item, ensure_ascii=False)}")
                for candidate in comparison["review_candidates"]:
                    print("人工核对候选：" + json.dumps(candidate, ensure_ascii=False))
            else:
                for item in comparison["errors"]:
                    print(f"baseline.errors:{item['line']} [{item['code']}] {item['message']}")
        print("检查范围：编号、单行依赖、优先级、最高号及顶层围栏；不代表业务、链接或 Mermaid 已验证")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

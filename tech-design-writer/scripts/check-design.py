#!/usr/bin/env python3
"""Read-only Markdown structure and explicit requirement-reference checks.

This is not a semantic coverage or design-quality validator. Top-level ATX
headings/fences are supported; nested list/quote Markdown needs human review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
DEFINITION = re.compile(r"^([FAT][0-9]+)(?:[ \t]*[-–—:：][ \t]*|[ \t]+)(\S.*?)$")
REFERENCE = re.compile(r"(?<![A-Za-z0-9_])F[0-9]+(?![A-Za-z0-9_])")
ID = re.compile(r"F[0-9]+\Z")
WATERMARK = re.compile(r"^\s*<!--\s*design-id-high-watermarks:\s*(.*?)\s*-->\s*$")
CONTAINER_FENCE = re.compile(r"^ {0,3}(?:>|[-+*] |[0-9]+[.)] ).*(?:`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)")
LINK = re.compile(r"\[([^\]\n]*)\]\((<[^>\n]+>|[^()\s]+)\)")


def read_source(path):
    text = path.read_text(encoding='utf-8-sig')
    if '\0' in text:
        raise ValueError(f'{path} 包含 NUL，不作为 Markdown 文本检查')
    return text


def finding(code, line, message):
    return {"code": code, "line": line, "message": message}


def markdown(text):
    """Return top-level prose, heading spans and explicit parse limitations."""
    prose, headings, errors, warnings, marks = [], [], [], [], []
    fence = None
    html_comment = False
    container_indent = None
    for number, raw in enumerate(text.splitlines(), 1):
        expanded = raw.expandtabs(4)
        # Unsupported list blocks stay out of machine evidence until dedented.
        if container_indent is not None:
            if expanded.strip() and len(expanded) - len(expanded.lstrip()) < container_indent:
                container_indent = None
            else:
                continue
        match = FENCE.match(raw)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                fence = None
            continue
        if match and (match[1][0] != '`' or '`' not in match[2]):
            if not html_comment:
                fence = (match[1][0], len(match[1]), number)
                continue
        if not html_comment and CONTAINER_FENCE.match(expanded):
            warnings.append(finding('unchecked_container', number, '列表/引用块内 Markdown 不作为覆盖证据，需人工复核'))
            if not expanded.lstrip().startswith('>'):
                marker = re.match(r'^ {0,3}(?:[-+*]|[0-9]+[.)]) +', expanded)
                if marker:
                    container_indent = marker.end()
            continue
        if not html_comment and (expanded.startswith('    ') or raw.lstrip().startswith('>')):
            continue
        watermark = WATERMARK.match(raw) if not html_comment else None
        if watermark:
            marks.append((number, watermark[1]))
        # Same-line code spans may contain literal HTML comment delimiters.
        code_spans = {item.start(): item for item in INLINE_CODE.finditer(raw)}
        visible = ''
        position = 0
        while position < len(raw):
            if html_comment:
                closing = raw.find('-->', position)
                if closing < 0:
                    break
                position = closing + 3
                html_comment = False
            elif position in code_spans:
                span = code_spans[position]
                visible += span[0]
                position = span.end()
            elif raw.startswith('<!--', position):
                html_comment = True
                position += 4
            else:
                visible += raw[position]
                position += 1
        raw = visible
        heading = HEADING.match(raw)
        if heading:
            headings.append({'line': number, 'level': len(heading[1]), 'title': heading[2].strip()})
        else:
            prose.append((number, raw))
    if fence:
        errors.append(finding('unclosed_fence', fence[2], '顶层代码围栏未闭合'))
    if html_comment:
        errors.append(finding('unclosed_html_comment', len(text.splitlines()), 'HTML 注释未闭合'))
    return prose, headings, errors, warnings, marks


def definitions(headings, prefixes):
    result = []
    for heading in headings:
        match = DEFINITION.match(heading['title'])
        if match and match[1][0] in prefixes and match[2] not in {'-', '–', '—', ':', '：'}:
            result.append((match[1], heading['line']))
    return result


def inspect(design: Path, requirements: Path | None = None, required_ids=()):
    source = read_source(design)
    prose, headings, errors, warnings, marks = markdown(source)
    nonempty = [(line, text) for line, text in prose if text.strip() and not re.fullmatch(r'[-*_ ]{3,}', text)]
    if not nonempty:
        errors.append(finding('no_prose', 0, '没有可检查的正文；空文件、标题或代码骨架不算文档完成'))
    end = len(source.splitlines()) + 1
    for index, heading in enumerate(headings):
        boundary = next((entry['line'] for entry in headings[index + 1:] if entry['level'] <= heading['level']), end)
        if not any(heading['line'] < number < boundary for number, _ in nonempty):
            warnings.append(finding('section_without_prose', heading['line'], f"小节 {heading['title']} 未见围栏外正文，需核实内容或纯代码/图表达"))

    declared = definitions(headings, 'AT')
    seen = {}
    for identifier, line in declared:
        if int(identifier[1:]) == 0:
            errors.append(finding('zero_identifier', line, f'{identifier} 的编号应为正整数'))
        if identifier in seen:
            errors.append(finding('duplicate_identifier', line, f'{identifier} 重复定义，首次在第 {seen[identifier]} 行'))
        seen.setdefault(identifier, line)

    # Only standalone, top-level watermark comments are recognized.
    if len(marks) > 1:
        errors.append(finding('duplicate_watermark', marks[1][0], '编号最高号记录重复，需人工合并'))
    marked_types = set()
    for number, value in marks:
        tokens = value.split()
        if not tokens or any(not re.fullmatch(r'[AT][0-9]+', token) for token in tokens) or len({token[0] for token in tokens}) != len(tokens):
            errors.append(finding('invalid_watermark', number, '最高号应为每类一个 A数字/T数字'))
            continue
        marked_types.update(token[0] for token in tokens)
        for token in tokens:
            maximum = max((int(item[1:]) for item in seen if item[0] == token[0]), default=0)
            if int(token[1:]) < maximum:
                errors.append(finding('watermark_too_small', number, f'{token} 小于当前标题定义的最大编号 {maximum}'))
    for prefix in sorted({item[0] for item in seen} - marked_types):
        warnings.append(finding('missing_watermark_type', 0, f'已有 {prefix} 编号标题但缺少该类最高号，历史上界需人工核对'))

    scope = list(dict.fromkeys(required_ids))
    if any(not ID.fullmatch(item) or int(item[1:]) == 0 for item in scope):
        raise ValueError('--require-id 只接受 F 后跟正整数，保留原补零形式')
    requirements_path = None
    if requirements is not None:
        requirements_path = str(requirements.resolve())
        _, requirement_headings, source_errors, source_warnings, _ = markdown(read_source(requirements))
        if source_errors:
            raise ValueError('需求来源结构错误：' + '; '.join(item['message'] for item in source_errors))
        requirement_defs = definitions(requirement_headings, 'F')
        available = [item for item, _ in requirement_defs]
        if not available or any(int(item[1:]) == 0 for item in available) or len(available) != len(set(available)):
            raise ValueError('需求来源无有效唯一 F 编号标题，不能自动推定检查范围')
        if scope and not set(scope) <= set(available):
            raise ValueError('--require-id 包含需求来源中未定义的编号')
        if not scope:
            scope = available
        warnings.extend({**item, 'source': requirements_path} for item in source_warnings)
    reference_lines = {}

    def record(identifier, number):
        reference_lines.setdefault(identifier, []).append(number)

    def qualified(target, number):
        source_name, separator, identifier = target.rpartition('#')
        if not separator or not ID.fullmatch(identifier):
            return False
        if requirements is None:
            record(identifier, number)
            warnings.append(finding('unchecked_reference_source', number, f'{target} 未提供需求文件，未核对来源'))
        else:
            candidate = Path(unquote(source_name))
            if not candidate.is_absolute():
                candidate = design.resolve().parent / candidate
            # Only exact local paths relative to the design are supported.
            if source_name and candidate.resolve() == requirements.resolve():
                record(identifier, number)
            else:
                warnings.append(finding('unmatched_reference_source', number, f'{target} 未匹配指定需求来源，不计入其引用'))
        return True

    for number, raw in prose + [(entry['line'], entry['title']) for entry in headings]:
        def link_reference(match):
            target = match[2].removeprefix('<').removesuffix('>')
            if not qualified(target, number) and REFERENCE.search(match[0]):
                warnings.append(finding('unchecked_link_reference', number, '链接内 F 编号未形成受支持的 来源#编号 引用，需人工核对'))
            return ' ' * len(match[0])

        raw = LINK.sub(link_reference, raw)
        for match in REFERENCE.finditer(raw):
            if match.start() and raw[match.start() - 1] == '#':
                prefix = re.search(r'[^\s`\[\]()<>，。；：]+$', raw[:match.start() - 1])
                qualified((prefix[0] if prefix else '') + '#' + match[0], number)
            else:
                record(match[0], number)
    missing = [item for item in scope if item not in reference_lines]
    for identifier in missing:
        errors.append(finding('missing_requirement_reference', 0, f'未在受支持的正文中找到本次范围 {identifier} 的显式引用；这不等同于业务语义缺失判断'))
    return {
        'file': str(design.resolve()), 'requirements': requirements_path,
        'required_ids': scope, 'reference_lines': {item: sorted(set(reference_lines.get(item, []))) for item in scope},
        'missing_requirement_references': missing,
        'definitions': [{'id': item, 'line': line} for item, line in declared],
        'errors': errors, 'warnings': warnings,
        'coverage': {'checked': ['top_level_fences', 'nonempty_prose', 'AT_heading_ids', 'explicit_F_references', 'top_level_watermark'],
                     'unchecked': ['business_semantics', 'requirement_fulfillment', 'contract_fields', 'acceptance_quality', 'Mermaid_rendering', 'table_defined_AT_ids', 'nested_Markdown', 'multiline_code_spans', 'complex_or_reference_style_links', 'historical_id_reuse']},
        'semantic_coverage_verified': False,
    }


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('design', type=Path)
    parser.add_argument('--requirements', type=Path)
    parser.add_argument('--require-id', action='append', default=[])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = inspect(args.design, args.requirements, args.require_id)
    except (OSError, UnicodeError, ValueError) as error:
        if args.json:
            print(json.dumps({'error': str(error), 'semantic_coverage_verified': False}, ensure_ascii=False))
        else:
            print(f'输入错误：{error}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"结构/引用错误 {len(report['errors'])}，人工复核候选 {len(report['warnings'])}；未验证业务语义")
        for item in report['errors'] + report['warnings']:
            print(f"{item['line']}: {item['code']}: {item['message']}")
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())

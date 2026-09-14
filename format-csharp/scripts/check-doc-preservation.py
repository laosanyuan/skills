"""Compare HTTP(S) URL candidates in comment-shaped lines; never edit source.

This is deliberately not a C# lexer or a documentation correctness checker.
Exit 0: comparison completed without missing candidates or changed punctuation
ambiguities (including no baseline candidates); 1: review candidates; 2: input
or execution error. All results describe this limited textual check only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from scan_scope import read_source


COMMENT_LINE = re.compile(r"^\s*(?://|/\*|\*)")
URL_START = re.compile(r"https?://", re.IGNORECASE)
XML_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")
XML_VALUES = {"&amp;": "&", "&lt;": "<", "&gt;": ">",
              "&quot;": '"', "&apos;": "'"}
SENTENCE_END = ".,;!?。，；！？"
CLOSERS = {"）": "（", ")": "(", "]": "[", "}": "{"}
LIMITATIONS = [
    "Only HTTP(S) tokens on lines starting with //, /* or * are compared",
    "XML entities are decoded only inside candidates starting with literal "
    "http:// or https:// (case-insensitive); XML-encoded schemes are not covered",
    "Line shapes are not C# syntax: raw/verbatim string examples may be candidates",
    "Inline comments and unstarred block-comment continuation lines are not covered",
    "URLs split across physical lines, non-HTTP links, cref/include targets, versions "
    "and natural-language contracts are not checked",
    "Unique URL sets are compared within one file; duplicate counts and declaration "
    "attachment are not checked, so moving a URL to the wrong member can go unnoticed",
    "No URL is fetched or validated; no result proves documentation correctness",
]


def decode_xml(value: str) -> str:
    """Decode only complete XML entities, not HTML's partial query-string names."""
    def replace(match: re.Match) -> str:
        token = match.group()
        if token in XML_VALUES:
            return XML_VALUES[token]
        number = int(token[3:-1], 16) if token.startswith("&#x") else int(token[2:-1])
        if number in (9, 10, 13) or 0x20 <= number <= 0xD7FF or (
                0xE000 <= number <= 0xFFFD) or 0x10000 <= number <= 0x10FFFF:
            return chr(number)
        return token
    return XML_ENTITY.sub(replace, value)


def plain_url(value: str) -> tuple[str, str]:
    """Separate likely prose punctuation, retaining ambiguity for review."""
    trimmed = value
    while trimmed:
        last = trimmed[-1]
        if last in SENTENCE_END or (last in CLOSERS and
                                   trimmed.count(last) > trimmed.count(CLOSERS[last])):
            trimmed = trimmed[:-1]
        else:
            break
    return trimmed, value[len(trimmed):]


def extract(source: str) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for line_number, line in enumerate(source.splitlines(), 1):
        if not COMMENT_LINE.match(line):
            continue
        consumed_until = 0
        for match in URL_START.finditer(line):
            start = match.start()
            if start < consumed_until:
                continue
            quote = line[start - 1] if start else ""
            quoted = quote in ('"', "'")
            end = start
            while end < len(line):
                char = line[end]
                if (quoted and char == quote) or (not quoted and (
                        char.isspace() or char in '<>"\'')):
                    break
                if line.startswith("*/", end):
                    break
                end += 1
            consumed_until = end
            raw = line[start:end]
            decoded = decode_xml(raw)
            url, punctuation = (decoded, "") if quoted else plain_url(decoded)
            # A bare scheme is still a candidate; URL validity is outside this check
            found.setdefault(url, []).append({
                "line": line_number, "column": start + 1, "raw": raw,
                "decoded": decoded, "quoted": quoted,
                "possible_sentence_punctuation": punctuation,
            })
    return found


def compare(before: str, after: str) -> dict:
    old, new = extract(before), extract(after)
    missing = [{"url": url, "before_occurrences": old[url]}
               for url in sorted(old.keys() - new.keys())]
    punctuation = []
    for url in sorted(old.keys() & new.keys()):
        old_forms = {item["decoded"] for item in old[url]}
        new_forms = {item["decoded"] for item in new[url]}
        if old_forms != new_forms and any(item["possible_sentence_punctuation"]
                                          for item in old[url] + new[url]):
            punctuation.append({"url": url, "before_occurrences": old[url],
                                "after_occurrences": new[url]})
    review = bool(missing or punctuation)
    return {
        "schema_version": 1,
        "status": ("review_required" if review else "no_baseline_url_candidates"
                   if not old else "no_missing_url_candidates"),
        "comparison_completed": True,
        "requires_review": review,
        "source_context_verified": False,
        "documentation_preserved": None,
        "before_unique_url_candidates": len(old),
        "after_unique_url_candidates": len(new),
        "missing_url_candidates": missing,
        "punctuation_change_candidates": punctuation,
        "before_candidates": old,
        "after_candidates": new,
        "limitations": LIMITATIONS,
    }


def source_path(value: str) -> Path:
    path = Path(value).resolve(strict=True)
    if not path.is_file() or path.suffix.casefold() != ".cs":
        raise ValueError(f"Expected a .cs file: {value}")
    return path


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, help="Unmodified baseline .cs file")
    parser.add_argument("--after", required=True, help="Edited .cs file")
    parser.add_argument("--encoding", default="auto", help="BOM-aware auto (default), "
                        "otherwise strict UTF-8; or an explicit codec")
    parser.add_argument("--json", action="store_true", help="One JSON object on stdout")
    args = parser.parse_args()
    try:
        before, after = source_path(args.before), source_path(args.after)
        if before == after or before.samefile(after):
            raise ValueError("Before and after must be distinct files; preserve a baseline copy")
        old_text, new_text = read_source(before, args.encoding), read_source(after, args.encoding)
        if not old_text.strip() or "\x00" in old_text or "\x00" in new_text:
            raise ValueError("Baseline is empty or input contains NUL; check source and encoding")
        result = compare(old_text, new_text)
        result.update({"before": str(before), "after": str(after)})
        code = 1 if result["requires_review"] else 0
    except (OSError, ValueError) as error:
        result = {"schema_version": 1, "status": "error", "comparison_completed": False,
                  "requires_review": True, "documentation_preserved": None,
                  "error": str(error), "limitations": LIMITATIONS}
        code = 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["status"])
        if code == 2:
            print(result["error"])
        else:
            for item in result["missing_url_candidates"]:
                print(f"Missing URL candidate: {item['url']}")
            for item in result["punctuation_change_candidates"]:
                print(f"Review URL punctuation change: {item['url']}")
        print("Limited line-shape comparison only; inspect context and declaration attachment")
    return code


if __name__ == "__main__":
    sys.exit(main())

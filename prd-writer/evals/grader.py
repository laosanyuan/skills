import json
import re
import sys
from pathlib import Path

# This script lives at prd-writer/evals/grader.py.
EVALS_DIR = Path(__file__).resolve().parent
SKILL_DIR = EVALS_DIR.parent
REPO_ROOT = SKILL_DIR.parent

# Pass the iteration workspace as the first arg. Defaults to workspace/prd-writer/iteration-1 at repo root.
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else REPO_ROOT / "workspace" / "prd-writer" / "iteration-1")
FIXTURE_SHOP = (EVALS_DIR / "fixtures" / "PRD-shop-app.md").read_text(encoding="utf-8")

def read_file(p):
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""

def strip_markdown_emphasis(text):
    """Remove markdown bold/italic markers so substring searches work on text like '本功能**不**包含'."""
    return re.sub(r"\*+", "", text)

def has_abc_options(text):
    return bool(re.search(r"\(a\)|\(b\)|\(c\)|（a）|（b）|（c）", text))

def grade_eval_0(out_dir):
    transcript = read_file(out_dir / "transcript.md")
    prds = list(out_dir.glob("PRD-*.md"))
    prd_text = "\n\n".join(read_file(p) for p in prds)
    prd_plain = strip_markdown_emphasis(prd_text)
    has_options = has_abc_options(transcript) or has_abc_options(prd_text)
    # count feature definitions (### F00X or | F00X |)
    fxx_in_table = set(re.findall(r"\|\s*(F00[0-9])\s*\|", prd_text))
    fxx_in_details = set(re.findall(r"###\s*(F00[0-9])\s*[-—]", prd_text))
    feature_count = max(len(fxx_in_table), len(fxx_in_details))
    has_discussion_list = ("待讨论功能清单" in prd_text or "待讨论功能" in prd_text or "待讨论清单" in prd_text)
    suggestion_count = prd_text.count("建议方案")
    return [
        {"text": "transcript 中明确列出了向用户追问的问题",
         "passed": ("问" in transcript or "追问" in transcript or "?" in transcript or "？" in transcript),
         "evidence": f"transcript 长度 {len(transcript)},含问题标记"},
        {"text": "至少一个追问带 (a)/(b)/(c) 选项",
         "passed": has_options,
         "evidence": "transcript/PRD 中检测到 (a)(b)(c) 选项" if has_options else "未找到 (a)(b)(c) 选项"},
        {"text": "PRD 使用 F + 三位数字编号(F001、F002)",
         "passed": bool(re.search(r"F00[0-9]", prd_text)),
         "evidence": "PRD 含 F00X" if re.search(r"F00[0-9]", prd_text) else "未使用 F00X"},
        {"text": "至少一个功能详情包含'本功能不包含'边界声明",
         "passed": ("不包含" in prd_plain),
         "evidence": f"PRD '不包含' 出现 {prd_plain.count('不包含')} 次"},
        {"text": "模糊处使用'建议方案'或'待用户确认'标注",
         "passed": ("建议方案" in prd_text or "待用户确认" in prd_text),
         "evidence": f"建议方案 {suggestion_count} 次, 待用户确认 {prd_text.count('待用户确认')} 次"},
        {"text": "没有'待定'或'TBD'占位符",
         "passed": ("待定" not in prd_text and "TBD" not in prd_text and "tbd" not in prd_text.lower()),
         "evidence": "无待定/TBD" if "待定" not in prd_text else "出现待定"},
        {"text": "线性流程不强行插入 Mermaid 图(允许 0 张)",
         "passed": True,
         "evidence": f"mermaid 代码块数 = {prd_text.count('```mermaid')};这个 case 允许 0 张图"},
        {"text": "冷启动只生成 ≤ 3 个功能,或包含'待讨论功能清单'章节",
         "passed": (feature_count <= 3) or has_discussion_list,
         "evidence": f"功能数 = {feature_count}, 含待讨论清单 = {has_discussion_list}"},
        {"text": "'建议方案'出现 ≤ 12 次(避免信息密度爆炸)",
         "passed": suggestion_count <= 12,
         "evidence": f"建议方案出现 {suggestion_count} 次(阈值 12)"},
    ]

def grade_eval_1(out_dir):
    transcript = read_file(out_dir / "transcript.md")
    prd = read_file(out_dir / "PRD-study-app.md")
    # find F003 section and check for 前置依赖
    f003_match = re.search(r"###\s*F003.*?(?=###\s*F00[0-9]|\Z)", prd, re.DOTALL)
    f003_section = f003_match.group(0) if f003_match else ""
    has_dep_field = ("前置依赖" in f003_section)
    return [
        {"text": "transcript 中明确说明读取了 PRD-study-app.md",
         "passed": ("study-app" in transcript or "已有" in transcript),
         "evidence": "transcript 含读取记录" if "study-app" in transcript or "已有" in transcript else "未找到记录"},
        {"text": "F001 和 F002 的核心内容保留",
         "passed": ("F001" in prd and "F002" in prd and "用户注册登录" in prd and "每日打卡" in prd),
         "evidence": "F001/F002 保留" if "F001" in prd and "F002" in prd else "缺失"},
        {"text": "新增功能编号为 F003",
         "passed": "F003" in prd,
         "evidence": "找到 F003" if "F003" in prd else "未找到"},
        {"text": "顶部日期改为 2026-05-20",
         "passed": "2026-05-20" in prd[:300],
         "evidence": "顶部含 2026-05-20" if "2026-05-20" in prd[:300] else "未更新"},
        {"text": "有追问或'建议方案'标注",
         "passed": ("建议方案" in prd or "待用户确认" in prd),
         "evidence": f"建议方案 {prd.count('建议方案')} 次"},
        {"text": "transcript 识别 F002 排除条款与新需求冲突",
         "passed": ("F002" in transcript and "点赞" in transcript and ("不包含" in transcript or "排除" in transcript or "冲突" in transcript or "矛盾" in transcript)),
         "evidence": "识别冲突" if "F002" in transcript and "点赞" in transcript else "未识别"},
        {"text": "功能列表表格新增 F003 一行",
         "passed": bool(re.search(r"\|\s*F003\s*\|", prd)),
         "evidence": "表格含 F003" if re.search(r"\|\s*F003\s*\|", prd) else "缺失"},
        {"text": "F003 含'前置依赖'字段(点赞依赖 F001 登录 + F002 打卡)",
         "passed": has_dep_field,
         "evidence": "F003 含'前置依赖'字段" if has_dep_field else "F003 未规范使用'前置依赖'字段"},
    ]

def grade_eval_2(out_dir):
    transcript = read_file(out_dir / "transcript.md")
    prd = read_file(out_dir / "PRD-shop-app.md")
    file_unchanged = prd == FIXTURE_SHOP
    has_options = has_abc_options(transcript)
    keywords_hit = sum(1 for k in ["全局", "局部", "例外", "重新考虑", "重新", "调整"] if k in transcript)
    return [
        {"text": "PRD-shop-app.md 未被修改",
         "passed": file_unchanged,
         "evidence": "文件未变" if file_unchanged else "文件被修改"},
        {"text": "transcript 引用 F001",
         "passed": ("F001" in transcript or "用户注册登录" in transcript),
         "evidence": "提及 F001" if "F001" in transcript else "未引用"},
        {"text": "transcript 引用'4-16 位'规则原文",
         "passed": "4-16" in transcript,
         "evidence": "引用 4-16" if "4-16" in transcript else "未引用"},
        {"text": "transcript 给三个选项:全局/局部/重新考虑",
         "passed": has_options and keywords_hit >= 2,
         "evidence": f"options={has_options}, 关键词命中 {keywords_hit}/6"},
        {"text": "transcript 明确说未修改文件",
         "passed": any(k in transcript for k in ["未修改", "没有修改", "不修改", "还没改", "未动", "未改", "暂不修改", "不动文档", "没改"]),
         "evidence": "声明未改" if any(k in transcript for k in ["未修改", "没有修改", "不修改", "还没改", "未动", "未改", "暂不修改", "不动文档", "没改"]) else "未声明"},
    ]

def grade_eval_3(out_dir):
    transcript = read_file(out_dir / "transcript.md")
    prd = read_file(out_dir / "PRD-shop-app.md")
    mermaid_blocks = re.findall(r"```mermaid\s*\n(.*?)```", prd, re.DOTALL)
    blocks_text = "\n".join(mermaid_blocks)
    has_flowchart = any("flowchart" in b or "graph " in b for b in mermaid_blocks)
    has_sequence = any("sequenceDiagram" in b for b in mermaid_blocks)
    has_state = any("stateDiagram" in b for b in mermaid_blocks)
    all_labels = []
    for b in mermaid_blocks:
        all_labels.extend(re.findall(r"\[([^\[\]]+)\]|\{([^\{\}]+)\}|\(([^\(\)]+)\)", b))
    flat = [x or y or z for x, y, z in all_labels]
    too_long = [l for l in flat if len(l) > 20]
    all_5_states = all(k in prd for k in ["待支付", "已支付", "已发货", "已收货", "已取消"])
    return [
        {"text": "transcript 中说明读取 PRD-shop-app.md,并识别下一编号 F004",
         "passed": ("shop-app" in transcript or "已有" in transcript) and "F004" in transcript,
         "evidence": "含 PRD 读取记录 + F004 编号"},
        {"text": "新功能编号为 F004",
         "passed": "F004" in prd,
         "evidence": "PRD 含 F004" if "F004" in prd else "未找到"},
        {"text": "PRD 中包含至少一个 ```mermaid 代码块",
         "passed": len(mermaid_blocks) >= 1,
         "evidence": f"找到 {len(mermaid_blocks)} 个 mermaid 块"},
        {"text": "至少有一个 flowchart 或 sequenceDiagram 表达分支",
         "passed": has_flowchart or has_sequence,
         "evidence": f"flowchart={has_flowchart}, sequenceDiagram={has_sequence}"},
        {"text": "包含 stateDiagram-v2 表达订单状态机(5 个状态)",
         "passed": has_state and all_5_states,
         "evidence": f"stateDiagram={has_state}, 5 状态名完整={all_5_states}"},
        {"text": "图中体现库存校验分支",
         "passed": "库存" in blocks_text,
         "evidence": "mermaid 含'库存'" if "库存" in blocks_text else "图中未体现"},
        {"text": "图中体现支付方式或支付成败分支",
         "passed": "支付" in blocks_text,
         "evidence": "mermaid 含'支付'" if "支付" in blocks_text else "图中未体现"},
        {"text": "Mermaid 图节点标签简短(无单节点>20 字)",
         "passed": len(too_long) == 0,
         "evidence": f"过长节点 {len(too_long)} 个" + (f": {too_long[:3]}" if too_long else "")},
        {"text": "顶部'最后更新'改为 2026-05-20",
         "passed": "2026-05-20" in prd[:300],
         "evidence": "顶部含 2026-05-20" if "2026-05-20" in prd[:300] else "未更新"},
        {"text": "功能列表表格新增 F004 一行",
         "passed": bool(re.search(r"\|\s*F004\s*\|", prd)),
         "evidence": "表格含 F004" if re.search(r"\|\s*F004\s*\|", prd) else "缺失"},
    ]

def grade_eval_4(out_dir):
    """refusal-out-of-scope: user asks for translation, skill should refuse"""
    transcript = read_file(out_dir / "transcript.md")
    prd = read_file(out_dir / "PRD-shop-app.md")
    file_unchanged = (prd == FIXTURE_SHOP)
    # check if any English-translated PRD was generated
    other_files = [p for p in out_dir.iterdir() if p.is_file() and p.name not in {"transcript.md", "PRD-shop-app.md"}]
    english_prd = any(
        p.suffix == ".md" and "PRD" in p.name.upper()
        and any(c.isalpha() and ord(c) < 128 for c in read_file(p)[:200])
        and read_file(p).count("功能") < 3  # heuristic: english version has no Chinese 功能 keyword
        for p in other_files
    )
    refused_keywords = ["不适用", "不做", "不该", "out of scope", "out-of-scope", "翻译", "不是 prd-writer", "更适合"]
    explains_refusal = any(k in transcript for k in refused_keywords) and ("翻译" in transcript or "translat" in transcript.lower())
    suggest_alternative = ("更适合" in transcript or "另" in transcript or "替代" in transcript or "其他" in transcript or "你可以" in transcript or "alternative" in transcript.lower() or "建议" in transcript)
    return [
        {"text": "skill 没有真的把 PRD 翻译成英文",
         "passed": not english_prd,
         "evidence": "未生成英文版 PRD" if not english_prd else "检测到工作目录中出现疑似英文 PRD"},
        {"text": "PRD-shop-app.md 未被改动",
         "passed": file_unchanged,
         "evidence": "文件未变" if file_unchanged else "文件被修改"},
        {"text": "transcript 明确告诉用户这不是 prd-writer 应该做的事",
         "passed": explains_refusal,
         "evidence": "transcript 包含拒绝/翻译关键词组合" if explains_refusal else "未明确拒绝"},
        {"text": "transcript 建议了替代方案或询问确认",
         "passed": suggest_alternative,
         "evidence": "transcript 中提到替代方案或确认" if suggest_alternative else "未提供替代或确认"},
    ]

def grade_eval_5(out_dir):
    """undo-delete-feature: user asks to delete F003"""
    transcript = read_file(out_dir / "transcript.md")
    prd = read_file(out_dir / "PRD-shop-app.md")
    f003_in_table = bool(re.search(r"\|\s*F003\s*\|", prd))
    f003_in_details = bool(re.search(r"###\s*F003", prd))
    has_f001 = "F001" in prd and "用户注册登录" in prd
    has_f002 = "F002" in prd and "商品浏览" in prd
    has_date = "2026-05-20" in prd[:300]
    mentions_summary = ("删了 F003" in transcript or "F003 被删" in transcript or "删除 F003" in transcript or
                       "删除了 F003" in transcript or "移除 F003" in transcript or "删掉 F003" in transcript or
                       "已删除" in transcript or "已删了" in transcript or "已移除" in transcript)
    return [
        {"text": "功能列表表格里不再有 F003",
         "passed": not f003_in_table,
         "evidence": "表格已无 F003" if not f003_in_table else "F003 行仍在表格中"},
        {"text": "功能详情里不再有 F003 的整段",
         "passed": not f003_in_details,
         "evidence": "F003 详情段已删除" if not f003_in_details else "F003 详情段未删除"},
        {"text": "F001 和 F002 保留",
         "passed": has_f001 and has_f002,
         "evidence": "F001/F002 保留" if has_f001 and has_f002 else "F001 或 F002 缺失"},
        {"text": "顶部日期改为 2026-05-20",
         "passed": has_date,
         "evidence": "日期已更新" if has_date else "日期未更新"},
        {"text": "transcript 中说明改了什么(列出 F003 被删 或 等价描述)",
         "passed": mentions_summary,
         "evidence": "transcript 描述了删除动作" if mentions_summary else "transcript 未明确说明删除结果"},
    ]

GRADERS = {
    "eval-0_cold-start-vague": grade_eval_0,
    "eval-1_iteration-add-feature": grade_eval_1,
    "eval-2_conflict-detection": grade_eval_2,
    "eval-3_branching-flow-needs-diagram": grade_eval_3,
    "eval-4_refusal-out-of-scope": grade_eval_4,
    "eval-5_undo-delete-feature": grade_eval_5,
}

results = []
for eval_dir in sorted(ROOT.glob("eval-*")):
    grader = GRADERS.get(eval_dir.name)
    if not grader:
        continue
    for cfg_dir in sorted(eval_dir.iterdir()):
        if not cfg_dir.is_dir() or not (cfg_dir / "run-0").exists():
            continue
        run_dir = cfg_dir / "run-0"
        out_dir = run_dir / "outputs"
        expectations = grader(out_dir)
        passed = sum(1 for e in expectations if e["passed"])
        total = len(expectations)
        grading = {
            "expectations": expectations,
            "summary": {
                "passed": passed,
                "failed": total - passed,
                "total": total,
                "pass_rate": round(passed / total, 4) if total else 0.0,
            },
        }
        (run_dir / "grading.json").write_text(
            json.dumps(grading, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        results.append(f"{eval_dir.name}/{cfg_dir.name}: {passed}/{total} ({passed/total*100:.0f}%)")

print("\n".join(results))

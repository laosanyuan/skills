# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个仓库是什么

我的 Claude Code skill 合集。每个子目录是**一个独立的 skill**,自包含。当前有 [prd-writer/](prd-writer/),后续会加更多。

## 约定

**每个 skill 都遵循同样的目录约定:**

```
<skill-name>/
├── SKILL.md              # YAML frontmatter + 指令本体(唯一的产品文件)
└── evals/                # 测试 / 评分相关(打包 .skill 时自动排除)
    ├── evals.json        # 功能场景测试 prompts
    ├── fixtures/         # 测试输入文件
    ├── grader.py         # 该 skill 私有的评分逻辑
    └── trigger-eval-*.json   # description 触发判断的测试(可选)
```

**唯一需要修改的产品文件就是 `<skill-name>/SKILL.md`**。其他都是支撑工具。

**workspace/ 目录**(已 .gitignore):评估时各轮迭代产物堆这里,组织成 `workspace/<skill-name>/iteration-N/eval-*/`。不进 git。

## 改某个 skill 时的标准工作流

以改 prd-writer 为例(其他 skill 类比):

1. **Snapshot baseline 作回归对照**:
   ```bash
   cp -r prd-writer workspace/snapshots/prd-writer-iter-N/
   ```
2. **编辑** `prd-writer/SKILL.md`
3. **对每个 eval 起两个 subagent 并发跑**(读 `prd-writer/evals/evals.json` 里的 prompts):
   - `new_skill`: 用刚改完的 `prd-writer/SKILL.md`
   - `old_skill`: 用 `workspace/snapshots/prd-writer-iter-N/SKILL.md`
   - 输出保存到 `workspace/prd-writer/iteration-N+1/<eval-name>/<config>/run-0/outputs/`
   - 把 fixture 文件(如有)预先复制到 `outputs/` 里
4. **保存 timing**: subagent 返回的 notification 里有 `total_tokens` + `duration_ms`,立刻写到 `run-0/timing.json`(只这一次能拿到)
5. **跑 grader**:
   ```bash
   PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python prd-writer/evals/grader.py workspace/prd-writer/iteration-N+1
   ```
6. **聚合 + 启动 viewer**(用 skill-creator 工具链):
   ```bash
   python -m scripts.aggregate_benchmark workspace/prd-writer/iteration-N+1 --skill-name prd-writer
   python <skill-creator>/eval-viewer/generate_review.py workspace/prd-writer/iteration-N+1 ...
   ```

## 加新 eval / 新断言

如果给某个 skill 加新场景:

1. 在 `<skill>/evals/evals.json` 加 prompt
2. 在 `<skill>/evals/fixtures/` 放需要的输入文件(可选)
3. 在 `<skill>/evals/grader.py` 加一个 `grade_eval_N` 函数,并在文件末尾的 `GRADERS` dict 注册

grader.py 写断言时几个 helper 已经定义好:`read_file`, `strip_markdown_emphasis`(处理 `本功能**不**包含` 这种 markdown 加粗导致的 substring 匹配失败),`has_abc_options`。

## description 优化

每个 skill 可有 `<skill>/evals/trigger-eval-*.json`(20 条左右,10 应触发 + 10 不该触发)。用 skill-creator 的 `run_loop.py` 跑 5 轮自动迭代:

```bash
python -m scripts.run_loop \
  --eval-set prd-writer/evals/trigger-eval-final.json \
  --skill-path prd-writer \
  --model claude-opus-4-7 \
  --max-iterations 5
```

**需要 `claude` CLI 已登录**(`claude /login`)。在 Claude Code agent mode SDK 环境里跑不动,因为该环境的 CLI 未登录。

## 打包发布单个 skill

```bash
python -m scripts.package_skill prd-writer/ <output-dir>
```

打包器自动排除 `evals/`、`__pycache__`、`.pyc`。产物是 zip 格式的 `.skill` 文件。

## 几个不要踩的坑

- **Windows 跑 Python 默认 GBK 编码,读含中文 JSON / 评分会炸** —— 跑任何脚本都加 `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` 环境变量
- **subagent 的 timing 只在它返回的 notification 里能拿到一次** —— 拿到就立刻写 `timing.json`,不要批量延后
- **PRD 文件里 `本功能**不**包含` 的加粗 `**` 会断 substring 匹配** —— grader 用 `strip_markdown_emphasis()` 已处理,你写新断言时也用这个 helper
- **不要在 SKILL.md 的 YAML description 里塞过长说明** —— description 越精确触发越准;200-300 字符为宜

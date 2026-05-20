# skills

## 已有 skill

| Skill | 说明 |
|-------|------|
| [prd-writer](prd-writer/) | 通过对话生成或迭代产品需求文档(PRD),主动追问、冲突检测、Mermaid 图表 |

## 仓库结构约定

每个 skill 的目录长这样:

```
<skill-name>/
├── SKILL.md              # YAML frontmatter (name + description) + skill 本体指令
└── evals/                # skill 私有的评估资源(打包 .skill 时被自动排除)
    ├── evals.json        # 功能场景的测试 prompts
    ├── trigger-eval-*.json   # description 触发判断的测试 queries(可选)
    ├── fixtures/         # 测试用的输入数据
    └── grader.py         # 自定义评分器(可选,断言用)
```

**自包含原则**: skill 本身能跑的代码、resources 全在 `<skill-name>/`(`SKILL.md` 同级或子目录)。测试相关的东西放 `<skill-name>/evals/`,这个目录的打包器约定会排除掉,不会进 `.skill` 文件。

**workspace/ 目录**(被 .gitignore):跑评估时各轮迭代产物 (`workspace/<skill-name>/iteration-N/`) 都堆这里,不进 git。

## 安装一个 skill

任选一种:

**方法 1: 目录直接复制**
```bash
cp -r prd-writer ~/.claude/skills/
```

**方法 2: 打包成 .skill 文件**
```bash
python -m scripts.package_skill prd-writer/ <output-dir>
# (需要 skill-creator 工具链)
# 产物 prd-writer.skill 双击安装,或解压到 ~/.claude/skills/
```

## 改 skill 时的工作流

详见 [CLAUDE.md](CLAUDE.md)。简化版:**snapshot baseline → 编辑 SKILL.md → 起 subagent 对每个 eval 跑 new/old 两份 → 用 evals/grader.py 打分 → 对比看回归**。

## 新增一个 skill

```bash
mkdir my-new-skill
# 用 skill-creator 起 SKILL.md,或者参考 prd-writer/SKILL.md 的结构
```

约定:
- 文件夹名 = skill name (与 YAML frontmatter 里的 `name` 一致)
- 测试和 grader 放 `my-new-skill/evals/`
- 顶部表格更新一行

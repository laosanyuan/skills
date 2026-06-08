# skills

我的 Claude Code skill 合集。每个 skill 是一个独立的子目录,自包含,直接 `cp -r <skill-name> ~/.claude/skills/` 就能装。

## 已有 skill

| Skill | 说明 |
|-------|------|
| [prd-writer](prd-writer/) | 通过对话生成或迭代产品需求文档(PRD),主动追问、冲突检测、Mermaid 图表 |
| [tech-design-writer](tech-design-writer/) | 把 PRD 翻译成中等深度的技术设计文档(架构图 + 技术栈 + 数据模型 + 接口清单 + 模块拆分 + 风险点),必须先读 PRD,六维度没问清楚不动笔 |
| [code-refactor](code-refactor/) | 在保持行为不变前提下改进代码结构 / 可读性 / 效率,默认先列"重构清单 + 风险评估"等用户挑要做哪几条,再小步实施 + 每步验证 |
| [format-csharp](format-csharp/) | C# 代码风格统一:dotnet format 机械流(空白/缩进/using)+ 合规审计语义流(命名 / 成员归类 / `<summary>` / 写法禁令),按 🟢🟡🔴 破坏性分批,每批 diff 预览 + 用户点头才动。**只改 surface,不改 structure**(真重构转 code-refactor)。自带 `references/`(规范文档)+ `scripts/`(find-csproj / compliance-grep / class-layout-check 等);依赖 Python 3.10+、.NET SDK 6.0+、git |

## 仓库结构

```
.
├── README.md
├── CLAUDE.md                       # 给 Claude Code session 的开发指引
├── prd-writer/
│   └── SKILL.md                    # YAML frontmatter + 指令本体
├── tech-design-writer/
│   └── SKILL.md
├── code-refactor/
│   └── SKILL.md
└── format-csharp/
    ├── SKILL.md
    ├── references/                 # 规范文档:compliance-check.md / 代码规范.md
    └── scripts/                    # 辅助脚本:find-csproj.py / compliance-grep.py / class-layout-check.py / summarize-format-report.py
```

每个 skill 一个文件夹,**至少**有 `SKILL.md`(YAML frontmatter + 指令本体);复杂 skill 还可以带 `references/`(参考文档)和 `scripts/`(辅助脚本)子目录 —— 这是 Claude Code 的标准 skill 结构。

## 安装

**方法 1: 目录直接复制**

```bash
# macOS / Linux
cp -r prd-writer ~/.claude/skills/

# Windows
xcopy prd-writer C:\Users\<you>\.claude\skills\prd-writer\ /E /I
```

重启 Claude Code 即可触发。

**方法 2: 打包成 .skill 文件**

```bash
python -m scripts.package_skill prd-writer/ <output-dir>
# (需要 skill-creator 工具链)
# 产物 prd-writer.skill 双击安装,或解压到 ~/.claude/skills/
```

## 怎么用

随便起一个 Claude Code 会话,说类似的话就会触发对应 skill:

**prd-writer**:
- "帮我整理一下学生上课签到的需求"
- "在 PRD-shop-app.md 上加一个商品收藏功能"
- "F003 改成必须实名认证才能发布"

**tech-design-writer**:
- "帮我把这个 PRD 做个技术方案"
- "F004 怎么实现"
- "拆一下开发任务,我们 2 人 6 周做 MVP"

**code-refactor**:
- "帮我重构一下 UserService 这个文件"
- "这段代码太乱,拆一下"
- "扫一下 src/payment/ 下的代码异味"
- "把 calcPrice 里的 O(n²) 优化掉"

**format-csharp**:
- "格式化下这个 .cs / csproj"
- "按代码规范跑一遍 / 按规范审计一下"
- "把 Cmd 后缀都改成 Command,私有字段补 `_`"
- "补一下缺失的 `<summary>` 注释"
- "重排一下类内成员顺序"

## 新增 skill

```bash
mkdir my-new-skill
# 写 my-new-skill/SKILL.md(参考现有的 SKILL.md 结构)
# 在上面表格补一行
```

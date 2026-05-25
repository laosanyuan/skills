# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个仓库是什么

我的 Claude Code skill 合集。每个子目录是**一个独立的 skill**,自包含,目前有:

- [prd-writer/](prd-writer/) — 通过对话写 PRD
- [tech-design-writer/](tech-design-writer/) — 把 PRD 翻成技术设计

每个 skill 目录里**只有一个文件**:`SKILL.md`(YAML frontmatter + 指令)。这是唯一需要编辑的产品文件。

## 改某个 skill 时

直接编辑 `<skill-name>/SKILL.md`。结构上要注意:

- **YAML frontmatter 的 `description`** 决定 skill **何时被 Claude 自动调用**。改 description 影响触发判断 —— 措辞要既能"捕获"该触发的场景,又能"放过"近似但不该触发的(详见 SKILL.md 里描述自己怎么写的指引,或参考已有 skill 的 description)
- **正文** 决定被触发后**怎么做事**
- 推荐 SKILL.md 控制在 500 行内,超过模型可能跳读

## 新增 skill

```
mkdir my-new-skill
# 写 my-new-skill/SKILL.md
# 在 README.md 顶部表格补一行
```

文件夹名 = skill name(与 YAML frontmatter 里 `name` 字段一致)。

## 安装到 Claude Code

```
# macOS / Linux
cp -r <skill-name> ~/.claude/skills/

# Windows
xcopy <skill-name> C:\Users\<you>\.claude\skills\<skill-name>\ /E /I
```

重启 Claude Code,会话里说出 description 描述的触发短语就会自动调用。

## 这个仓库不包含什么

为了精简上传内容,**测试 / 评估 / fixture / grader 等开发期资源没纳入仓库**。如果以后需要做迭代验证,可以参考 [Anthropic skill-creator](https://github.com/anthropics/skills) 的 evals 工作流自己搭。

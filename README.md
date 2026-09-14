# skills

四个可独立安装的个人技能，支持从当前技能目录定位自己的参考文件和脚本，不依赖相邻技能包才能工作。

## 技能

| Skill | 功能 |
|---|---|
| [prd-writer](prd-writer/SKILL.md) | 创建或迭代 PRD，补齐验收、依赖与稳定编号；附结构检查和可选基线差异复核 |
| [tech-design-writer](tech-design-writer/SKILL.md) | 从需求生成技术设计；附草稿结构、编号与显式需求引用检查，不代替语义评审 |
| [code-refactor](code-refactor/SKILL.md) | 行为保持的重构、性能优化和只读异味检查；附行为测试有效性、反证与验证声明核对 |
| [format-csharp](format-csharp/SKILL.md) | C# 格式、命名、布局和注释整理；附可选 Roslyn 注释检查、文档 URL 保留候选检查与验证执行器 |

共同原则：明确的局部修改直接完成；开放的大改先讨论取舍；只读请求不修改；保留用户已有工作。description 保持一两句，详细流程写在正文。

## 包结构

```text
code-refactor/
  SKILL.md
  references/behavior-verification.md
prd-writer/
  SKILL.md
  scripts/check-prd.py
tech-design-writer/
  SKILL.md
  references/文档结构.md
  references/可靠性与安全.md
  references/实施规划.md
  scripts/check-design.py
format-csharp/
  SKILL.md
  references/代码规范.md
  references/compliance-check.md
  references/verification-runner.md
  references/comment-syntax.md
  scripts/scan_scope.py
  scripts/find-csproj.py
  scripts/compliance-grep.py
  scripts/class-layout-check.py
  scripts/summarize-format-report.py
  scripts/check-doc-preservation.py
  scripts/run-verification.py
  scripts/comment-syntax.py
  scripts/comment-syntax/Program.cs
  scripts/comment-syntax/CommentSyntax.csproj
```

辅助脚本使用 Python 3.10+ 标准库。C# 机械格式化另需与项目兼容的 .NET SDK/MSBuild 环境；没有工具时可进行限定范围的人工检查，并明确验证限制。检查器不代替业务、C# 语义或技术设计的人工判断。

可选 Roslyn 注释检查需要已安装稳定版 .NET SDK 8+，直接使用 SDK 自带程序集，在显式产物目录离线构建自带工具，不执行受审项目。它提高真实注释/XML 标签定位精度但有构建开销，仅覆盖两条注释规则；保留原正则路径，具体盲点和失败状态见包内参考。

`format-csharp` 携带自包含的验证执行器及说明；`code-refactor` 沿用项目验证命令，并在自建测试时核对装置有效性。执行器不提供系统沙箱，也不扩大命令、网络或写入授权；退出成功不证明测试覆盖充分。

## 安装

复制整个技能目录，不仅是 `SKILL.md`。更新已有版本前备份并核对差异；不要将本仓库根目录整体覆盖到用户配置目录。

本机当前技能目录：

- Codex：`C:\Users\yhong\.codex\skills`（其他环境按实际 `CODEX_HOME` / 配置确定）
- Claude：`C:\Users\yhong\.claude\skills`

Windows 单技能示例，先替换成实际目标目录：

```powershell
$skillSource = 'E:\skills\format-csharp'
$skillDestination = 'C:\Users\yhong\.codex\skills\format-csharp'
New-Item -ItemType Directory -Path $skillDestination -Force | Out-Null
Copy-Item -Path "$skillSource\*" -Destination $skillDestination -Recurse -Force
```

其他技能同理。复制后核对文件完整性；若当前客户端会话仍显示旧的发现信息，开启新会话或按客户端提示刷新。脚本命令中的 `<skill-root>` 需替换成当前实际加载的技能目录。

## 使用示例

- “给这个记录工具写一份 PRD”“F003 改成必须二次确认”
- “按这份需求做技术方案，选型由你推荐”“两人六周怎么拆任务”
- “把这个内部函数改名并同步引用”“只扫描这里的代码异味，不改”
- “只格式化这个 C# 文件的空白”“按规范整理这些类的注释”

C# 注释默认要求：summary 简短描述职责，开始/结束标签各占一行，注释结尾省略句号；实现细节只在必要的代码逻辑附近说明。

## 维护

维护约定见 [CLAUDE.md](CLAUDE.md)。测试和评估资源放临时目录，不进入安装包；新增脚本须跑行为验证。仓库不提供打包模块，不假设存在 `python -m scripts.package_skill` 或双击安装流程。

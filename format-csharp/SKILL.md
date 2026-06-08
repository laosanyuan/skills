---
name: format-csharp
description: 保持 C# 代码可读性 + 风格统一 —— **不改运行行为,但改"代码长什么样"**:空白 / 缩进 / using 排序(dotnet format 机械流),加上命名(`UseCmd` → `UseCommand`、私有字段加 `_`、自定义 Attribute/Exception 后缀、英文标识符)、类内成员归类(字段 → 属性 → 构造 → 方法)、`#region` 分组、补 `<summary>` 注释、`enum.ToString()` → `[Description]+.GetDescription()`、匿名委托 → 具名方法、补 `if/for/while` 大括号、`string +=` → StringBuilder、浮点 `==` 加 epsilon 等(合规审计 + 按破坏性分批实施 语义流)。**两条腿走路 —— 用户讲 "format/格式化" 默认两个都跑**。**与 code-refactor 的边界:format-csharp 改 surface(代码长什么样),code-refactor 改 structure(代码怎么组织)** —— 改名/重排/补注释/换语法糖是 surface,提取方法/拆大类/优化算法是 structure。**严格护栏**:audit 完按 🟢 低 / 🟡 中 / 🔴 高 破坏性分批,每批 diff 预览 + 用户点头才动,改完跑 `dotnet build` 自检,失败立刻 revert 该批。**典型触发场景**:被 PR reviewer 批评、CI 因 `--verify-no-changes` 失败、新人没按规范提交、想统一一遍命名 / 加注释 / 重排成员、merge 后大量空白扰动。**具体用户原话**:"格式化下这个 csproj"、"整理一下这个 .cs"、"修一下缩进"、"按代码规范跑一遍"、"按规范审计一下"、"统一一下命名"、"把 Cmd 后缀都改成 Command"、"补一下缺失的 summary"、"重排一下类内成员顺序"、"按规范清理一下这个文件"、"format my C# project"、"clean up the C# code style"、"apply our coding conventions"。**不适用**:写新的 C# 代码 / 修 bug / 真重构(提取方法、拆大类、改算法、合并重复逻辑) → 转 code-refactor / 翻译 C# 到 F# 或 VB / 改 .editorconfig 或规范文档本身 / 处理非 .cs 文件(Razor / XAML / JSON / 配置)/ 配置 IDE 或 CI / 单纯解释 flag。
---

# Format C# Code

**保持 C# 代码可读性 + 风格统一 —— 不改运行行为,只改"代码长什么样"。** 用户说"格式化/format/按规范跑一遍"通常指这一坨事,包括:空白 / 缩进 / using 排序(机械)、命名 / 类内归类 / 注释 / 写法禁令(语义)。两条腿走路:

**阶段 A. 机械流(`dotnet format`)** —— 空白、缩进、using 排序、大括号位置。Section 1-5 走这条。
**阶段 B. 语义流(合规审计 + 按破坏性分批实施)** —— 命名、类内归类、`<summary>` 注释、写法禁令(`Cmd`→`Command`、`_` 前缀、`enum.ToString()` 等)。Section 6-8 走这条。

**默认两条腿都跑**。除非用户明确说"只修空白"(只跑 A)、或"只审计先别动"(只跑 B,审计不实施)、或"只改命名"(只跑 B 但跳过 dotnet format)。

**环境前提**:
- **Python 3.10+**(脚本用了 `list[X]` / `X | None` 等 PEP 604 类型注解)
- **.NET SDK 6.0+**(`dotnet format` 是 .NET Core/5+ 工具,**.NET Framework 老项目**(VS2017 前的 `csproj` 老格式)用不了 dotnet format,要用 ReSharper / VS 内置格式化器,这种项目这个 skill 只能跑 Section 6 audit,跑不了阶段 A)
- **git VCS**(Section 7 实施流程依赖 `git status` / `git checkout` / `git diff`)。SVN/Mercurial 项目只能跑 audit,Section 7 fix 流程不支持

## 工作流概览

```
阶段 A: dotnet format 机械流(Section 1-5)
   1. 定位目标(.cs / .csproj / .sln)
   2. dry-run → 列改动 → 用户点头
   3. dotnet format 实施
   4. git diff --stat 汇总
   5. --verify-no-changes 自检

阶段 B: 合规审计 + 实施 语义流(Section 6-8)
   6. compliance-grep.py 抓违规 → 真违规清单
   7. 按 🟢 低 / 🟡 中 / 🔴 高 破坏性分批 → diff 预览 → 用户挑批 → 实施 → dotnet build 验证(失败 revert)
   8. 终极汇总(A + B 改了什么)
```

**section 2(dry-run)、section 7(每批先预览)绝对不能跳到直接动手** —— 改文件出问题没退路,**预览+用户点头是兜底**。

## 0. 先判定:是不是该由 format-csharp 处理 vs 转 code-refactor

**format-csharp 处理 "改 surface" —— 代码长什么样变了,但代码"做什么"没变**:
- ✅ 改名(`UseCmd`→`UseCommand`、`isVisible`→`_isVisible`、`AuthorAttr`→`AuthorAttribute`、中文标识符→英文)
- ✅ 改类内顺序(字段→属性→构造→方法)、加/去 `#region`
- ✅ 补 / 修 `<summary>` 注释、加模块头
- ✅ 改语法糖(`enum.ToString()` → `[Description]+.GetDescription()`、匿名 delegate → 具名方法、`if (...) x;` → `if (...) { x; }`、`string +=` → StringBuilder、`double ==` → epsilon 比较)
- ✅ 空白 / 缩进 / using 排序 / 大括号位置(dotnet format 那部分)

**code-refactor 处理 "改 structure" —— 代码组织变了**:
- ❌ "把 800 行的 `ProcessPayment` 拆成 3 个方法" → code-refactor
- ❌ "提取一个 `Validator` 类出来" → code-refactor
- ❌ "把 O(n²) 优化成 O(n)" → code-refactor
- ❌ "消除这段重复逻辑" → code-refactor
- ❌ "把这个 if/else 链改成策略模式" → code-refactor

**两边都不是的**:
- ❌ 写新代码 / 修 bug / 翻译 C# 到别的语言 / 格式化非 .cs(Razor/XAML/JSON)/ 改 .editorconfig 或规范文档本身 → 不是 format-csharp 也不是 code-refactor

**模糊情况**: 比如"把命名空间和文件夹对齐"涉及移动文件 + 改 `namespace` 声明 —— 这是 surface,format-csharp 干。"把这个 class 拆成 partial 多文件"涉及改组织结构 —— 这是 structure,转 code-refactor。改名的细分(规范驱动 vs ad-hoc)见 frontmatter 描述。

不确定就先反问一句,别上来就动手。

## 1. 定位格式化目标

`dotnet format` 接受三种粒度的输入,先搞清楚用户给的是哪种:

| 用户给的 | 范围 | 命令形态 |
|---|---|---|
| `Foo.cs`(单个 .cs 文件) | 仅这个文件 | `dotnet format <containing-csproj> --include <relative-path>` |
| 一组 .cs 文件 | 这些文件 | `dotnet format <containing-csproj> --include "a.cs b.cs c.cs"`(空格分隔) |
| `MyApp.csproj` | 整个项目 | `dotnet format MyApp.csproj` |
| `MyApp.sln` | 整个解决方案 | `dotnet format MyApp.sln` |
| 只说"格式化 C# 代码"没指文件 | 当前目录 | 先列当前目录的 .sln/.csproj(PowerShell: `Get-ChildItem *.sln,*.csproj`;bash: `ls *.sln *.csproj 2>/dev/null`),**找到了就用,没找到就反问** |

**`--include` 的路径要传 csproj 的相对路径**(绝对路径有时也行,但相对路径跨 SDK 版本最稳):用 `[System.IO.Path]::GetRelativePath($csprojDir, $csAbsPath)` 算。

**关键陷阱:`dotnet format` 不能直接吃 .cs 文件** —— 它需要项目上下文(.editorconfig、analyzers、references)。单文件场景必须先找到所属 .csproj。

**用 skill 自带脚本搞定**:
```bash
python ~/.claude/skills/format-csharp/scripts/find-csproj.py path/to/Foo.cs
# 返回最近 .csproj 全路径(向上爬目录);找不到 exit 2
```

找不到的话告诉用户:这个 .cs 文件不属于任何 csproj,`dotnet format` 没法处理,问要不要他指定一个 csproj。

## 2. Dry-run:先看会改哪些文件

正式格式化之前,**先用 `--verify-no-changes --report <dir>` 跑一遍**,它会:
- 不修改任何文件
- 把所有"需要格式化"的位置写到 `<dir>/format-report.json`(结构化 JSON,易解析)
- 退出码非 0 表示有需要格式化的地方

```powershell
# Windows / PowerShell —— report 落到 TEMP,避免污染项目目录
dotnet format <target> --verify-no-changes --report $env:TEMP --verbosity quiet
# 生成 $env:TEMP\format-report.json,exit code 0=已干净,非0=有要改的
```

```bash
# bash
dotnet format <target> --verify-no-changes --report /tmp --verbosity quiet
# 生成 /tmp/format-report.json
```

**`--report` 落到 TEMP 而不是 `.`** —— 否则 `format-report.json` 会落到当前目录(通常是项目根),git status 会冒出一个 untracked 文件,有洁癖的项目会嫌恶心。落到 TEMP 既不污染,又方便后续脚本读。

**为什么用 `--report`** — 没它的话 `--verify-no-changes` 把每个问题当 error 打一行 stderr,大项目淹没终端;`--report` 输出干净 JSON。**别用 stdout/stderr 做判断,看 exit code + JSON 即可**。

**JSON 注意点**:同一个文件会重复出现(whitespace/style/analyzers 三个子阶段各输出一份)。**别手工解析,直接跑脚本**:

```bash
python ~/.claude/skills/format-csharp/scripts/summarize-format-report.py
# 自动 dedup by FilePath + 聚合 DiagnosticId(WHITESPACE/ENDOFLINE/IMPORTS/IDE0XXX 等)
# 加 --json 输出结构化 JSON 供后续编程处理
```

**展示给用户的格式建议:**
```
🔍 dry-run 完成,以下 N 个文件需要格式化:

  src/Program.cs       IMPORTS(using 排序)、WHITESPACE × 8、ENDOFLINE × 6
  src/Helper.cs        WHITESPACE × 12、ENDOFLINE × 11
  ...

要全部格式化吗?还是要排除某些文件 / 某种诊断类别?
```

**用户想排除某些文件**,加 `--exclude`(空格分隔的相对路径,支持 glob):
```bash
dotnet format <target> --exclude "src/Generated/** src/Vendor/**"
```

**用户只想修某类问题**(比如只修缩进不动 using 排序),用子命令缩范围(见第 3 节)。

## 3. 实施:正式格式化

确认范围后,去掉 `--verify-no-changes` 跑一遍。**推荐用 `--verbosity quiet`,完全静默,只看 exit code 0/非 0**:

```bash
dotnet format <target> --verbosity quiet
```

如果出问题需要排查,再切到 `--verbosity diagnostic` 看每条规则的具体动作。**别一上来就 diagnostic**,99% 情况下你不需要那些信息。

常用 flag(按需加):
- `--verbosity quiet|minimal|normal|detailed|diagnostic` —— 默认 minimal,实际格式化推荐 quiet
- `--severity warn` —— 只修 warn 及以上严重度的问题(默认是 warn,可以放宽到 `info` 或收紧到 `error`,**不建议设 error**,会让本来只是 warn 的格式问题导致命令非 0 退出)
- `--no-restore` —— 项目已经 restore 过了,跳过那一步(快很多)
- `--include <file/dir>` —— 限定范围(单文件场景必备,用项目相对路径)
- `--exclude <file/dir>` —— 反向排除
- `--include-generated` —— 默认 false。**不要随便加**,生成代码格式化了下次 build 又会被覆盖

`dotnet format` 内部有三个子命令(`whitespace` / `style` / `analyzers`),默认三个都跑。**用户明确说"只修空白别动 using"**之类的子集请求,详见 [`references/compliance-check.md`](./references/compliance-check.md) 附录"dotnet format 子命令"。

## 4. 汇总:告诉用户改了什么

格式化完跑收尾汇总。**两种来源,选一个**:

**(a) 仓库是 git,优先用 git(最直观):**
```bash
git diff --stat                    # 文件级统计 +N -M
git diff --name-only               # 只列文件名
git diff --shortstat               # 一行总体 +/- 行数
```

把改了的文件列出来,**每个文件配一句话说改了什么**。读 diff 自己判断,典型类别:
- "tab 缩进 → 4 空格"
- "末尾空白清理"
- "using 重新排序(System.* 优先)"
- "类/方法的 `{` 换到下一行"
- "操作符两侧补空格(`a=b` → `a = b`)"
- "行尾 CRLF / LF 统一"

**(b) 没 git,复用 dry-run 时的 `format-report.json`**(第 2 节已经生成过):按 `FilePath` 去重,统计每个文件的 `DiagnosticId` 分布,直接报。

汇总格式建议:
```
✅ 格式化完成,改了 N 个文件:

src/Program.cs       using 重排 + 缩进 tab → 4 空格 + 操作符空格
src/Helper.cs        参数列表空格 + 方法体重新缩进
src/Models/User.cs   命名空间与路径对齐

跑的命令: dotnet format Demo.csproj --verbosity quiet
没改 .editorconfig、.csproj 或任何 .cs 之外的文件。
```

**关键:不要把 dotnet format 自己的 stdout 原样贴给用户** —— 那里都是 "已将代码文件 X 格式化" / "Formatted code file 'X'"(取决于系统 locale)级别的状态信息,既冗余又不直观。git diff 或 JSON report 才是干净来源。

## 5. 收尾自检

最后跑一次 `--verify-no-changes`,**应该退出码为 0**,表示已经干净:

```bash
dotnet format <target> --verify-no-changes
echo "exit=$?"
```

非 0 退出码 = `dotnet format` 自己修不了的 analyzer 警告:**不要在这里改代码**。命名/`<summary>`/写法禁令类警告(IDE1006/SA1600/CA1707 等)进 Section 6 audit 处理;死代码/复杂度类(IDE0051/CA1822 等)转 code-refactor;真改不掉的把告警贴给用户。**如果只跑了阶段 A 没跑 B**,告诉用户"剩下这堆告警按规范跑一遍能处理大部分"。

## 6. 代码规范合规审计(语义流第一步 — 找违规)

`dotnet format` 已经把 `.editorconfig` 能描述的部分修了,但**风格指南通常有一堆 dotnet format 处理不了的语义规则** —— `Cmd → Command` 缩写禁令、中文标识符禁令、私有字段 `_` 前缀、自定义 Attribute/Exception 后缀、控件名前缀、`<summary>` 注释覆盖率、`enum.ToString()`/匿名委托/浮点 `==` 等写法禁令、类内成员顺序、反义词组配对。

**规则来源 — 内嵌在 skill 里,跨项目通用:**

[`references/代码规范.md`](./references/代码规范.md) 是合规审计的唯一权威来源。**不会去搜用户项目里的同名文件**。要改规则就直接编辑这个文件,改完立即生效。

**步骤(从拿到目标到 audit 报告):**

```
(1) Read references/代码规范.md          ← 规则吸进来
(2) 跑 scripts/compliance-grep.py       ← 一次抓所有候选 + 已应用确定性过滤
(3) Read references/compliance-check.md  ← 学每条规则的 filter_hint + 如何 fix
(4) 对 enum_tostring_suspect 这类需要语义判断的,打开命中文件读上下文
(5) 出 audit 报告(下面格式)
```

**两个脚本并行跑**(各管一摊):
```bash
# 1. 11 条 grep 规则(命名 / 中文标识符 / 写法禁令 / summary 单行 等)
python ~/.claude/skills/format-csharp/scripts/compliance-grep.py --scope path/to/src
# 输出 $TEMP\compliance-audit.json,violations 已过滤(只 enum_tostring 还需 LLM 读上下文)

# 2. 4 条 class body 解析规则(成员顺序 / region 缺失 / region 间空行 / 成员间空行)
python ~/.claude/skills/format-csharp/scripts/class-layout-check.py --scope path/to/src
# 输出 $TEMP\class-layout-audit.json,findings 含 file:line:rule:message
```

**audit 报告必须按破坏性分级(给 Section 7 实施用):**

判定流程:对每条违规,**先用 Grep 工具扫 identifier 在整个 solution 的出现位置**(`.cs` + `.xaml` + `.razor` + `.cshtml` + `.json` + `.resx` 等 —— 脚本只扫 `.cs`,字符串引用必须 LLM 手工查),再按下表分级:

| 档 | 判据 | 典型规则 |
|---|---|---|
| 🟢 低 | 改动只影响声明本身 / 同 class 内部 / 单 method 内 | 私有字段加 `_`、`if/for/while` 加 `{ }`、加 `<summary>`、`<summary>` 单行→多行、浮点 `==` 改 epsilon、`string +=` 改 StringBuilder、类内重排/补 region |
| 🟡 中 | 同工程内多处源码引用(可 Grep 验证),含 XAML/.razor binding | ICommand `Cmd` → `Command`(必查 XAML)、自定义 Attribute/Exception 改名、`enum.ToString()` → `[Description]`、匿名 delegate → 具名方法 |
| 🔴 高 | 外部 API / 字符串-based 引用 / 跨工程 / 语义判断 | public class/interface/enum 改名、中文标识符→英文(需用户拍板目标命名)、测试方法改名 |

**典型 grep 调用**(中/高破坏性必跑,没跑直接判级 = 蒙数据 = 误判):
```
Grep("XxxCmd"   in *.xaml *.axaml *.razor *.cshtml)   # ICommand binding
Grep("[XxxAttr" in *.cs)                              # Attribute 用法
Grep("中文名"   in *.cs *.xaml *.json *.resx)         # 中文 identifier 全引用
```

**audit 报告格式** —— 完整 mock 范例在 [`references/compliance-check.md`](./references/compliance-check.md) Section 5。骨架:总览 (🟢/🟡/🔴/ℹ️ 计数) → 按破坏性分级的详单(每条违规给 file:line + 改法 + 引用扫描结果) → 规则冲突单独列。

**关键原则:**
- **Grep 命中要开文件确认** —— 脚本已用确定性 filter 砍掉绝大多数误判,但 `enum.ToString()` 这种**必须 LLM 读上下文判断左侧类型**,不能盲信。
- **规则冲突单独列**,不混在违规里 —— 这是用户决策项,不是 fix 项
- **不要修改 `references/代码规范.md`** —— 即使发现规范有自相矛盾,在报告"规则冲突"那一节指出,不替用户改

## 7. 按破坏性分批实施(语义流第二步 — 改)

audit 完直接进 Section 7。**核心节奏:每批先 diff 预览 → 用户挑批 → 实施 → `dotnet build` 验证 → 失败 revert 该批**。

### 7.1 前置条件 + 标准操作流

**进 Section 7 前**:跑 `git status --porcelain`,**非空就告诉用户先 commit 或 stash**(`git checkout --` revert 会污染用户原有 uncommitted 改动)。**不要替用户 stash/commit**。

**每批走这一套**:
```
1. LLM 用 Edit 工具按 audit 清单把这批改动一次性应用到 working tree
2. git diff --stat + git diff 给用户预览
3. 用户表态:
   - "全部 OK" → 进 4
   - "排除 ___"  → 必须搞清楚是 (a) 整条规则跳过 / (b) 某文件不改 / (c) 某条具体改动不改 —— **不确定就反问**:
     - (a) → `git checkout -- <本规则触动的所有文件>`,标记规则跳过
     - (b) → `git checkout -- src/Foo.cs`,保留其他,回 2 重新预览
     - (c) → Edit 撤该条改动,保留其他,回 2 重新预览
   - "全部撤回" → `git checkout -- <本批触动的所有文件>`,跳过该批
4. dotnet build —— 0=进 5,非 0=`git checkout -- <touched>` revert 该批,报错给用户
5. 跑测试(若项目有测试):**先反问用户测试怎么跑**(`dotnet test <sln>` / `dotnet test <Tests.csproj>` / `vstest.console.exe ...` / NUnit / 其他 runner —— 不要假设)。全过=进下一批,挂了=revert 该批,报错。**如果用户说"先跳过测试"或项目没测试,只 build 验证就行**
```

### 7.2 各批的默认动作

| 批次 | 默认做法 |
|---|---|
| 🟢 低 | **预先全部应用 + diff 预览 + 一键 OK** —— 100 个私有字段改名逐条问是浪费 |
| 🟡 中 | **按规则一组一组** —— 同一条规则一批应用 + diff(必须看到 XAML/.razor binding 也跟着改) + 用户 OK |
| 🔴 高 | **逐条来** —— 先 grep 影响文件清单、再预览、用户单独 OK。**中文→英文必须先让用户拍板目标命名** |
| ℹ️ 规则冲突 | 不实施。只在 audit 报告列,用户后续手动改规范或 .editorconfig |

### 7.3 改完之后 + per-rule 修复细则

各批之间不需要单独 commit —— 改动在 working tree 累积,出错 revert 只动当批文件。**Section 7 全部跑完也不替用户 commit**,让用户自己看 final state 决定怎么拆。

每条规则具体改法(grep 哪些引用、补哪些 using、何时反问用户) → 见 [`references/compliance-check.md`](./references/compliance-check.md) Section 7。关键点 highlight:
- **改名**:`Edit` 改声明 → grep 引用 → `Edit` 同步;XAML/.razor binding 同改
- **enum.ToString() → `.GetDescription()`**:**先 grep `GetDescription()` 已有否**,没有**反问用户**在哪建,不默认新建文件
- **加 `<summary>`**:写不出有信息量的标"建议手工补",**不凑数写废话**
- **类内布局**(顺序/region/间隔):跑 `class-layout-check.py` 拿 findings,**重排是文件内部的大改动**,用 Edit 重写整段类体 + dotnet build 验证(改动不跨文件,build 通过基本就 OK)。**audit findings 是必要不充分条件** —— audit 报的肯定要改,**但 audit 没报的不代表合规**。脚本基于正则启发式分类,可能漏算 `[DllImport] extern` 方法 / 多行 attribute decoration / nested 类内 lambda 等,导致某 group 实际有 N 个成员但 audit 只算到 N-1 个。**每个修类的 LLM 任务,改完 audit findings 后必须打开 JSON 里的 `class_layouts` 看该 class 的 group 全景**(每个非空 group 的 count / in_region / status),跟实际文件里成员数对一下,发现差异就是 parser miss,主动补 region —— 不要"audit 没标就跳过"

## 8. 全流程终极汇总

阶段 A + B 都跑完后,给用户一份合并报告。骨架:

- ✅ / ⚠️ 头部:总体状态
- 阶段 A 改了 N 个文件,主要修了什么
- 阶段 B 改了 M 个文件 × P 处违规,按 🟢/🟡/🔴 分类计数
- 跳过未改:列出原因(规则冲突 / 模块头默认 skip 等)
- dotnet build / dotnet test 结果
- 下一步:建议用户怎么 commit(拆 2-3 个 commit 方便 review)

完整 mock 范例见 [`references/compliance-check.md`](./references/compliance-check.md) Section 8。

**重要:不替用户 commit**。让他自己看完最终 working tree 决定怎么拆。

## 常见坑 + 故障排查

环境类:
- **dotnet 命令找不到** —— `dotnet --version` 先验一遍。Windows 装 .NET SDK,Linux 装 `dotnet-sdk-8.0/9.0/10.0` 等
- **没 .editorconfig** —— `dotnet format` 用 .NET 内置默认规则(4 空格、CRLF 跟系统),可能不合预期。用户没 .editorconfig 但说"按项目风格",先反问或建议 `dotnet new editorconfig`
- **`Unable to load Workspace`** —— restore 失败 / SDK 版本错。先 `dotnet restore`,看 global.json 的 SDK 版本

性能 / 范围类:
- **慢得离谱** —— 大项目第一次 restore + 加载 analyzer 是 5-30 秒。加 `--no-restore` / 限 `--include` 范围加速
- **改了不期望的文件**(生成代码、迁移)—— 加 `--exclude "Migrations/** Generated/**"`,或 .editorconfig 设 `generated_code = true`
- **`Could not find any project or solution`** —— 当前目录没 .csproj/.sln 且没显式传,显式传一个或 cd 过去
- **`MSB1009: Project file does not exist`** —— .csproj 路径写错,用绝对路径

行为 / locale 类:
- **PowerShell 下别用 `2>&1`** —— PS 5.1 把每个 stderr 行包成 `NativeCommandError`,`$LASTEXITCODE` 判断会乱。直接看 exit code + `--report` JSON
- **CRLF/LF 总被改回** —— .editorconfig 没设 `end_of_line`,或 .gitattributes 的 autocrlf 在干扰。在 .editorconfig 写死 `end_of_line = lf`
- **dotnet format 状态输出受 locale 影响** —— 中文 Windows 是"已将代码文件 X 格式化",英文是 `Formatted code file 'X'`。**别 grep 字符串判断**,看 exit code 或 JSON
- **`.cs` 不在任何 .csproj 里**(scratch 脚本)—— dotnet format 处理不了,告诉用户这个限制
- **项目编译不过** —— dotnet format 不要求 emit(Roslyn 分析够),但严重 syntax error 会让格式化结果不准,先让用户修

收尾类:
- **`--verify-no-changes` 永远非 0** —— 有 analyzer 规则修不掉(需要语义改)。走 Section 5 分级路由:命名/`<summary>`/写法禁令类进 Section 6 audit,死代码/复杂度类转 code-refactor。**别靠改 `--severity` 凑** —— 那是隐藏问题
- **`Hangs forever on "Loading workspace"`** —— 大 sln 第一次 + analyzer NuGet 下载,等;或缩范围到单 csproj

## 不该做的事

**阶段 A**:不要不 dry-run 直接改 / 不要静默加 `--include-generated` / 不要顺手改 .editorconfig / 不要把 `--severity` 提到 `error`。

**阶段 B**:不要按 audit 报告"擅自决定"中文 → 英文的目标命名(必须让用户拍板)/ 不要为了凑数写废话 `<summary>`(写不出有信息量的就标"建议手工补")/ 不要修改 `references/代码规范.md`(除非用户明说加规则)。

**通用边界**:
- **拆方法 / 优化算法 / 合并重复逻辑 / 提取类 → 转 code-refactor**。format-csharp 只改 surface,不改 structure。详见 Section 0。
- **改名**:规范驱动 → format-csharp;ad-hoc("我觉得名字不好") → code-refactor。

(本节其他护栏已在前面 section 强调过:dry-run 必跑、Section 7 前 git status 干净、每批 build 验证、不替用户 commit 等)

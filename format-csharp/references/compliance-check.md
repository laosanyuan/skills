# C# 合规审计 — 找违规 + 修违规(配套 SKILL.md Section 6/7/8)

本文档配套 SKILL.md **Section 6(audit 找违规)+ Section 7(按破坏性分批实施)+ Section 8(全流程终极汇总)** 的语义流。**默认两条腿走路时,这一套和 dotnet format 主流程都跑**。

`dotnet format` 把 `.editorconfig` + 内置 IDE/style/analyzer 规则覆盖的部分修了,但**风格指南里总有一堆 dotnet format 处理不了的语义规则**(后缀约定、命名禁令、注释完整性、写法禁令等)。这份文档:
- Section 1-4: 怎么把违规查出来(audit)
- Section 5: audit 报告格式(SKILL.md Section 7 实施的输入)
- Section 6: 审计阶段不该做的事
- Section 7: per-rule 修复细则(SKILL.md Section 7 实施细则展开)
- Section 8: 全流程终极汇总 mock 范例

---

## 1. 规则来源 — 嵌入在 skill 里的 `代码规范.md`

**规则的唯一权威来源**是同目录下的 [`代码规范.md`](./代码规范.md)(本 skill 自带的副本)。**不在用户的项目里搜风格指南文档** —— 项目里有没有 `代码规范.md` / `STYLE_GUIDE.md` 都不影响,skill 自己自带一份固定的规则集。

**为什么内嵌不靠路径搜:**
- 用户在不同项目跨切时,规则保持一致(不会因为某个项目没有规范文档就跳过审计)
- 规则版本可控(由 skill 维护,不会因为某个项目仓库里规范文档没更新就漏检)
- 不会和项目自带的非格式化文档(README/CHANGELOG/TODO 等)冲突

**先做的事:** 进入第 6 节时,**Read `代码规范.md` 一遍**,把规则吸收进来。后续每条违规要在报告里**引用 `代码规范.md` 的具体行号或章节**,用户才知道是哪条规则。

**规范不是这份 skill 的产品 —— 是用户组织的规则。** 如果用户说"我们规范不是这样"、"这条规则不适用了"、"加一条新的",指引用户**直接编辑这个文件**(`~/.claude/skills/format-csharp/references/代码规范.md`),改完 skill 立即生效。**不要为了某次会话临时调整规则**(失忆;下次又恢复了)。

---

## 2. dotnet format 能搞 vs 不能搞(分工)

```
✅ dotnet format 已经处理(主流程 1-5 节)
- 空白、缩进、行尾
- 操作符两侧空格、{ } 位置
- using 排序(IDE0065)
- file-scoped namespace、表达式 body 等 IDE 规则
- naming rule(如果 .editorconfig 里配了 dotnet_naming_rule.* —— 90% 项目没配)

❌ dotnet format 处理不了(本节做合规审计)
- 自定义后缀:Attribute / Exception / Tests / Command 必须以特定后缀结尾
- "Cmd 缩写禁用" 这种项目级字符串禁令
- "所有标识符必须用英文"
- 控件名前缀(lbl/txt/btn 等)命名约定
- 私有字段 _ 前缀(.editorconfig 可配但通常没配)
- 方法名动宾短语(语义)
- 模块头注释 / 公有 API 的 <summary> 注释覆盖率
- 禁止 enum.ToString() / 禁止匿名委托 / 禁止 if 不加 { } / 禁止浮点 == 等"写法禁令"
- 类内成员顺序(字段/属性/构造/方法)
- 反义词组配对(add/remove, open/close)
```

---

## 3. Grep-able 检查(机械,能精确定位)

**强烈推荐: 跑 skill 自带脚本一把抓所有候选**:

```bash
python ~/.claude/skills/format-csharp/scripts/compliance-grep.py --scope <目录|.csproj|.sln|.cs>
# 默认输出 $TEMP/compliance-audit.json
# 里面是 11 条规则的 violations(已应用确定性过滤)+ 每条的 filter_hint
```

JSON 结构:
```json
{
  "scope": "...",
  "scanned_files": 27,
  "rules": [
    {
      "key": "cmd_suffix",
      "name": "Cmd 后缀禁用",
      "rule_source": "良好习惯表: ...",
      "filter_hint": "无需过滤,全部是违规",
      "pattern": "\\b\\w+Cmd\\b\\s*(\\{|=>|;)",
      "candidate_count": 72,
      "candidates": [ { "file": "...", "line": 23, "text": "..." }, ... ]
    },
    ...
  ]
}
```

**LLM 拿到 JSON 后干啥:** 对每条规则 (1) 读 `filter_hint` 知道哪些是误判要丢、(2) 应用过滤、(3) 对剩下的"真违规"必要时开文件读上下文确认(尤其是 `enum.ToString()` 这种高误判规则)、(4) 写最终审计报告(按 Section 5 格式)。

**脚本里覆盖的 10 条规则**(模式和 filter_hint 的权威定义在 `scripts/compliance-grep.py` 的 `$rules` 数组里。要看具体某条规则的 regex 或 filter 文案,直接 Read 那个脚本):

| key | 名称 | 命中即违规? |
|---|---|---|
| `cmd_suffix` | Cmd 缩写禁用 (应改 Command) | ✅ 是 |
| `attribute_no_suffix` | 自定义 Attribute 缺 Attribute 后缀 | 需过滤 |
| `exception_no_suffix` | 自定义 Exception 缺 Exception 后缀 | 需过滤 |
| `interface_no_i_prefix` | 接口缺 `I` 前缀 | 需过滤 |
| `chinese_identifier` | 标识符含中文(字符串/注释里的中文允许,只查标识符位置) | ✅ 是 |
| `private_field_no_underscore` | 私有字段缺 `_` 前缀 | 需过滤 |
| `anonymous_delegate` | 匿名 delegate | ✅ 是 |
| `no_braces_on_control_flow` | if/for/while 单语句不加 `{ }` | 需过滤(误中 inline return/throw) |
| `float_equality` | 浮点 `==` / `!=` 直接比较 | 需过滤(== null 合法) |
| `enum_tostring_suspect` | `enum.ToString()` 嫌疑(误判极多) | **必须读上下文**判断左侧类型 |
| `summary_inline` | `<summary>` 标签和内容写在同一行 | ✅ 是 (规范要求标签独占一行,内容夹在中间) |

**Backing field 豁免**:`private int _no;` 这种属性的 backing field 如果放在 `[Properties]` region 内(紧贴它的属性),脚本会自动识别为 backing field,**不算 `[Private Fields]` 的成员、不破坏顺序、和属性之间不强制空行**。规则详见 `代码规范.md` "代码布局"章节。脚本**不主动检查** "`_no` 应该挪到 [Properties]" —— 在 `[Private Fields]` 里的 `_xxx` 字段位置是合法的,只是规范**推荐**把它放属性旁边。LLM 别擅自给用户报这种"未触发的建议"。

**类内布局规则用第二个脚本** `scripts/class-layout-check.py` 跑(需要 class body 解析,纯 grep 不够):

```bash
python ~/.claude/skills/format-csharp/scripts/class-layout-check.py --scope <目录|.csproj|.sln|.cs>
# 默认输出 $TEMP/class-layout-audit.json
# 检查 5 条子规则:
#   members_out_of_order        - 成员顺序错(非 私有字段→属性→事件→构造→公有方法→私有方法)
#   region_missing               - 某分组 2+ 成员但没 #region [GroupName] / #endregion 包裹
#   region_no_blank_between      - #endregion 和下一个 #region 之间缺空行
#   members_no_blank_line        - 同一分组内连续两个方法/属性/事件之间缺空行
#   summary_missing_on_public    - public 类/方法/属性/事件缺 /// <summary> 文档注释
```

**class-layout 的 6 个 region 名固定**(见 `代码规范.md`"代码布局"):`[Private Fields]` / `[Properties]` / `[Events]` / `[Constructors]` / `[Public Methods]` / `[Private Methods]`。

**两种免 region 的情况**(脚本已实现,LLM 不主动报):
- 单成员分组(1 个成员的组不需要 region 包裹)
- 全类只有一种非空分组(没有别的组要分隔,这一个 region 多余)

顺序在所有情况下都要保持。

**脚本局限**(LLM 抽查时心里有数):用正则状态机解析 class body,**不处理 nested class / partial class 的多文件合并 / 多行 attribute 修饰 / 多行 expression body**。规模大的项目跑出来的 findings 用 LLM 抽检几个确认 false positive 率可控,再批量改。

**两条 Grep 不好覆盖、要走 Section 4 reading-required 的写法禁令:**
- 字符串 `+=` 循环拼接(在 for/while body 内),需 multiline scan
- for 循环体内修改循环变量,只能读

**控件前缀对照表**(以 `代码规范.md` "控件名缩写示例" 为准,审计控件命名时需要):

```
lbl=Label  txt=TextBox  tbk=TextBlock  btn=Button  chk=CheckBox  lst=ListBox
cmb=ComboBox  dtp=DateTimePicker  llb=LinkLabel  lvw=ListView  nud=NumericUpDown
prg=ProgressBar  rdo=RadioButton  rtx=RichTextBox  tvw=TreeView  grp=GroupBox
pnl=Panel  spl=GridSplitter  tab=TabControl  spn=StackPanel  cmn=ContextMenu
mns=MenuStrip  ssr=StatusStrip  tsr=ToolStrip  wbs=WebBrowser  tip=ToolTip
dpn=DockPanel  ckl=CheckedListBox
```

> **如果脚本不可用** —— 罕见情况(Python 没装或被禁)。打开 `scripts/compliance-grep.py` 拿 `RULES` 数组里每条的 `pattern` 字段,用 Grep 工具手工逐条跑;然后照每条的 `filter_hint` 在 LLM 里过滤。流程一样,只是没有合并的 JSON 输出。
>
> **关于 lookaround**:ripgrep 默认编译不带 PCRE2,**不支持 `(?<!...)` / `(?!...)`** —— 这种模式会静默返 0 命中。脚本里所有规则都用宽 grep + filter 两步走避开这个坑;手工跑也要遵守。

---

## 4. Reading-required 检查(语义,Grep 抓不准)

下面这些必须打开文件读判断,LLM 直接看代码:

| 规则 | 规范出处 | 检查方式 |
|---|---|---|
| 方法名是不是动宾短语 | 良好习惯表(`方法的命名,一般将其命名为动宾短语,如 ShowDialog/CreateFile`) | 列项目所有 `public/internal` method 签名,过滤 `Is*/Has*/Can*/Should*`(谓语 OK)、`To*/From*`(转换 OK)、`Get*/Set*/Update*/Create*/Delete*` 这类常见动词 OK。**剩下纯名词命名的方法**(如 `User()`、`Order()`、`Customer()`)就是不合规 |
| ~~`<summary>` 注释覆盖率~~ | ~~方法注释规范 1+3~~ | **✅ 已脚本化** —— 见 `class-layout-check.py` 的 `summary_missing_on_public` 子规则(自动检查 public 类/方法/属性/事件,跳过 attribute decoration) |
| 模块头注释(功能/作者/日期) | 模块头部注释规范 | 读 .cs 文件头 15 行,看有没有规范要求的 `/// <summary>` 模板字段(功能/完成日期/作者)。**⚠️ 默认跳过这条** —— 这是 .NET Framework 时代的遗留规范,现代 C# 项目(尤其用 file-scoped namespace 之后)几乎从不写模块头注释,跑全量审计大概率每个文件都报、刷屏。**只在用户明确说"按模块头规范查一下"时才跑**,平时静默 |
| 类内成员顺序 | 代码布局 1(`类内部的代码布局顺序:数据成员、属性、构造函数(、事件)、方法`) | 读类体,按出现顺序提取每个成员的 kind(field / property / ctor / event / method),看是否符合顺序 |
| 局部变量名意义 | 良好习惯表(`局部变量的名称要有意义。不要用 x,y,z 等等(除用于 For 循环变量中可使用 i,j,k,l,m,n)`) | 读 `var x = ` / `int tmp = ` / `string s = ` 等声明,判断是不是 `x/y/z/tmp/s/a/b/c/data/foo/bar` 这类无意义名;`for` 循环里的 `i/j/k/l/m/n` 允许 |
| 反义词组配对 | 良好习惯表(`用正确的反义词组命名具有互斥意义的变量或相反动作的函数等`) + 反义词组示例 | **只查"明确成对的动词":`Start*/Stop*`、`Open*/Close*`、`Begin*/End*`、`Lock*/Unlock*`、`Acquire*/Release*`、`Subscribe*/Unsubscribe*`、`Connect*/Disconnect*`、`Show*/Hide*`、`Enable*/Disable*`、`Mount*/Unmount*`**。出现一边就找另一边。**不查 `Add*/Remove*` `Get*/Set*` `Create*/Delete*` 这种** —— 这些动词常单独存在(`AddItem()` 不必有 `RemoveItem()`,会刷大量假违规) |
| #region 分组使用 | 代码布局 + 良好习惯表(`把相似的内容放在一起...适当地使用 #region…#endregion`) | 读类体,看有没有 #region 分类(数据成员/属性/方法分块) |

---

## 5. audit 报告格式(给 SKILL.md Section 7 实施用)

audit 完不是终点,是**通向"按破坏性分批实施"的输入**。报告必须按 🟢/🟡/🔴 分级,因为 Section 7 就是据此挑批的。

**默认审计范围:**
- 跟在 `dotnet format` 后面跑(两条腿都走) → 默认审计**本次 format 改过的文件**(从 `format-report.json` 取 FilePath 列表),范围小、跑得快
- 独立跑 audit(用户说"只审计先别动" / "只做语义流") → 默认审计**用户指定的 sln/csproj/目录**,没指定就反问。**不要默认扫全项目** —— 大项目几千个 .cs 跑全量 grep 慢且报告淹没

**报告范例(Section 6 已经规定的格式,这里展开一条具体规则的写法):**

```
## 🔍 合规审计 — <scope>(用户给的目录 / .csproj / .sln)

📊 总览: 261 处违规分布在 27 个文件
  🟢 低破坏性: 177 处 (134 私有字段 + 43 单语句无大括号)
  🟡 中破坏性: 84 处 (72 Cmd → Command + 12 enum.ToString())
  🔴 高破坏性: 0 处 (本次无 public API / 中文标识符违规)
  ℹ️ 规则冲突: 0 处

### 🟢 低破坏性详单

#### 私有字段缺 `_` 前缀 (134 处, 规范:大小写表 + 良好习惯表)
- src/ViewModels/DownloaderViewModel.cs L33: `private bool isVisible = false;` → `_isVisible`
- src/ViewModels/DownloaderViewModel.cs L49: `private int themeType = 1;` → `_themeType`
- ...(132 条略,完整清单在 $env:TEMP\compliance-audit.json)

#### if/for/while 单语句缺 `{ }` (43 处, 规范:良好习惯表 + 表达式与语句 1)
- src/ViewModels/LoginViewModel.cs L132: `if (lang.Contains("en")) lang = "en";` → 包成 `{ }`
- ...

### 🟡 中破坏性详单

#### ICommand 属性 `Cmd` → `Command` (72 处, 规范:良好习惯表 — 禁止 Cmd 缩写)

  ⚠️ 引用扫描:在 .xaml 文件中找到 <X> 处 `{Binding XxxCmd}` 用法,改名时需同步
    (XAML grep 是审计阶段 LLM 手工跑的 —— compliance-grep.py 只扫 .cs)
    - src/Views/Login.xaml (<X1> 处)
    - src/Views/Main.xaml (<X2> 处)
    - ...

  详单(改名 + 同步 XAML):
  - src/ViewModels/LoginViewModel.cs L265: `SwitchUICmd` → `SwitchUICommand`
    XAML 同步: src/Views/Login.xaml L34, L48
  - ...

#### enum.ToString() 嫌疑 (12 处, 需 LLM 读上下文判断左侧是否枚举)
- src/Http/Client.cs L34: `httpRequest.Method = HttpMethod.Get.ToString();`
  - 上下文确认:`HttpMethod` 是 enum ✓
  - 改法:enum 上加 `[Description("GET")]`,改用 `.GetDescription()` 扩展方法
  - 影响:本文件 + 需在工程里有 `GetDescription()` 扩展方法 (没有就先问用户在哪建,不默认新建文件)
- ...

### 🔴 高破坏性详单
(本次无)

### ℹ️ 规则冲突
(本次无)

### ✅ 已通过 dotnet format 覆盖
- using 排序、缩进、空白、行尾、操作符空格(阶段 A 跑完已干净)

---
下一步: SKILL.md Section 7。**默认建议**:
  🟢 → 一批 apply + 一次 diff 预览 + 一键 OK
  🟡 → 按规则一组应用(改名 + XAML 同步在同一批,确保 diff 能看到 XAML 也改了),用户挑批
  🔴 → 逐条:先列引用扫描、再 preview、用户单独 OK
```

**关于"合规率/百分比":不要造假分母**。如果给比例,得说清楚分母(比如"扫描 5876 个 .cs 文件,17 个有命名违规,占 0.3%")。模糊的"合规率 76%"看起来像数据,实际是凭空数。要么列具体计数,要么不要这一行。

**报告写作要点:**
- 每条违规给 (a) 文件:行 (b) 违反的规则(引用 `代码规范.md` 具体章节,不要只说"规范说") (c) 建议改法 (d) 破坏性等级(🟢/🟡/🔴)
- **同一规则在同一文件命中多次,合并展示**:"src/Foo.cs 有 12 处 Cmd 后缀"比 12 条独立强
- **中/高破坏性必须附引用扫描结果** —— 给 Section 7 实施时的影响面参考
- **规则冲突单独列**(规范文档 vs .editorconfig 矛盾),不混在违规里 —— 那是用户决策项,不是 fix 项

---

## 6. 不该做的事

- **不要把 Grep 命中直接当违规** —— 必须开文件确认上下文。`\.ToString\(\)` 经常误中 `int.ToString()` 等合法用法。误判一次消耗一次信任。脚本已经把确定性误判过滤掉,但 `enum_tostring_suspect` 这种**必须 LLM 读上下文**。
- **不要为了刷违规数把同一规则在同一文件里拆成几十条** —— 合并展示。
- **不要修改 `代码规范.md` 这份内嵌文档** —— 即使发现规范有自相矛盾、有打字错、有过时信息,在报告"规则冲突"那一节**指出**,但**不替用户改**。规范变动是用户决策。如果用户明确说"改一下规范文档加一条新规则"那是另一种意图,可以改,但要先确认。
- **不要试图替 LLM 跑 Roslyn 分析器** —— StyleCop / SonarAnalyzer / 自定义 analyzer 这些属于 `dotnet format analyzers` 子命令范畴。本文是这些 analyzer 之外、需要 LLM 语义判断或字符串模式匹配的部分。
- **不要去搜用户项目里的 `代码规范.md` 或类似文件作为额外规则源** —— 规则唯一来源是本目录的 `代码规范.md`。项目里有同名文件也忽略。如果用户希望用项目里的版本,告诉他把内容复制覆盖到 `~/.claude/skills/format-csharp/references/代码规范.md`。
- **不要在 audit 阶段就动文件** —— audit 只产出报告;实施在 SKILL.md Section 7 做(分批 + diff 预览 + 用户挑批 + build 验证)。
- **不要把"按规范统一改名"推给 code-refactor** —— 这是 format-csharp 的核心 use case。code-refactor 是处理 "structural"(提取方法、拆大类、改算法、合并重复逻辑)的,不是 "surface" 改名/重排/补注释。详见 SKILL.md Section 0 的边界说明。

---

## 附录:dotnet format 子命令

`dotnet format` 内部有 3 个子命令,默认全跑。用户明确说"只做某一类"时按需选:

| 子命令 | 作用 |
|---|---|
| `dotnet format whitespace <target>` | 只调缩进、行尾、空格 |
| `dotnet format style <target>` | 跑 IDE/style 规则(命名、using 排序、表达式 body 等) |
| `dotnet format analyzers <target>` | 跑第三方 analyzer 修复(StyleCop / SonarAnalyzer 等) |

用户原话举例:
- "只修缩进别动 using 顺序" → `dotnet format whitespace`
- "只跑 analyzer 修复" → `dotnet format analyzers`
- 默认/不确定 → 不指定子命令(全跑)

---

## 7. Per-rule 修复细则(SKILL.md Section 7 实施细则展开)

每条规则具体怎么改 —— grep 哪些引用、补哪些 using、何时反问用户。**每个修复都通过 SKILL.md Section 7 的"分批 + diff 预览 + 用户挑批 + build 验证"流程,这里只讲单条规则的具体动作**。

### 命名后缀类

- **`UseCmd` → `UseCommand`**(`cmd_suffix`):
  1. `Edit` 改 ViewModel/相关 .cs 里的属性声明
  2. `Grep` `XxxCmd` in `*.xaml *.axaml *.razor *.cshtml` 找 binding 引用
  3. `Edit` 同步所有 XAML binding(`{Binding XxxCmd}` → `{Binding XxxCommand}`)
  4. **diff 预览要能看到 .cs 改动 + .xaml 改动都在**
  5. `dotnet build` 验证

- **`AuthorAttr` → `AuthorAttribute`**(`attribute_no_suffix`):
  1. `Edit` 改类声明 + 同文件内构造函数引用(`AuthorAttr(...)`)
  2. `Grep` `[AuthorAttr` in `*.cs` 找标注用法
  3. `Edit` 同步所有 `[AuthorAttr(...)]` → `[Author(...)]`(注意:C# attribute 用法时会自动补 `Attribute` 后缀,所以代码里写 `[Author]`,真实类名是 `AuthorAttribute`)
  4. `dotnet build` 验证

- **`AppExc` → `AppException`**(`exception_no_suffix`):同 Attribute 流程,但 grep 用法是 `throw new XxxExc(` 和 `catch (XxxExc`

- **`Foo` → `IFoo`**(`interface_no_i_prefix`):
  1. `Edit` 改 interface 声明
  2. `Grep` 整个工程找用法(`Foo foo;` `: Foo` `where T : Foo` 等)
  3. `Edit` 同步所有引用
  4. **注意**:可能有命名冲突 —— 项目里已经有 `IFoo` interface,这种情况要先反问用户改成什么名

### 标识符语言类

- **中文标识符 → 英文**(`chinese_identifier`):
  1. **先反问用户目标命名** —— `用户信息` → `UserProfile` / `UserInfo` / `UserDetail`? 是语义判断,不能自己拍板
  2. 用户拍板后,`Edit` 改声明
  3. `Grep` 全工程(`*.cs *.xaml *.json *.resx *.config`)找所有出现的中文名
  4. `Edit` 同步所有引用
  5. **如果是文件名也含中文**:重命名文件(`git mv 用户信息.cs UserProfile.cs`),`.csproj` 里如果显式列了文件路径(老项目)也得改
  6. `dotnet build` 验证

### 私有字段前缀类

- **`isVisible` → `_isVisible`**(`private_field_no_underscore`):
  1. `Edit` 改字段声明
  2. `Grep` 同文件内的引用(私有字段只在同 class 内,范围小)
  3. `Edit` 同步引用点
  4. **特别注意**:`this.isVisible` → `this._isVisible`,`OnPropertyChanged(nameof(isVisible))` → `nameof(_isVisible)`(WPF/MVVM 项目要小心 `nameof` 引用,虽然属性名不变但绑定字段名变了)
  5. `dotnet build` 验证

### 注释类

- **加 `<summary>` 注释**:
  - LLM 按方法/类的语义自己写一两行中文 summary(参照 `代码规范.md` 注释规范的 4-6 条)
  - **不能为了凑数写废话** —— "Method to do something" 比没注释更恶心
  - 写不出有信息量的就在报告里标"建议手工补",不动手

- **`<summary>` 单行 → 多行**(`summary_inline`):
  把 `/// <summary>内容</summary>` 拆成三行:
  ```
  // 之前
  /// <summary>处理订单付款</summary>
  // 之后
  /// <summary>
  /// 处理订单付款
  /// </summary>
  ```
  **保留原文不动,只调布局**。其他 XML 文档标签(`<param>` / `<returns>` 等)单行允许,只 `<summary>` 必须多行

### 类内归类

- **重排类成员**(规范要求顺序:字段 → 属性 → 构造 → 事件 → 方法):
  1. 读类体,用 Roslyn 语法理解或者手工识别每个成员的 kind
  2. `Edit` 重写整个类体,按顺序重排
  3. **更稳的方式**:把成员按类型分组用 `#region` 包起来,既符合规范又减少 diff 噪声
  4. `dotnet build` 验证

### 写法禁令类

- **匿名 delegate → 具名方法**(`anonymous_delegate`):
  1. 在同 class 内加 `private void OnFooHappened(object s, EventArgs e) { ... }` 等具名方法,函数体复制自原匿名 delegate
  2. `Edit` 把 `EventHandler handler = delegate (object s, EventArgs e) { ... };` → `EventHandler handler = OnFooHappened;`
  3. `dotnet build` 验证

- **if/for/while 加大括号**(`no_braces_on_control_flow`):
  1. `Edit` 把 `if (cond) doX();` → `if (cond) { doX(); }`
  2. 多行版:
     ```
     if (cond) { 
         doX();
     }
     ```
  3. 这条没有引用问题,改完直接 build 验证

- **浮点 `==` → epsilon**(`float_equality`):
  1. `Edit` 把 `if (x == 0)` → `if (Math.Abs(x) < 1e-9)`(epsilon 值看精度需求选,常见 `1e-9` / `1e-6` / `float.Epsilon`)
  2. 反向:`!=` 改 `>=`
  3. `dotnet build` 验证

- **`enum.ToString()` → `[Description]+.GetDescription()`**(`enum_tostring_suspect`):
  1. **先 LLM 读上下文确认**左侧确实是 enum(不是 int/DateTime/etc)
  2. **再 grep 项目里有没有 `GetDescription()` 扩展方法**:`Grep("public static.*GetDescription\(this Enum")`
  3. 有的话:`Edit` enum 加 `[Description("XXX")]` 标注,`Edit` 调用点 `.ToString()` → `.GetDescription()`,文件头加 `using System.ComponentModel;`
  4. **没有的话不要默认新建文件** —— 新建 `EnumExtensions.cs` 跨进 structural 范畴。**先反问用户**:"项目里没找到 `GetDescription` 扩展方法,要加到现有 Utilities 类、还是新建 EnumExtensions.cs?" 等用户拍板再动
  5. `dotnet build` 验证

- **字符串 `+=` → StringBuilder**(读判定,需 LLM 看上下文):
  1. 确认 `+=` 在循环体内
  2. `Edit` 在循环前加 `var sb = new StringBuilder();`
  3. `Edit` 循环内 `str += x` → `sb.Append(x)`
  4. `Edit` 循环后 `var str = sb.ToString();`
  5. 文件头加 `using System.Text;`
  6. `dotnet build` 验证

---

## 8. 全流程终极汇总 mock 范例

阶段 A + B 都跑完后给用户的合并报告范例:

```
✅ 代码风格清理完成

阶段 A (dotnet format 机械流): 改了 27 个文件
  - 主要修了: 缩进、using 排序、操作符空格

阶段 B (合规审计 + 实施): 改了 22 个文件,261 处违规
  🟢 私有字段加 _ 前缀                134 处
  🟢 if/for/while 加大括号             43 处
  🟡 ICommand 属性 Cmd → Command      72 处 (含 XAML binding 同步 53 处)
  🟡 enum.ToString() → [Description]  12 处

跳过未改:
  - 模块头注释规则 (默认 skip,现代项目不需要)
  - 规则冲突 1 处 (规范说 Tab,.editorconfig 说 space —— 待你拍板)

dotnet build: ✅ 通过
单元测试: ✅ 通过 (跑了 137 个测试,全过)

下一步建议: 检查一下 git diff --stat,觉得 OK 就 commit
  (一般建议拆 2-3 个 commit:1 个 dotnet format 改动 + 1-2 个语义改动批次,
   方便 code review 时分开看)
```

**报告原则**:
- 数据**用真实跑出来的数字**,不要凭空写(范例里的具体数 27/134/43/72/12/53 等只是示意 —— LLM 实际跑完后填脚本输出的真实计数)
- 不替用户 commit —— 让他自己看完最终 working tree 再决定怎么拆分

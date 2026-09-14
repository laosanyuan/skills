# 可选 Roslyn 注释检查

只读检查两条注释规则：`summary_inline` 根据真实 XML 文档注释确认标签是否独占物理注释行，标为 `syntax_layout_violation`；`comment_terminal_period` 只在真实注释中寻找句末中英文句号，仍标为 `candidate`，须核对缩写、URL、代码示例和字面数据，不自动修改。

```text
python -B <skill>/scripts/comment-syntax.py --scope <C#文件或目录> --artifacts-root <已存在且获准写入的绝对目录> --sdk-version 10.0.400
```

复用 `scan_scope.resolve_cs_files/read_source`：`--include-from` 相对清单目录解析，禁止越出 scope；默认排除生成目录、生成文件名和头标记；`--include-generated` 显式包含；`--encoding` 默认严格 BOM-aware UTF，编码错误不跳过。空范围失败，不把零文件报告成合规。

需要 Python 3.10+ 和已安装稳定版 .NET SDK 8+。省略 `--sdk-version` 选择本机最高稳定版本，输出记录实际 SDK、目标框架和 Roslyn 使用的语言版本。需要复现时固定 SDK。`--language-version` 默认 SDK 编译器默认值；不识别的版本失败，不替用户推断项目语言版本。`--define SYMBOL` 可重复，仅检查这些符号下的活动分支；不读取项目配置、不推断项目符号、不遍历所有符号组合。inactive 区域会列入 `unchecked_regions` 并使结果不完整，不能将其认定为合规。

真实的 raw/verbatim/interpolated string 字面内容不作为注释；插值表达式内真实注释仍可识别。文档 XML 的转义文本不当作 summary 标签；大小写敏感，只检查无命名空间前缀的 `summary`。识别 `///` 和 `/** */` 文档标签，但只有物理 `///` 行可通过布局检查；块式文档标签也报告布局违规。移除 `///` 前缀后，整行内容须恰好为单个真实开始或结束标签的源码；合法属性和标签内部空白不被误判为未独占行，自闭合、跨物理行或与正文同排仍命中。同一行开始和结束标签分别报告，不应把命中数当成缺陷行数。普通 `//` 或 `/* */` 中的 summary 文本不作为 XML 文档标签检查。

每次在显式产物根下新建唯一 `comment-syntax-*` 子目录，复制工具自身两份构建源，写入精确 SDK 的 `global.json`、清空包源的 `NuGet.Config`、解码后的输入快照、构建缓存、命令日志和 `report.json`。原始源文件只读；输入快照含被审查代码，请选合适的产物目录并按仓库隐私要求保管，不自动清理。产物根应在技能和目标项目外；目录 scope 内的产物根会明确拒绝，避免下次扫描包含工具副本，即使提供 include 清单也不例外。本工具不安装 SDK/包，不执行目标项目；仅离线 restore/build 自带项目，直接引用本机 SDK Roslyn 程序集，关闭父级 Directory.Build/Packages 导入和共享编译。

所有 .NET 命令从第一条 SDK 探测前使用现有 `run-verification.py` 的 `dotnet` profile，隔离 CLI home、NuGet 缓存和 TEMP，关闭证书生成、首次体验和遥测；使用其进程生命周期收尾机制。`--timeout` 默认 120 秒，是每条子命令的预算，另加执行器清理预算，不是整次扫描总预算。需要依次 SDK 探测、restore、build 和扫描，冷启动有构建开销，不声称比 regex 更快。此机制不是 OS 沙箱，不防止恶意进程替换已核验路径。

标准输出只给 JSON（`--help` 除外），构建输出单独保留。退出 `0` 只表示选定规则的活动语法扫描完成，**即使命中违规也可能为 0**；退出 `3` 表示存在解析诊断或未检查分支；退出 `2` 表示参数/读取/SDK/构建/执行失败，日志路径位于错误或 `run_dir` 中。不自动降级为 regex 并伪装成功；没有 SDK 时可显式运行原 `compliance-grep.py`，仍按候选人工确认。

位置是从 1 开始的物理行和 UTF-16 列，`offset` 从 0 开始，以解码文本 UTF-16 单元计数，不是原始文件字节偏移。解析诊断全部保留并使结果不完整；损坏语法附近的命中不能直接据此修改。此工具不做语义编译、summary 职责/简短判断、URL/缩写语义判断、自动修复或另外十条规则检查。零命中不等于全面合规。

句号扫描仍按物理行后缀判断，并非完整 XML 正文分析。真实 CDATA 正文末尾的字面句号，在后面仅有空白、Roslyn 识别的 CDATA 结束符和 XML 结束标签时也会作为候选；结构仅在等长检查视图中置为空白，报告保留原始 UTF-16 位置。普通注释、字符串、属性、实体或 CDATA 正文中的相似文本不会成为该新增检查的结构。后续有正文、开始标签、空元素或其他 XML 节点时，不按完整 XML 渲染结果推断句末；代码示例中间的句号、实体编码标点也不保证覆盖。即使 `status=completed`，仍须按实际注释内容人工复核这些盲点；该状态只说明解析/执行完整，不说明规则检出完整。

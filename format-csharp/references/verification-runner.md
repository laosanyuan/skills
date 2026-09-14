# 有界验证命令

已有验证命令获得授权、需要独立日志与超时收尾时，使用包内 `scripts/run-verification.py`（Python 3.10+ 标准库）：

```text
python <skill>/scripts/run-verification.py --cwd <绝对工作目录> --artifacts-root <已存在且获准写入的绝对目录> --timeout 120 --profile plain -- python -B -m unittest -v
```

参数均显式提供。`--` 后是已确认的程序及各参数；`shell=False` 只表示执行器不自动拼接 shell 命令，不保证指定程序及其子进程没有解释器语义。Windows 会隐式解释批处理文件，因此执行器拒绝直接 `.cmd`/`.bat`（忽略大小写及 Windows 路径末尾空格、点），不会自动换用 cmd 绕过。应选择实际可执行程序；确需解释器时，由调用方明确审阅其参数语义并取得相应授权，再显式指定该解释器。工作目录与产物根必须已存在，不接受 `..`、符号链接或 Windows reparse 目录（含祖先）。产物根不能是盘符根目录。脚本不能判断用户授权，选择路径与命令仍由调用方负责；已经被策略拒绝的命令不能换经此脚本重试。

每次创建唯一 `verification-*` 子目录，保留 `stdout.log`、`stderr.log`、`result.json`。两个输出流直接重定向到文件，不使用输出管道或读取线程；日志与结果均不覆盖，脚本不清理产物。标准输出同时给出 JSON：整体 `status`、清理前的 `command_status`、原命令 `exit_code`、`timed_out`、`run_dir` 与日志路径。只有命令退出 0 且收尾无错误、所管理进程已确认退出才是 `success`；非零、超时、启动失败、清理未确认或句柄关闭失败都不会成为成功。清理问题使整体状态为 `runner_error`，仍保留原命令状态、退出码与超时事实。输出中的 PASS 字样不决定结果，退出事实也不代替测试覆盖范围判断。

计时均为单调时钟秒数：`command_interval_elapsed` 从调用创建进程前到观察到命令终态、超时或异常；`cleanup_elapsed` 单独统计随后收尾；兼容字段 `command_elapsed` 是二者之和。`total_elapsed` 从进入执行器到生成最终报告、准备落盘之前，另含参数/目录/环境准备等开销，不包含报告自身写入及打印时间。

Windows 命令先以不创建控制台窗口且暂停状态创建，加入本次专属 Job 对象后才恢复；这不保证指定程序的 GUI 隐藏。收尾显式请求终止该 Job，等待根进程退出，并查询 Job 的活动进程数；只有观察到零才确认所管理进程全部退出。`cleanup` 分别记录 `method`、`requested`/`request`、`request_result`、`result`、`parent_exited`、最后观察的 `active_processes`（适用时）、Job 的 `close_action`/`close_result` 和 `errors`。`accepted` 仅表示系统接受终止请求；`confirmed_exited` 才是退出观察结果。关闭带 kill-on-close 的 Job 也会发起终止，但关闭成功本身不把 `unconfirmed` 升为已退出；显式 Windows CloseHandle 失败会记录并阻止整体成功。

根进程等待与后代退出观察共享从收尾开始计算的一个 5 秒截止时间，不会在 finally 再追加一轮 5 秒等待；因此总耗时可能超过命令的 `--timeout`，到期仍未确认退出会如实报告。该预算限制等待与轮询，同步操作系统文件/进程/句柄调用异常阻塞不受 Python 等待超时控制。不按名称全局结束进程，不触碰其它验证进程。POSIX 只管理独立会话中的原进程组，退出确认以该组不再存在为准；主动脱离组的后代不在此保证内，僵尸进程也可能导致保守地报告未确认。

执行 .NET 命令时选 `--profile dotnet`，从启动首条命令前设置 `DOTNET_GENERATE_ASPNET_CERTIFICATE=false`、`DOTNET_CLI_TELEMETRY_OPTOUT=1`、`DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1`、`DOTNET_ADD_GLOBAL_TOOLS_TO_PATH=false`，并将 `DOTNET_CLI_HOME`、`NUGET_PACKAGES`、`TEMP`、`TMP` 放入本次产物子目录。`plain` 保留原环境。此环境不自动 restore/install、不改全局配置，也不把普通构建转为离线命令；例如仅在项目已具备对应依赖且该命令获准时执行 `dotnet build --no-restore`。

这不是操作系统沙箱：命令仍具有调用账户的权限，自定义 targets、显式配置路径和网络访问都可能产生其它副作用。路径检查拒绝当前可见的重解析点，但不防御恶意进程在检查后替换路径。先检查命令及项目构建钩子的实际行为，执行器不扩大授权。

# alb flash:远程烧录命令

板子插在台架 Windows 上,人在 Linux 这边用 `alb flash` 烧录。命令不直接碰 USB:它把请求发给 hub,hub 交给台架上的 agent,agent 在那台 Windows 上运行 fastboot、高通 9008 工具或登记过的厂商工具。

```
Linux(你)                 hub(alb-api)                     台架 Windows
alb flash write ──HTTP──> 查参数、开记录、排队一个 ──作业通道──> alb_agent.py
                          镜像从 hub 的工作目录读               │ 再查一遍参数
                          边跑边收输出 + 串口                   v
                                                         fastboot.exe / 9008 工具 /
                                                         登记的厂商工具 ──USB──> 板子
```

- `alb flash` 连的是 hub 的 HTTP 接口:默认 `http://127.0.0.1:8765`,hub 在别的机器上就设 `ALB_API_URL`(见 [configuration.md](./configuration.md))。
- 一次只跑一个会改板子的作业。第二个**立刻拒绝**(`FASTBOOT_BUSY`),不排队。查询类(`status`、`devices`、`tools`)不受影响。
- hub 只发「操作名 + 参数」,不发命令行;台架 agent 按自己 `agent.conf` 里的设置拼出要运行的命令,参数再查一遍。

## 先准备什么

| 要做的事 | 需要 | 在哪配 |
|---|---|---|
| 所有 `alb flash` 命令 | hub 在跑,台架 agent 连上了 | [dial-home-runbook.md](./dial-home-runbook.md);`alb flash status` 查 |
| fastboot 类(`devices` `getvar` `reboot` `oem` `write` `erase` `plan`) | 台架上有 fastboot | `agent.conf` 的 `fastboot_path`,步骤见 [FASTBOOT-SETUP.md](../clients/windows-agent/FASTBOOT-SETUP.md) |
| 只许写某些分区 | 分区白名单,可以按板子分 | `flash_partitions` / `flash_partitions.<板>`,见 [Windows agent README](../clients/windows-agent/README.md)「按板分名单」 |
| 高通 9008 | 两个高通工具 + 刷机包目录 | `edl_sahara` `edl_firehose` `edl_root`,见 [Windows agent README](../clients/windows-agent/README.md)「高通 9008」 |
| 其它厂商的下载工具 | 在台架上登记工具 | `tool_exe.<名>` 等,见 [Windows agent README](../clients/windows-agent/README.md)「其它厂商的下载工具」 |

几块板轮流接同一根线时,先用 `alb bench board use <板名>` 选中这次的板子:串口跟着切,按板分的分区名单也按它来。

## 命令一览

| 命令 | 做什么 | 板子要在 | 改板子、要确认 |
|---|---|---|---|
| `alb flash status` | agent 连上没有、是否在忙、现在能不能写分区 | — | 否 |
| `alb flash devices` | 台架上的 `fastboot devices` | — | 否 |
| `alb flash getvar [名字]` | `fastboot getvar`,不写名字等于 `getvar all` | fastboot | 否 |
| `alb flash reboot [目标]` | 离开 fastboot;不写目标就回系统,或 `bootloader` / `fastboot` / `recovery` | fastboot | 改,不确认 |
| `alb flash oem <命令>` | `fastboot oem <命令>`,命令原样发给 bootloader | fastboot | 是 |
| `alb flash write <分区> <镜像>` | 写一个分区 | fastboot | 是 |
| `alb flash erase <分区>` | 擦一个分区 | fastboot | 是 |
| `alb flash plan <清单>` | 按刷机清单整包烧,可以跳过指定分区 | fastboot | 是(整份确认一次) |
| `alb flash tools` | 台架上登记了哪些厂商工具、下载模式的设备在不在 | — | 否 |
| `alb flash tool <名> -- <参数>` | 运行登记的厂商工具,参数照厂商的写法 | 那家的下载模式 | 是 |

要确认的命令加 `--yes`(`-y`)跳过确认,脚本和 AI 调用时用。

高通 9008 和另外几个操作没有单独的 `alb flash` 子命令,用通用入口 `alb bench run <操作> 参数=值`,`alb bench ops` 列出台架 agent 会的全部操作:

| 操作 | 例子 |
|---|---|
| `edl-sahara` / `edl-program` / `edl-reset` | `alb bench run edl-program dir=board-a/v12 xml=rawprogram0.xml,patch0.xml` |
| `set-active` | `alb bench run set-active slot=b` |
| `boot` | `alb bench run boot --image images/boot-test.img`(只进内存,不写分区) |
| `usb-list` | `alb bench usb`:台架上的 USB 设备和每块板现在是什么模式 |

## 看状态:status / devices / getvar

```bash
alb flash status          # [ok] ready / [busy] <在跑什么> / [fail] <原因>
alb flash devices         # 板子从 adb 里消失后(进了 fastboot),只有这里看得到它
alb flash getvar product
alb flash getvar          # getvar all
```

- `status` 在「能连上但一个分区都不许写」时也返回 1 并说明原因,比如按板分了名单、却没有选板子。
- `getvar` 把板子的回答原样打出来,不解释值。同一个变量在不同平台上的意思不一样(比如有的平台对存在的分区也回 `partition-size: 0`),判断以板子原话为准。
- 板子回 `FAILED`(比如 `getvar:<名> FAILED (remote: 'GetVar Variable Not found')`,这块板没有这个变量)时报 `FLASH_GETVAR_FAILED`、退出码 1。fastboot 自己这时退出码是 0,所以单看 fastboot 会当成功。不写名字的 `alb flash getvar` 列出板子有哪些变量。

## 进出 fastboot

进 fastboot 用 `alb power reboot fastboot`(或 `bootloader`,看平台):走 adb,adb 不通时自动改走串口。出 fastboot 用 `alb flash reboot`,回系统不用写目标。

## 写、擦一个分区:write / erase

```bash
alb flash write boot_a images/board-a/v12/boot.img
alb flash erase misc
```

- **镜像在 hub 那台机器上**,路径相对 hub 的工作目录(hub 的 `ALB_WORKSPACE`)。路径跑出工作目录的拒绝。hub 先算 sha256,台架 agent 收完再核一次,对不上不写。
- 分区名先在 hub 查,台架 agent 再查:形状(字母、数字、`_` `.` `-`,不能以 `-` 开头)和白名单。不在名单上报 `FLASH_PARTITION_REJECTED`,什么都不写。
- 配了 `fastboot_product.<板>` 的,写之前先问板子 `getvar product`,和选中的板子对不上报 `FLASH_BOARD_MISMATCH`。
- 写的时候串口一起录下来(见下面「结果和记录」)。

## 整包烧:plan

```bash
alb flash plan images/board-a/v12/flash.txt --dry-run              # 先看计划
alb flash plan images/board-a/v12/flash.txt --skip userdata --yes
```

清单是厂商刷机工具逐行执行的那种文本,第一列是厂商工具名,不看:

```
# 注释
tool getvar check-board-a
tool flash boot_a boot.img
tool erase misc
tool oem some-command
tool reboot-bootloader
```

| 动作 | 参数 | 说明 |
|---|---|---|
| `flash` | 分区 文件 | 文件名相对清单所在目录 |
| `erase` | 分区 | |
| `getvar` | 名字 | 当检查用:板子回 FAILED 就停,后面的不跑 |
| `oem` | 命令 | |
| `reboot-bootloader` | — | 重启回 fastboot,等板子重新出现(`--reboot-wait` 秒,默认 120) |
| `reboot` | — | |
| `setvar` | 键 值 | fastboot 没有这个命令,必须用 `--skip-action setvar` 跳过,整份才会通过 |

- **整份先查,有一处不对就一行都不跑**:动作不认识、镜像不存在、分区名不合规、`--skip` 的名字一行都没匹配上、台架 agent 会拒的分区。
- 查过后打印计划、确认一次,然后在一次占锁里按顺序跑完,中间别人插不进来;第一行失败就停,结果列出跑了哪些、停在第几行。
- `--skip <分区>`:不碰这些分区,逗号分隔或写多次,名字要一字不差。`--skip-action <动作>`:跳过没有分区的那几类行。
- `--from-line N`:上次停在中间,从清单第 N 行接着跑;前面的 `getvar` 检查照样跑。
- 确认后、开跑前,hub 会重算一遍计划,镜像在这期间被改过就拒绝,保证跑的就是你确认的那一版。

## 其它厂商的下载工具:tools / tool

台架上登记过的厂商命令行工具(登记方法见 [Windows agent README](../clients/windows-agent/README.md)「其它厂商的下载工具」),参数照厂商文档写,alb 只负责转发和记录。

```bash
alb flash tools
alb flash tool vt --pkg board-a/v12 -- -p {com} -f update.img
```

- `--` 后面的参数原样交给工具,不经过 cmd.exe。`--pkg` 是工具的 `tool_root` 下的目录,工具在那个目录里运行,文件写包里的相对路径就行。
- `{pkg}` 换成包目录的完整路径,`{com}` 换成登记的 USB 设备的 COM 口(有两个时用 `--port COMnn` 选),`{{` `}}` 是字面的花括号。
- 台架 agent 拒绝带盘符路径(`X:\`)、网络路径(`\\server`)、从盘根开始的路径(`\x`)、`..` 的参数,报错写明第几个参数。
- 结果里的 `judged by` 写明是谁判的:`fail_pattern`(输出里有失败标志)、`exit_code`(退出码不是 0)、`ok_pattern_missing`(退出码 0 但没看到成功标志)、`exit_code+ok_pattern`、`exit_code_only`(台架没登记成功标志,只看了退出码——有的工具失败也返回 0,烧完到板上核对)。
- 工具自己写的日志文件会收回 hub,命令最后打出放在哪。
- 记录里的 `board` 是台架当前选中的板子(`alb bench board use`),不一定是工具实际烧的那块:透传不知道工具碰的是哪块板。几块板同时接在台架上时,先选中要烧的那块。

## 结果和记录

退出码:

| 命令 | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| 会改板子的命令 | 成功 | 被拒或失败 | hub 拒绝请求或连不上 | 流断了、没收到结果:**板子状态未知**,先看 hub 日志再重试 |
| `status` | 能写 | 不能写,或没有能烧录的 agent | hub 连不上 | — |
| `plan` | 跑完(或 `--dry-run` 计划通过) | 计划有问题,或某一行失败 | 同上 | 同上 |

失败时打印错误码、原因和建议(`try:` 那行),错误码的完整解释见 [errors.md](./errors.md)。

会改板子的作业都在 hub 的工作目录下留一份记录,命令最后一行 `record:` 给出路径:

```
<hub 工作目录>/flash/<操作>-<UTC 时间>/
├─ timeline.jsonl   作业的每条输出和串口的每一行,用同一个时钟
├─ uart.log         串口原始字节(写分区、boot、plan、tool 才录串口)
├─ job.json         最终结果
└─ files/           厂商工具自己写的日志(只有 tool)
```

按时间顺序看作业输出和串口:

```bash
alb timeline show <记录目录>
alb timeline show <记录目录> --src uart --grep panic
```

烧完板子没起来时,先看这份:最后一条作业输出前后串口在打什么。

## 给 AI 用

AI 通过命令行用这些命令时:改板子的命令加 `--yes`;`alb flash tool --json`、`alb bench run --json` 输出整份结果帧,`data` 里有 `judged_by`、文件列表等结构化字段。MCP 现在没有烧录工具。

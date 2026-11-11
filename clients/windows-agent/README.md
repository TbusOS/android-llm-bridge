# alb 设备 agent — Windows 端使用说明

（English version: [README.en.md](README.en.md)）

这个文件夹是「远程调试桥」的 **Windows 端**。它的作用一句话：**板子插在这台
Windows 上（USB 线 + 串口线），跑起这个 agent 之后，Linux 那边（hub）就能像
板子直接插在 Linux 上一样调试它** —— adb、UART 串口、网页控制台、AI 调试都
从 Linux 侧驱动，Windows 这边不用再开 Xshell / SecureCRT。

连接方向是 Windows **主动拨出**去连 Linux，所以 Windows 不用开任何入站端口、
不用装 SSH 服务、防火墙不用改。

## 快速开始（3 步）

1. 确认这台 Windows 装了 **Python 3.11 或更高**（没装的话去
   https://www.python.org/downloads/ 下载，安装时勾上
   "Add python.exe to PATH"）。
2. 把 `agent.conf.example` 复制为 `agent.conf`，填两个值：`hub_url`（hub 的
   地址）和 `token`（和 hub 端 `hub.env` 里 `ALB_AGENT_TOKEN` 一致，问 hub
   那边要）。
3. **双击 `run-agent.bat`**。完事。

窗口里看到 `connected to ws://... as win-xxxx (build xxxxxxxxxxxx)` 就是连上了。把窗口最小化挂着
即可（关掉窗口 = 断开远程调试）。括号里的 build 号是这台机器跑的版本，启动时第一行
`starting: build ...` 也会打出来，跟 `alb bench ops` 第二行对得上就是同一版。

在窗口里选中文字（比如复制日志）时，窗口会停止滚动，但 agent 照常工作，adb、串口、
烧录都不受影响；按 Esc 取消选中，这期间的日志会补打出来。日志文件不受影响。
（build 7f98af4a52b5 及更早的版本做不到这一点：选中文字会让整个 agent 停住。）

## 每次启动都会自动检查什么

`run-agent.bat` 每次运行都把环境过一遍，逐项打印结果：

| 检查项 | 不通过时的行为 |
|---|---|
| Python 3.11+（`python` 或 `py -3`） | 停下并提示安装地址 |
| websockets / pyserial 两个依赖库 | **自动安装**，装失败才停 |
| `agent.conf` 是否存在 | 停下并提示先复制 `agent.conf.example` |
| adb 是否在 PATH + 当前设备列表 | 只提示不阻塞（不装 adb 串口功能照常用）；有 adb 时把 `adb devices` 看到的设备直接打出来 |
| 本机能看到的串口列表 | 打印出来供核对，空列表会提示查线/驱动 |

任何一步失败窗口都会停住（按任意键才关），报错原因看得见。

## 状态页：连没连上、出了什么错，本机自查

agent 启动后在本机开一个状态页：**http://127.0.0.1:8731**（只有这台电脑
自己能访问）。上面能看到：

- **hub 网页控制台的链接**（`web console` 一行，直接点开就是网页界面；
  启动时窗口里也会打一行 `hub web console: http://...`，不用再问地址）
- 连接状态（绿点 connected / 红点 disconnected）和最近一次错误原因
- 这台电脑上枚举到的 **adb 设备** 和 **串口列表**（板子插没插好一眼看出）
- 当前活跃的转发通道（Linux 那边正在用 adb 还是串口）

连不上 hub 时优先看这个页的 `last error`：
- `HTTP 403` / `1008` → token 不对，或 hub 端还没起来
- `connection refused` / `timeout` → hub 地址不对、hub 没起、或网络不通

## 常见问题

**问：串口用不了，Linux 那边说打不开 COM 口？**
按顺序查三件事：
1. 状态页的 `serial ports` 一行有没有列出 COM 口。**空的**说明 Windows 根本
   没看到串口设备 —— 检查 USB 转串口线插没插在这台电脑、驱动装没装
   （设备管理器 → 端口(COM 和 LPT) 里应该能看到）。
2. 列表里有口但号不对（比如是 COM5 不是 COM4）→ 告诉 Linux 侧改
   `hub.env` 里的 `ALB_AGENT_SERIAL_COM` 后重启 hub（串口号是 Linux 侧配的，
   Windows 这边不用改）。
3. 号对但打不开 → 有别的程序占着这个口（Xshell / SecureCRT / 串口助手），
   把它们关掉。串口同一时间只能被一个程序占用。

**问：adb 设备列表是空的？**
- 这台 Windows 要装 adb（platform-tools）并在 PATH 里；
- 板子 USB 线插好，第一次连接要在板子屏幕上点"允许 USB 调试"；
- 本机 cmd 里跑 `adb devices` 能看到设备，Linux 那边才能看到；
- adb server 卡住没重新扫的情况，Linux 侧可以远程触发本机重启 adb server：
  `curl -X POST http://<hub>:8765/api/agent/adb/restart`（agent 在本机执行
  `adb kill-server` 后重新上报设备列表），不用人到这台电脑跟前。

**问：驱动正常、设备也枚举了，adb 还是看不到（列表空）？**
最常见的根因是**另一个改名版 adb 独占了 USB 接口**。很多厂商 PC 工具
（烧录/部署套件、模拟器等）自带一份改了名字的 adb（`xxx_adb.exe` 之类），
它的 server 先打开接口后就独占了——ADB 的 USB 接口同一时刻只允许一个进程
持有，标准 adb 每次打开都吃"拒绝访问"，设备列表永远是空的，而驱动看起来
完全正常。这套工具的应对：

- **启动自检**：`run-agent.bat` 在 devices 为 NONE 时会自动扫一遍进程表，
  把"名字里带独立 adb 字段（`xxx_adb`、`adb_server` 这类）但不是 adb 本尊"
  的进程连 pid 一起打出来，并给出处置命令（只认独立字段，名字中间碰巧连出
  a-d-b 三个字母的无辜进程不会被误伤）；
- **状态页/hub 可见**：agent 上报设备列表为空时会附带嫌疑进程清单
  （状态页 `adb conflicts` 一行、hub 的 `/agent/status` 里 `adb_conflicts`）；
- **远程一键处置**：Linux 侧跑
  `curl -X POST "http://<hub>:8765/api/agent/adb/restart?kill_conflicts=true"`，
  agent 会先结束嫌疑进程再重启 adb server。默认不杀（`kill_conflicts` 不传
  就只重启 server）——如果现场正拿厂商工具烧录，贸然杀它的 adb 会打断操作；
- **手动处置**：`taskkill /f /pid <pid>` 然后 `adb kill-server` + `adb devices`。
- 注意这是共存的固有矛盾：厂商工具每次运行都可能把接口再抢回去。用完厂商
  工具后顺手跑一遍处置命令即可，两边不会坏数据。

**问：想换 hub 地址 / token / 显示名字？**
编辑 `agent.conf`（记事本就行），改完重新双击 `run-agent.bat`。格式是
`键=值` 一行一个，`#` 开头是注释。各字段：

| 键 | 含义 |
|---|---|
| `hub_url` | hub 的地址，`ws://<Linux的IP>:8765/agent/connect` |
| `token` | 和 hub 端 `hub.env` 里 `ALB_AGENT_TOKEN` 一致的口令 |
| `name` | 在 hub 网页上显示的名字，随意 |
| `agent_id` | 固定身份号，别改；删了的话每次重启 hub 会当成新设备 |
| `status_port` | 状态页端口，默认 8731，写 0 关闭 |

**问：能开机自动启动吗？**
可以。任务计划程序（Task Scheduler）新建任务指向 `run-agent.bat`，并在任务的
环境变量里加 `ALB_AGENT_NO_PAUSE=1`（不加的话出错时脚本会等按键，后台任务会
卡住）。

**问：能从 Linux 那边远程启动 agent 吗？**
能，要先在这台 Windows 上配一次。agent 是往外拨的，它没在跑的时候，hub 那边
没有任何办法叫它起来；下面这一步给 hub 留一条安全的路：

1. 在 hub 上生成一把专用密钥（私钥只留在 hub）：
   `ssh-keygen -t ed25519 -N "" -C alb-hub-to-bench -f ~/.ssh/id_ed25519_albbench`，
   把 `~/.ssh/id_ed25519_albbench.pub` 那一行（公钥，不是秘密）拿到 Windows 这边。
2. 在本文件夹双击 **`setup-remote-start.bat`**，按提示粘贴那一行公钥。它做两件事：
   - 把公钥加进本账号的 SSH 授权文件。管理员账号用的是
     `C:\ProgramData\ssh\administrators_authorized_keys`（Windows 的 OpenSSH 只认这个，
     放在个人目录不起作用），并把它的权限收紧成只有管理员和 SYSTEM 能改；脚本
     会弹 UAC 要管理员权限。普通账号用 `%USERPROFILE%\.ssh\authorized_keys`。
   - 建计划任务 `alb-agent`：在你的桌面会话里运行 `run-agent.bat`（窗口照常可见），
     登录时也自动启动，不受默认的「运行 3 天后停止」限制。
   最后它打印两行：Windows 用户名和本文件夹路径，交给管 hub 的人。
3. hub 上加一条 `~/.ssh/config`：

   ```
   Host alb-bench
       HostName <这台 Windows 的地址>
       User <上面打印的用户名>
       IdentityFile ~/.ssh/id_ed25519_albbench
       IdentitiesOnly yes
   ```

4. hub 的 `deploy/hub/hub.env` 加一行 `ALB_BENCH_SSH_HOST=alb-bench`，重启 alb-api。

之后 agent 没在跑时，在 hub 上执行 **`alb bench agent-start`**：hub 经 ssh 启动这个
计划任务，再等 agent 连上来；没连上时把本文件夹 `logs\agent.log` 的最后几行一起
带回来。不想经 hub，也可以直接 `ssh alb-bench schtasks /Run /TN alb-agent`。

**这不是重启。** 这个任务已经在跑时，Windows 不会再启动第二份（任务设的是
「已在运行就忽略新请求」），`schtasks /Run` 照样回「成功」，但什么都没变。
换版本用 hub 上的 `alb bench agent-update`（文件没变时它也不重启）；目前 hub 这边
没有单纯重启 agent 的命令。

为什么经过计划任务，而不是用 ssh 直接启动：ssh 会话里启动的进程，桌面上看不到
窗口，ssh 一断它也跟着退出；计划任务里的进程就像在这台机器上双击启动的一样。

前提和边界：
- Windows 上要装好并启动 **OpenSSH Server**（`sshd`），脚本会检查并给出装法；
- 用户没登录时计划任务起不来，因为它要在桌面会话里跑；
- 没改防火墙，也没改 `sshd_config`；如果 `sshd_config` 指定了别的授权文件，以那里为准；
- 撤销：`setup-remote-start.bat --remove`，按提示粘贴同一行公钥，删掉公钥和计划任务。

**问：Ctrl+C 退不出去？**
- 第一次 Ctrl+C = 优雅退出（窗口会打一行 `stopping...`）；
- 万一进程卡住没反应，**再按一次 Ctrl+C 直接强杀**，窗口一定能退出来；
- 退出后 cmd 可能问 `Terminate batch job (Y/N)?`——按 Y 即可；
- 另外串口写操作带 2 秒超时，以前"串口被流控卡死导致退不出"的根因已堵上；
- 从计划任务 / 远程终端 / IDE 内嵌终端拉起时，Windows 会给进程继承一个
  "忽略 Ctrl+C"标志（按多少次都没反应）——agent 启动时会自动清掉这个
  标志，Ctrl+Break 也始终可用。

**问：窗口关了会怎样？**
远程调试断开，Linux 那边立刻看不到板子。重新双击 `run-agent.bat` 即可，断线
也会自动重连（1/2/5/10 秒退避），不用手动干预。

**问：工具本身出问题了怎么定位？**
看日志文件：**`logs\agent.log`**（本文件夹下，`agent.conf` 里
`log_file=logs/agent.log` 控制）。每次运行都往里追加，窗口关了记录也在：

- 启动环境一行（Python / websockets / pyserial 版本、用的哪个配置文件）
- 本机串口列表快照
- 每次连接 / 断开 / 重连的原因，每条通道打开失败的原因

文件到约 5 MB 自动轮转，最多保留 3 份旧的（`agent.log.1` ~ `.3`），不会
撑爆磁盘。报告问题时把这个文件一起发过来最有用。写 `log_file=none` 可关闭。

---

## 想让这台机器支持 fastboot 烧录？

板子进 fastboot 之后会从 adb 里消失，默认情况下 hub 那边就够不着它了 ——
连"把它重启回系统"都做不到。要补上这条路，照
**[FASTBOOT-SETUP.md](FASTBOOT-SETUP.md)** 配一遍（装 platform-tools +
在 `agent.conf` 里写 `fastboot_path=`），几分钟的事。

不配也不影响 adb 和串口，只是 hub 会直接告诉调用方"这台不能烧"。

## 这台 agent 能替 hub 做什么：操作表（usb-list / log-tail / set-active / boot / erase / tool-run）

agent 连上 hub 时（`hello`）会把自己会做的操作列成一张表报上去：每个操作带名字、
参数规则、要不要板子在 fastboot 里、会不会改设备、要不要独占、超时。hub 的命令行
和网页按这张表来做，所以 agent 多会一种操作，Linux 那边不用改代码就能用。

| 操作 | 做什么 | 要板子在 fastboot 里 |
|---|---|---|
| `devices` / `getvar` / `oem` / `reboot` / `flash` | 原有的 fastboot 操作，没变 | 是 |
| `erase` | `fastboot erase <分区>`，分区白名单跟 `flash` 共用（`flash_partitions`） | 是 |
| `set-active` | `fastboot set_active a` 或 `b`，切启动槽 | 是 |
| `boot` | `fastboot boot <镜像>`，镜像只进内存、不写分区 | 是 |
| `usb-list` | 列这台 Windows 上的 USB 设备：名称、VID:PID、序列号、驱动、问题码、接口类型。`fastboot devices` 为空时，用它分清是线没插在这台机器还是驱动没装 | 否 |
| `log-tail` | 读本机日志的最后 N 行，可按关键字筛。来源只有名字：`agent`（本 agent 自己的日志，`log_file` 配了才有）、`setupapi`（Windows 的驱动安装日志）、`update`（上一次远程更新的经过，更新过才有） | 否 |
| `agent-update` | 换这个 agent 自己的文件并重启，见下一节 | 否 |
| `edl-sahara` / `edl-program` / `edl-reset` | 高通 9008（EDL）烧录，见下面「高通 9008」一节 | 否（要板子在 9008 里） |
| `tool-run` / `tool-list` | 跑在 `agent.conf` 里登记过的厂商烧录工具，参数由调用方照厂商的写法给，见下面「其它厂商的下载工具」一节 | 否（要板子在那家的下载模式里） |

两条规矩不变：

- hub 只发结构化字段（操作名 + 参数），**从不发命令行、从不发路径**。argv 在这台
  机器上按写死的模板拼出来，参数先逐个校验（分区形状 + 白名单、枚举必须一字不差、
  整数有上下限）。
- `usb-list` 和 `log-tail` 只读本机信息、不碰板子，所以**不占烧录锁** —— 正在烧录
  时也能查。fastboot 类操作一次只跑一个，第二个会被立刻拒绝，不排队。

fastboot 没配（见上一节）时，表里只有 `usb-list` / `log-tail` / `agent-update`，hub
会直接告诉调用方这台不能烧。状态页（http://127.0.0.1:8731）的 `ops` 一行列的就是这张表。

### 几块板轮流接同一根线：按板分名单

`flash_partitions` 整台 agent 一份，几块板轮流接同一根 USB 线时只能写成并集 ——
只有一块板有的分区，对另一块也放行。可以改成每块板一份（ADR-063 第二期）：

```
flash_partitions.board-a=boot,dtbo,vendor_cfg
fastboot_product.board-a=board_a_product
flash_partitions.board-b=boot_a,boot_b
```

板名就是 hub 板子表里的名字（`alb bench board`）。写了按板名单之后：

- hub 每个作业都带上它选中的板子，分区必须在**那块板**的名单上；没选板、或这块板
  没有名单，作业在收镜像之前就被拒（`FLASH_BOARD_NOT_LISTED`）。
- 配了 `fastboot_product.<板>` 的，改设备的作业动手前先跑一次 `fastboot getvar product`，
  答案对不上就拒（`FLASH_BOARD_MISMATCH`），什么都不写。板名只说「用哪份名单」，
  不说「线上接的是哪块板」，这一步才说。
- `flash_partitions` 总名单必须删掉，按板名单不能为空 —— 两种写法都会让名单变宽，
  agent 拒绝启动，而且在停掉旧实例之前就拒绝。

### 高通 9008（EDL）烧录

有的板子只能在高通紧急下载模式里写（USB 05c6:9008，设备管理器里叫
「Qualcomm HS-USB QDLoader 9008 (COMnn)」）。在 `agent.conf` 里配三行，agent 就多出三个操作：

```
edl_sahara=D:\flash\board-a\tools\QSaharaServer.exe
edl_firehose=D:\flash\board-a\tools\fh_loader.exe
edl_root=D:\flash\board-a\packages
```

目录建议按板子分：一块板（名字和 hub 板子表里的一样）一个目录，烧录要用的东西都放在里面。
根目录不按某一家的下载模式起名，别的厂商的工具以后也放在这下面。

```text
D:\flash\board-a\
├─ tools\       厂商工具，旁边放一个说明写来源和 md5
├─ packages\    解压后的刷机包，一个包一个目录，保留原名
├─ drivers\     驱动安装包，重装时用
└─ README.txt   怎么进下载模式、用哪个清单
```

这三行整台机器只有一套，不能按板子分别配：机器上只接一种要走 9008 的板子时，指向那块板的目录就行。

| 操作 | 做什么 |
|---|---|
| `edl-sahara dir=<包目录> [programmer=<文件名>]` | 用 QSaharaServer 把 firehose programmer 送进板子内存。每次重新进 9008 都要先跑一次 |
| `edl-program dir=<包目录> xml=<a.xml,patch0.xml> [memory=emmc]` | 用 fh_loader 按清单写 |
| `edl-reset` | 让板子从 9008 重启出来 |

- `dir` 是 `edl_root` 下的相对目录（比如 `pkg-v1/fac`，`/` 和 `\` 都认；在 bash 里写 `\` 要加引号），hub 只发这个名字，
  完整路径在这台机器上拼；拼出来跑到 `edl_root` 外面的（包括链接指出去的）一律拒绝。
  根目录下哪个包都能用，没有包名单。
- `programmer` 不写就用目录里唯一的 `prog_*firehose*` 文件，有两个就拒绝、列出来让你选。
- 9008 的 COM 口 agent 自己从 USB 列表里找，只认 05c6:9008；找不到、或有两块板都在 9008 时报错并列出。
  `port=COMnn` 只能在这几个 9008 口里挑，调试串口点不中。
- 两个工具都在包目录里运行，文件按名字给，和手工跑的命令一样；fh_loader 会在包目录里写 `port_trace.txt`。
- 判成功要退出码 0 **并且**有成功标志（Sahara 的「Sahara protocol completed」、
  fh_loader 的「{All Finished Successfully}」）。失败时结果里带工具输出的最后 80 行。

### 其它厂商的下载工具：登记后照厂商的参数跑

别家芯片的下载模式工具，各家参数都不一样，alb 不去理解它们：在 `agent.conf` 里把工具登记一个名字，
调用方照厂商文档写参数，agent 检查过路径后原样交给工具。厂商工具平时怎么跑，现在还怎么跑，只是换成在
Linux 那边敲命令。

```
tool_exe.vt=D:\flash\board-a\tools\VendorTool.exe
tool_root.vt=D:\flash\board-a\packages
tool_ok.vt=Download finished
tool_fail.vt=(?i)fail|error
tool_note.vt=^Write .* (ok|fail)
tool_usb.vt=1a2b:3c4d
tool_usage.vt=按住 recovery 键插 USB 进下载模式
tool_usage.vt=例:alb flash tool vt --pkg v12 -- -p {com} -f update.img
```

`vt` 是自己起的名字（小写字母、数字、`_`、`-`，最长 32）。每个键的含义：

| 键 | 必填 | 含义 |
|---|---|---|
| `tool_exe.<名>` | 是 | 工具 exe 的绝对路径 |
| `tool_root.<名>` | 是 | 刷机包的根目录，一个包一个子目录；hub 只能指定它下面的目录 |
| `tool_cwd.<名>` | 否 | `package`（默认）：在包目录里运行，和手工 `cd` 进去跑一样；`job`：每次一个临时目录，跑完删掉 |
| `tool_timeout.<名>` | 否 | 秒，1-1700，默认 1500。到点连工具起的子进程一起结束 |
| `tool_ok.<名>` | 否 | 正则。写了就必须有一行匹配才算成功 |
| `tool_fail.<名>` | 否 | 正则。有一行匹配就算失败，退出码是 0 也算 |
| `tool_note.<名>` | 否 | 正则。匹配的行列进结果，比如每个分区一行 |
| `tool_usb.<名>` | 否 | `VID:PID`，逗号分隔可写多个。运行前先查 USB 上有没有；参数里用 `{com}` 时必须写 |
| `tool_usage.<名>` | 否 | 说明文字，`alb flash tools` 里显示。写多行就重复这一行 |

调用（在 Linux 那边）：

```bash
alb flash tools                                   # 登记了哪些工具、下载模式的设备在不在、说明
alb flash tool vt --pkg v12 -- -p {com} -f update.img
```

- `--` 后面的参数原样交给工具，不经过 cmd.exe，参数里的空格、`&`、`%` 都不会被解释。stdin 是空的，
  等按键的工具会直接读到结束，不会卡住。
- agent 先查每个参数，有下面任何一种就拒绝，工具不会运行（`BENCH_TOOL_ARG_REJECTED`，报错写明第几个参数）：
  盘符路径（`X:\`、`X:/`）、网络路径（`\\server`、`//server`）、从盘根开始的路径（`\x`）、作为一段路径的 `..`。
  串口设备名 `\\.\COM<n>` 放行。文件按包里的相对路径写（工具在包目录里运行），或者用 `{pkg}`。
- 查完才替换：`{pkg}` 换成包目录的完整路径，`{com}` 换成 `tool_usb` 那个设备的 COM 口，`{{` `}}` 是字面的花括号。
- 判成功按这个顺序：有一行匹配 `tool_fail` → 失败；退出码不是 0 → 失败；写了 `tool_ok` 但没有一行匹配 → 失败；
  其余算成功。结果里的 `judged_by` 写明是哪一条判的；没写 `tool_ok` 时会提醒「只看了退出码」——
  有的工具失败也返回 0，这种工具一定要写 `tool_ok`。
- 每行输出边跑边传回 hub，和串口一起记进这次作业的 `timeline.jsonl`；跑完把工具在工作目录里新写或改过的文件
  （它自己的日志）收回 hub，放在记录目录的 `files/` 下。每个最多 4 MiB、最多 20 个、合计 6 MiB，超出的只列名字和大小。
- 和 fastboot、9008 共用一把烧录锁，一次只跑一个。
- `tool_exe` 或 `tool_root` 指的文件此刻不存在：启动日志打一条警告，这个工具不提供；键写错（缺必填、相对路径、
  正则写错、超时超范围）：agent 不启动。
- 管不到的：工具自己读的配置文件里写的路径、工具往固定目录（比如用户目录）写的东西、以 `/` 开头的参数
  （很多 Windows 工具用 `/x` 当选项，所以放行）。这层检查防的是手误和 AI 拼错路径，不防故意。
- 只有图形界面的工具不支持；要按键或短接才能进的下载模式，需要人到现场。

建议先把已经会用的 `fastboot.exe` 登记成一个透传工具，用 `alb flash tool fb -- getvar product` 和
`alb flash getvar product` 对照一次，确认这条路通了，再登记新厂商的工具。

## 更新 agent：在 Linux 那边推，不用到这台机器上拷

**只有第一次要手动装**：把整个文件夹拷到这台机器（比如 `D:\alb-windows-agent\`），
关掉旧的 agent 窗口，双击 `run-agent.bat`。在这之前跑的旧版 agent 不认识远程更新。

之后每次更新，在 Linux 那边跑：

```bash
alb bench agent-update                      # 只换程序文件，这台机器上的 agent.conf 不动
alb bench agent-update --config agent.conf  # 连 agent.conf 一起换
```

推过来的是 hub 自己那份 `clients/windows-agent/` 文件夹。这台机器上依次发生：

1. 收到压缩包，校验大小和 sha256；包里只许有平铺的文件名，带路径的一律拒绝。
2. 用新代码跑一次自检（`alb_agent.py --self-check`：能不能编译、依赖在不在、
   agent.conf 读不读得进）。不过就直接拒绝，旧版照常跑，什么都没换。
3. 拉起一个换版进程，旧 agent 退出，它的窗口自动关掉。
4. 换版进程把现有文件备份到 `.alb-agent\backup\`，换上新文件，在新窗口里启动新版。
5. 新版 60 秒内连上 hub 就算成功；没连上，就把旧文件放回去、重新启动旧版。
   Linux 那边会报 `AGENT_UPDATE_ROLLED_BACK`，`alb bench log update` 能看到经过。

文件一样就什么都不做，不会重启。`alb bench ops` 的第二行会写出这台机器跑的是哪一版
（build 号），跟 hub 那份不一样时会提示你更新。

文件夹里多出来的 `.alb-agent\` 放的是运行记录、更新暂存、备份和 `update.log`。
删掉也能用，下次启动会重新建；但最近一次更新的备份在里面，正在更新时别删。

### 同一个 agent 只会跑一份

双击 `run-agent.bat` 时，如果这个 agent 已经在跑，会先让旧的退出（旧窗口自动关），
再启动新的。旧的正在烧录时，会等烧完再接手，不会把烧了一半的分区打断。

"同一个"按 agent.conf 判断。要在一台机器上接两块板，就把整个文件夹再复制一份
（比如 `D:\alb-windows-agent-2\`），改那份 agent.conf 里的 `agent_id`、`name`。
两份各自更新、互不影响。

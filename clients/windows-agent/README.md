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

窗口里看到 `connected to ws://... as win-xxxx` 就是连上了。把窗口最小化挂着
即可（关掉窗口 = 断开远程调试）。

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

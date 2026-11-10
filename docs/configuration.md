---
title: 配置参考
type: reference
created: 2026-11-10
updated: 2026-11-10
owner: sky
tags: [configuration, reference, env, serial, adb, ssh, hub, agent, llm-friendly]
---

# 配置参考

> alb 的配置分在环境变量和 6 种文件里。本页按任务列出每一项:作用、默认值、哪个程序读、读它的代码文件。
> 写进去也不起作用的项单独标了 **无效**,集中列在 [第十节](#十设了无效的项)。
> `tests/test_docs_configuration.py` 会检查代码里出现的每个 `ALB_*` 变量都写在本页,代码加了变量而本页没写,测试就失败。

---

## 一、谁读哪份配置

alb 有四个程序。它们不在同一个地方读配置:

```
Linux 机器(hub 所在)
  alb / alb-mcp ...... 环境变量、config.toml、profiles/<name>.toml
      |                .env.local / .env 只有 alb 读,alb-mcp 不读
      |  连 127.0.0.1 上 hub 开的转发口:adb 默认 5037,串口默认 9001
      v
  alb-api(hub) ...... hub.env(run-hub.sh 导入)、boards.toml、
      ^                .env.local / .env、config.toml、profiles
      |  WebSocket,agent 主动连 hub(hub_url + token)
Windows 台架主机
  alb_agent.py ....... agent.conf、命令行参数
      |  本机 adb server(127.0.0.1:5037)、COM 口
      v
  板子
```

| 配置来源 | `alb`(CLI) | `alb-mcp` | `alb-api`(hub) | Windows agent |
|---|---|---|---|---|
| 进程的环境变量 | 读 | 读(只有 MCP 客户端配置里给的) | 读 | 只读 `ALB_AGENT_LAUNCHER`;`run-agent.bat` 另读 `ALB_AGENT_NO_PAUSE` |
| `.env.local` / `.env` | 读 | **不读** | 读 | 不读 |
| `~/.config/alb/config.toml` | 读 | 读 | 读 | 不读 |
| `<workspace>/profiles/<name>.toml` | 读 | 读 | 读 | 不读 |
| `deploy/hub/hub.env` | 不读 | 不读 | 由 `run-hub.sh` 导入环境变量 | 不读 |
| `boards.toml`(`ALB_BENCH_BOARDS` 指向它) | 不读 | 不读 | 读 | 不读 |
| `clients/windows-agent/agent.conf` | 不读 | 不读 | 不读 | 读 |

出处:`.env` 文件由 `src/alb/infra/env_loader.py` 加载,只有 `src/alb/cli/main.py` 和 `src/alb/api/server.py` 调用它;`alb-mcp` 的入口 `src/alb/mcp/server.py` 不加载。agent 的配置读取在 `clients/windows-agent/alb_agent.py`(`_load_config`)。

### 1.1 改了配置要重启

- `alb` 每条命令是一个新进程,改完下一条命令就生效。
- `alb-mcp` 和 `alb-api` 是常驻进程。环境变量在进程启动时就定了;config.toml 和 profile 在第一次建传输时读一次,之后一直用这份(`src/alb/transport/factory.py` 的 `active_settings` 缓存)。**改完要重启这两个进程。**
- Windows agent 改了 `agent.conf` 要重启 agent,或者用 `alb bench agent-update` 推一份新的。

### 1.2 优先级

| 要决定的事 | 优先级(左边赢) | 代码 |
|---|---|---|
| 环境变量从哪来 | shell 里已有的 > `.env.local` > `.env` > 程序默认值 | `src/alb/infra/env_loader.py` |
| 用哪种传输 | `alb --transport` > `ALB_TRANSPORT` > profile 的 `primary_transport` > `adb` | `src/alb/transport/factory.py` |
| 用哪个 adb 程序 | `ALB_ADB_PATH` > `[transport.adb] bin_path` > PATH 上的 `adb` | `src/alb/infra/config.py` |
| 连哪个 adb server | `[transport.adb] server_socket` > `ADB_SERVER_SOCKET` > 本机 hub 的转发口 > adb 默认 5037 | `src/alb/infra/adb_endpoint.py` |
| 串口连哪里 | `ALB_SERIAL_DEVICE` > `ALB_SERIAL_TCP` > `[transport.serial] default_tcp_host:default_tcp_port` | `src/alb/transport/factory.py` |
| ssh 连哪台 | `ALB_SSH_HOST` > profile 里第一条 `transport = "ssh"` 的 `ssh_host` | `src/alb/transport/factory.py` |
| ssh 端口 / 用户 / 密钥 | `ALB_SSH_*` > `[transport.ssh]` 的对应键 | `src/alb/transport/factory.py` |
| hub 串口转发开哪个 COM | `alb bench board use` 选中的板子 > `ALB_AGENT_SERIAL_COM` / `ALB_AGENT_SERIAL_BAUD` | `src/alb/remote/bench_board.py` |
| Windows agent 的设置 | 命令行参数 > `agent.conf` > 内置默认值 | `clients/windows-agent/alb_agent.py` |
| `alb chat` 的模型后端 | 命令行参数 > 环境变量 > 后端默认值 | `src/alb/cli/chat_cli.py` |

两个容易踩的地方:

- `run-hub.sh` 用 `set -a` 把 `hub.env` 导入成环境变量,所以 `hub.env` 里的值赢过 `.env.local`。
- `.env.local` 先在当前目录找,找不到再到代码仓库根目录找;同名文件只读找到的第一个(`env_loader.py` 的 `_default_roots`)。

---

## 二、串口 UART

串口有三种接法。先看你是哪种:

| 接法 | 要配的 | 不用配的 |
|---|---|---|
| 串口线插在跑 alb 的这台 Linux 上 | `ALB_SERIAL_DEVICE`,波特率 `default_baud` | TCP 那几项 |
| 串口在别的机器上,经 ser2net 或 ssh 隧道转成 TCP | `ALB_SERIAL_TCP` 或 `default_tcp_host` / `default_tcp_port` | `default_baud`(TCP 模式不用它) |
| 台架:串口插在 Windows 台架主机,经 hub 转发 | hub 上配 COM 和波特率;客户端一般什么都不用配 | 客户端的 `default_baud` |

### 2.1 本机 /dev/ttyUSB0

**只有一个办法:设 `ALB_SERIAL_DEVICE`。** config.toml 里没有放本机串口路径的键。

```bash
export ALB_SERIAL_DEVICE=/dev/ttyUSB0
alb --transport serial shell 'uname -a'
```

- 波特率读 `[transport.serial] default_baud`,默认 115200。不知道波特率,用 `alb serial probe --device /dev/ttyUSB0` 挨个试。
- 打不开设备时,检查当前用户在不在 `dialout` 组(`src/alb/transport/serial.py` 的报错就这么提示)。
- `alb serial` 下除 `probe` 以外的子命令(`capture`、`shell`、`send`、`break-in`、`health`、`status`、`learn`),`--device` 参数**不能**填 `/dev/ttyUSB0`:它按 adb 序列号的格式检查,路径里有 `/` 会被拒(`src/alb/transport/factory.py` 调 `is_safe_device`)。本机串口路径要用 `ALB_SERIAL_DEVICE`。只有 `alb serial probe --device` 收路径。
- `alb setup serial --device /dev/ttyUSB0 --save` 只把波特率存进 config.toml,**不存设备路径**(`src/alb/cli/setup_cli.py`)。存完下次还是要设 `ALB_SERIAL_DEVICE`。
- 给 `alb-mcp` 用时,变量要写在 MCP 客户端的配置里,例如 `claude mcp add alb -e ALB_SERIAL_DEVICE=/dev/ttyUSB0 -- …`。

### 2.2 经 TCP 转发(ser2net、ssh 反向隧道)

不设任何东西时,alb 连 `localhost:9001`。端点不同就改 config.toml 或设环境变量:

```bash
alb setup serial --tcp-host localhost --tcp-port 9001 --save   # 探测通过后写进 config.toml
# 或者只对当前 shell 生效
export ALB_SERIAL_TCP=127.0.0.1:19001
```

- `ALB_SERIAL_TCP` 的格式是 `host:port`。写成 `:19001`,host 取 `default_tcp_host`。值里没有 `:` 时整个变量被忽略,退回 config.toml 的端点(`src/alb/transport/factory.py`)。
- TCP 模式下波特率由转发的那一端决定,alb 的 `default_baud` 不起作用。
- 搭 ser2net 和隧道的步骤见 [`methods/07-uart-serial.md`](./methods/07-uart-serial.md)。

### 2.3 台架(经 hub)

hub 连上 agent、并且配了 COM 口后,会在 `127.0.0.1:9001` 开串口转发口(`src/alb/remote/forwarder.py`)。这正好是客户端的默认端点,所以 hub 同一台机器上的 `alb` / `alb-mcp` 什么都不用配。

要配的都在 hub 上:

| 变量 | 作用 | 默认值 |
|---|---|---|
| `ALB_AGENT_SERIAL_COM` | 让 agent 打开哪个 COM 口,例如 `COM5` | 不设:hub 不开串口转发 |
| `ALB_AGENT_SERIAL_BAUD` | 这个 COM 口的波特率 | 115200 |
| `ALB_BENCH_BOARDS` | 多块板子轮流接同一个 COM 口时,板子表的路径;运行中用 `alb bench board use <name>` 切换 | 不设:没有板子表 |
| `ALB_SERIAL_FORWARD_PORT` | 串口转发口的端口 | 9001 |

- 选中的板子记在 `<workspace>/bench/board.json`,hub 重启后还在。选中的板子不在表里、或表格写错时,hub 退回 `ALB_AGENT_SERIAL_COM` / `ALB_AGENT_SERIAL_BAUD`,原因显示在 `/agent/status`(`src/alb/remote/bench_board.py`)。
- 转发口只绑 `127.0.0.1`。alb 不在 hub 那台机器上时,要自己先把端口转过来。
- 改了 `ALB_SERIAL_FORWARD_PORT`,每个客户端都要设 `ALB_SERIAL_TCP=127.0.0.1:<新端口>`:命令行用的写进 `.env.local`,MCP 的写进 MCP 客户端配置,hub 自己的网页串口控制台写进 `hub.env`。
- `boards.toml` 的写法见 [第九节](#93-boardstoml)。

### 2.4 串口相关的 config.toml 键

都在 `[transport.serial]` 下,由 `src/alb/infra/config.py` 解析,`src/alb/transport/factory.py` 使用。

| 键 | 作用 | 默认值 |
|---|---|---|
| `default_baud` | 本机串口(`ALB_SERIAL_DEVICE`)的波特率 | 115200 |
| `default_tcp_host` | TCP 端点的主机 | `localhost` |
| `default_tcp_port` | TCP 端点的端口 | 9001 |
| `handshake_timeout` | 连上后等多少秒判断板子处在什么状态 | 2.0 |
| `pty_link_dir` | **无效**:解析了,没有代码用它 | 空 |
| `prompts`(写成 `[transport.serial.prompts]` 表) | 改写识别板子状态的正则,见下 | 不设,用内置正则 |

`[transport.serial.prompts]` 的键只能是这 11 个:`uboot`、`spl`、`kernel_boot`、`linux_init`、`login_prompt`、`shell_user`、`shell_root`、`recovery`、`fastboot`、`panic`、`crash`(`src/alb/transport/serial_state.py` 的 `DEFAULT_PATTERNS`)。写了别的键,建串口传输时报 `unknown serial state pattern key`。`alb serial learn` 会照着板子的实际提示符生成一段可以直接贴进来的配置。

### 2.5 其它

- `alb reboot` 在没指定传输、adb 又找不到设备时,会改走串口重启(`src/alb/cli/power_cli.py` 调 `serial_fallback_for`),所以只用 adb 的人也可能用到上面的串口配置。设了 `ALB_TRANSPORT` 或 `--device` 就不会这样。

---

## 三、USB / adb

### 3.1 本机 USB

板子插在跑 alb 的这台机器上:装好 adb 就能用,不用配。

- adb 不在 PATH 上:设 `ALB_ADB_PATH=/path/to/adb`,或写 `[transport.adb] bin_path`。`ALB_ADB_PATH` 赢(`src/alb/infra/config.py`)。
- 没有 adb:`alb setup deps --install` 下载 Android Platform-Tools,装到 `~/.local/share/alb`,不需要 root。内网下载不了 Google 的地址时,设 `ALB_PLATFORM_TOOLS_URL=<zip 地址>` 换成镜像(`src/alb/infra/host_deps.py`)。
- 多台设备时用 `--device <序列号>` 选。profile 里的 `[[devices]]` 列表**不**用来选 adb 设备。

### 3.2 远程 adb server

adb server 在别的机器上(例如经 ssh 隧道转过来):

```bash
export ADB_SERVER_SOCKET=tcp:127.0.0.1:5037     # adb 自己的变量,alb 原样传给 adb
```

要长期固定,写进 config.toml:

```toml
[transport.adb]
server_socket = "tcp:127.0.0.1:5037"
```

`server_socket` 的优先级最高,写了它,本机 hub 的转发口就不会被自动发现。做法见 [`methods/01-ssh-tunnel-adb.md`](./methods/01-ssh-tunnel-adb.md)。

### 3.3 经 hub(台架)

hub 连上 agent 后,在 `127.0.0.1:5037` 开 adb 转发口(`src/alb/remote/forwarder.py`)。hub 机器自己也跑着 adb server 时会抢同一个端口,这时在 `hub.env` 里把转发口挪开:

```bash
ALB_ADB_FORWARD_PORT=15037
```

**挪开以后不要手动设 `ADB_SERVER_SOCKET` 去对上它。** 同一台机器上的 `alb` / `alb-mcp` 会问 hub 转发口在哪(GET `/agent/status`),自动用上(`src/alb/infra/adb_endpoint.py`)。

### 3.4 adb server 地址怎么定(ADR-057)

```
1. config.toml [transport.adb] server_socket    来源记为 config
2. 环境变量 ADB_SERVER_SOCKET                   来源记为 env
3. 本机 hub 的 adb 转发口(自动问出来)          来源记为 hub
4. 都没有:adb 默认的 5037                      来源记为 default
```

为什么要警告手抄端口:adb 客户端连上**任何**一个 adb server 都不报错。连错了 server,看到的是「0 台设备」,和板子没插一模一样。hub 的转发口换过端口、而 shell 配置文件里还留着旧的 `ADB_SERVER_SOCKET`,就会一直看到 0 台设备。所以:

- 用 hub 时,`ADB_SERVER_SOCKET` 和 `server_socket` 都不要设。什么都不设的 shell,反而跟着转发口走。
- 自动发现只问**本机**的 hub。地址由 `ALB_API_URL`,或 `ALB_API_HOST` + `ALB_API_PORT` 决定;指向别的机器时不做自动发现(`_hub_is_local`)。
- 等 hub 回答最多 0.4 秒,hub 没开不影响普通命令。
- 设备列表为空、而 hub 那边能看到设备、端口又对不上时,`alb devices` 和 `alb_status` 会直接说出这个矛盾(`endpoint_conflict`)。

---

## 四、ssh

ssh 传输连的是板子上的 sshd(dropbear、Termux openssh 等)。用 `alb --transport ssh …` 或 `ALB_TRANSPORT=ssh` 选它。

| 变量 | config.toml 里对应的键 | 作用 | 默认值 |
|---|---|---|---|
| `ALB_SSH_HOST` | 无;profile 里 `transport = "ssh"` 的 `ssh_host` | 板子地址 | 无,两处都没有就报错 |
| `ALB_SSH_PORT` | `[transport.ssh] default_port` | 端口 | 22 |
| `ALB_SSH_USER` | `[transport.ssh] default_user` | 用户名 | `root` |
| `ALB_SSH_KEY` | `[transport.ssh] key_path` | 私钥路径 | 不设:alb 不传密钥参数,由 asyncssh 按它自己的默认找 |
| `ALB_SSH_KNOWN_HOSTS` | `[transport.ssh] known_hosts` | known_hosts 文件 | 不设:用 `~/.ssh/known_hosts` 严格校验 |
| 无 | `[transport.ssh] connect_timeout` | 连接超时(秒) | 15 |

- 代码:`src/alb/transport/factory.py`、`src/alb/transport/ssh.py`。
- 关掉主机密钥校验(新刷的板子常用):只能在 config.toml 写 `known_hosts = ""`。环境变量设成空字符串会被当成没设,退回 config.toml 的值。
- profile 里设备的 `ssh_port` **无效**,端口只看 `ALB_SSH_PORT` 和 `default_port`。
- `alb setup ssh <host>` 只检查连通、提示怎么放公钥,不读上面这些变量。
- `alb doctor` 的 ssh 检查只看 `ALB_SSH_HOST` / `ALB_SSH_USER` / `ALB_SSH_PORT` 三个环境变量,不看 config.toml(`src/alb/capabilities/doctor.py`)。

---

## 五、hub(alb-api)

hub 推荐用 `deploy/hub/run-hub.sh` 启动:它把同目录的 `hub.env` 导入环境再启动 `alb-api`。第一次先 `cp hub.env.example hub.env` 再改。下面的变量都写在 `hub.env` 里。串口和板子表那几项见 [2.3](#23-台架经-hub)。

| 变量 | 作用 | 默认值 | 代码 |
|---|---|---|---|
| `ALB_AGENT_TOKEN` | agent 连 hub 时要带的令牌,和 agent.conf 的 `token` 一致 | 不设:`/agent/*` 不校验令牌 | `src/alb/api/agent_route.py` |
| `ALB_API_HOST` | hub 监听的地址 | `0.0.0.0`(所有网卡) | `src/alb/api/server.py` |
| `ALB_API_PORT` | hub 监听的端口 | 8765 | `src/alb/api/server.py` |
| `ALB_ADB_FORWARD_PORT` | adb 转发口的端口 | 5037 | `src/alb/remote/forwarder.py` |
| `ALB_WS_DEFLATE` | 设成 `1` 打开 WebSocket 压缩 | 关 | `src/alb/api/server.py` |
| `ALB_BENCH_SSH_HOST` | `alb bench agent-start` 用来 ssh 到台架主机的 Host(hub 用户 `~/.ssh/config` 里的名字) | 不设:命令报 `AGENT_START_NOT_CONFIGURED` | `src/alb/remote/agent_start.py` |
| `ALB_BENCH_AGENT_TASK` | 台架主机上启动 agent 的计划任务名 | `alb-agent` | `src/alb/remote/agent_start.py` |
| `ALB_AGENT_BUNDLE_DIR` | `alb bench agent-update` 推给 agent 的文件从哪个目录取 | 本仓库的 `clients/windows-agent` | `src/alb/remote/agent_update.py` |
| `ALB_LOG_LEVEL` | hub 日志级别,名字(`DEBUG`)或数字都行 | `INFO` | `src/alb/api/log_setup.py` |
| `ALB_WARMUP` | 设成 `0` 关掉启动时的模型预热 | 开(只在设了 `ALB_OLLAMA_MODEL` 时预热) | `src/alb/api/server.py` |
| `ALB_TPS_SAMPLE_INTERVAL_S` | 网页上模型生成速度(token/s)的采样间隔,秒;不是正数时用默认值 | 1.0 | `src/alb/infra/metric_sampler.py` |

几点说明:

- **令牌只保护 `/agent/*`**,hub 其余接口不校验身份。`ALB_API_HOST=0.0.0.0` 时 hub 启动会打一行警告。hub 要放在可信网络里。
- 不要把 `ALB_API_HOST` 改成 `127.0.0.1`:台架主机上的 agent 就连不进来了。
- `ALB_WS_DEFLATE` 默认关,是因为压缩在 hub 自己的事件循环里做,推大镜像时 hub 反而成了瓶颈(ADR-062)。hub 和 agent 之间网很慢时才打开。
- `ALB_BENCH_BOARDS` 写相对路径时,是相对 hub 进程的当前目录。`run-hub.sh` 只在用 `uv run` 启动时先 `cd` 到仓库根目录;装好的 `alb-api` 在 PATH 上时不切目录。写绝对路径最稳。
- hub 也读 `.env.local` / `.env`,但 `hub.env` 导入的值优先。

### 5.1 命令行怎么找到 hub

`alb flash …`、`alb bench …` 要发请求给 hub;adb 和串口的自动发现也要问 hub。它们按下面的顺序定 hub 地址(`src/alb/cli/flash_cli.py`、`src/alb/infra/adb_endpoint.py`):

| 变量 | 作用 | 默认值 |
|---|---|---|
| `ALB_API_URL` | hub 的完整地址,例如 `http://<hub-host>:8765`,设了就不看下面两个 | 不设 |
| `ALB_API_HOST` | hub 的主机。值是 `0.0.0.0` 时当成 `127.0.0.1` | `127.0.0.1` |
| `ALB_API_PORT` | hub 的端口 | 8765 |

同一个 `ALB_API_HOST`,hub 拿它当**监听地址**(默认 `0.0.0.0`),命令行拿它当**连接地址**(默认 `127.0.0.1`)。

---

## 六、台架 Windows agent

agent 是 `clients/windows-agent/` 里的 `alb_agent.py`,跑在插着板子的 Windows 主机上,主动连 hub。配置写在同目录的 `agent.conf`(从 `agent.conf.example` 复制),双击 `run-agent.bat` 启动。完整步骤见 [`dial-home-runbook.md`](./dial-home-runbook.md)。

### 6.1 agent.conf 的键

代码:`clients/windows-agent/alb_agent.py` 的 `_CONFIG_KEYS` 和 `_parse_args`。除了 `<key>.<board>` 这两种,每个键都有同名的命令行参数(下划线换成连字符,例如 `--hub-url`),命令行赢。`<key>.<board>` 只能写在文件里。

| 键 | 作用 | 默认值 |
|---|---|---|
| `hub_url` | hub 的地址,`ws://<hub-host>:8765/agent/connect`;hub 前面有 TLS 反向代理时用 `wss://` | 无,必填 |
| `token` | 和 hub 的 `ALB_AGENT_TOKEN` 一致 | 不设 |
| `name` | hub 连接页上显示的名字 | `device-agent` |
| `agent_id` | 固定的 agent 身份。不设时每次启动随机生成,hub 会当成一个新 agent;远程更新也要求设了它 | 每次随机 |
| `status_port` | 本机状态页端口,`0` 关掉 | 8731 |
| `status_host` | 状态页绑定的地址。改成别的会让状态页暴露到本机以外 | `127.0.0.1` |
| `log_file` | 运行日志文件,相对路径相对脚本所在目录;写 `none` 关掉 | 不写日志文件(模板里写的是 `logs/agent.log`) |
| `fastboot_path` | fastboot 程序路径 | PATH 上的 fastboot;找不到就不提供烧录能力 |
| `flash_partitions` | 允许 `alb flash write` 写的分区,逗号分隔 | 空:任何格式合法的分区名都收 |
| `flash_partitions.<board>` | 按板子分开的分区白名单,`<board>` 是 hub 板子表里的名字 | 不设 |
| `fastboot_product.<board>` | 这块板子 `fastboot getvar product` 应答的值;设了就先核对,不对就拒绝写 | 不设 |
| `edl_sahara` | 9008 模式用的 QSaharaServer 路径 | 不设 |
| `edl_firehose` | 9008 模式用的 fh_loader 路径 | 不设 |
| `edl_root` | 9008 刷机包所在的目录,hub 只能指定它下面的目录 | 不设 |

文件格式规则(`_load_config`):

- 一行一个 `KEY=VALUE`,只有第一个 `=` 起分隔作用,值里可以有 `=`。
- `#` 只在行首才算注释,值里的 `#` 原样保留。
- 值两边的一对引号会去掉。空值等于没写,但 `<key>.<board>` 写空值是错误,agent 拒绝启动。
- 写错键名,agent 拒绝启动,并列出所有合法键名。
- `flash_partitions` 和任何一个 `flash_partitions.<board>` 同时写,agent 拒绝启动。
- `edl_sahara` / `edl_firehose` / `edl_root` 三个要一起写。少一个、或路径不存在,9008 的三个操作都不提供,启动日志里写明原因(`_resolve_edl`)。

agent 连的 adb server 固定是台架主机上的 `127.0.0.1:5037`,不能改(`ADB_TARGET`)。

### 6.2 agent 相关的环境变量

| 变量 | 作用 | 谁设 | 代码 |
|---|---|---|---|
| `ALB_AGENT_NO_PAUSE` | 设了以后 `run-agent.bat` 退出时不等按键。无人值守启动时用 | `setup_remote_start.py` 注册的计划任务会设 `1` | `clients/windows-agent/run-agent.bat`、`clients/windows-agent/setup_remote_start.py` |
| `ALB_AGENT_LAUNCHER` | 告诉 agent 它是从 `run-agent.bat` 启动的,远程更新后按同样方式重启。**内部用,不要手动设** | `run-agent.bat` | `clients/windows-agent/alb_agent.py` |

hub 那边和 agent 有关的变量(`ALB_AGENT_TOKEN`、`ALB_AGENT_SERIAL_COM`、`ALB_BENCH_SSH_HOST` 等)见第五节和 2.3。

---

## 七、网页助手和 alb chat 的模型后端

两处会用到模型:命令行的 `alb chat`,和 hub 网页上的助手。后端有三种:`ollama`、`openai-compat`、`anthropic`。`llama-cpp` 还没实现,选它会报错,提示改用 `openai-compat` 连 llama.cpp 自带的服务。

| 变量 | 作用 | 默认值 | `alb chat` 读 | hub 读 |
|---|---|---|---|---|
| `ALB_AGENT_BACKEND` | `alb chat --backend` 的默认值 | `ollama` | 读 | 不读 |
| `ALB_OLLAMA_URL` | Ollama 地址 | `http://localhost:11434` | 读 | 读 |
| `ALB_OLLAMA_MODEL` | 模型名(注意下面第一条) | `qwen2.5:3b` | 读 | 读 |
| `ALB_OLLAMA_NUM_CTX` | 请 Ollama 开多大的上下文(token 数);`0` 表示不发这个参数,用 Ollama 自己的默认 | 16384 | 读 | 读 |
| `ALB_OLLAMA_TIMEOUT` | 等 Ollama 回答的秒数,包括等第一个字 | 600 | 读 | 读 |
| `ALB_ANTHROPIC_URL` | Anthropic API 地址 | `https://api.anthropic.com` | 读 | 读 |
| `ALB_ANTHROPIC_KEY` | Anthropic API key;不设时读 `ANTHROPIC_API_KEY` | 不设 | 读 | 读 |
| `ALB_ANTHROPIC_MODEL` | Anthropic 模型名(会被 `ALB_OLLAMA_MODEL` 盖掉,见下) | `claude-haiku-4-5-20251001` | 读 | 读 |
| `ALB_OPENAI_COMPAT_URL` | OpenAI 兼容服务地址,写到 `/v1` | `http://localhost:8080/v1` | 读 | **不读** |
| `ALB_OPENAI_COMPAT_KEY` | OpenAI 兼容服务的 key;不设时读 `OPENAI_API_KEY` | 不设 | 读 | **不读** |
| `ALB_OPENAI_COMPAT_MODEL` | **无效**:报错提示里叫你设它,但代码不读,设了没用 | 无 | 不读 | 不读 |
| `ALB_AGENT_TOOL_RESULT_MAX_CHARS` | 一次工具结果最多给模型看多少字符,`0` 表示不截。只截模型那份,页面上显示全文 | 4000 | 读 | 读 |

代码:`src/alb/cli/chat_cli.py`、`src/alb/api/chat_route.py`、`src/alb/agent/backends/__init__.py`、`src/alb/agent/backends/ollama.py`、`src/alb/agent/backends/anthropic.py`、`src/alb/agent/backends/openai_compat.py`、`src/alb/agent/loop.py`。

要注意的行为:

- **`ALB_OLLAMA_MODEL` 对所有后端都生效。** `alb chat --model` 的环境变量就是 `ALB_OLLAMA_MODEL`(`chat_cli.py`);hub 在请求没带模型名时也取 `ALB_OLLAMA_MODEL`(`chat_route.py`)。所以设了它,再选 `anthropic` 或 `openai-compat`,发过去的是 Ollama 的模型名,`ALB_ANTHROPIC_MODEL` 不起作用。用别的后端时,用 `--model` 明确指定。
- `openai-compat` 后端没有默认模型名,必须用 `--model` 指定(或者设 `ALB_OLLAMA_MODEL`,见上一条)。
- hub 的网页助手选 `openai-compat` 时,地址和 key 只能用默认值(`http://localhost:8080/v1`、不带 key),因为 hub 不读 `ALB_OPENAI_COMPAT_URL` / `ALB_OPENAI_COMPAT_KEY`。
- hub 启动时会预热 `ALB_OLLAMA_MODEL` 指定的模型,`ALB_WARMUP=0` 关掉;没设 `ALB_OLLAMA_MODEL` 时不预热。
- 连接 Ollama 本身的等待是固定 10 秒,不受 `ALB_OLLAMA_TIMEOUT` 影响(`ollama.py` 的 `DEFAULT_CONNECT_TIMEOUT`)。
- key 不要写在命令行参数里,会进 shell 历史。用环境变量或 `.env.local`。

---

## 八、日志和工作目录

| 变量 | 作用 | 默认值 | 谁读 | 代码 |
|---|---|---|---|---|
| `ALB_WORKSPACE` | 工作目录:日志、截图、会话、profile、`bench/board.json` 都放这里 | 当前目录下有 `workspace/` 就用它,否则 `~/.alb-workspace` | CLI / MCP / hub | `src/alb/infra/workspace.py` |
| `ALB_LOG_LEVEL` | hub 的日志级别 | `INFO` | hub | `src/alb/api/log_setup.py` |

- 不设 `ALB_WORKSPACE` 时,工作目录跟着**进程的当前目录**变。在不同目录下运行 `alb`,会用到不同的工作目录,profile 也跟着变(profile 在 `<workspace>/profiles/` 下)。`alb-mcp` 的当前目录由启动它的 MCP 客户端决定。要固定就设 `ALB_WORKSPACE`。
- config.toml 的 `[workspace] root` **无效**:能解析,但决定工作目录的函数只看 `ALB_WORKSPACE`。
- Windows agent 的日志由 agent.conf 的 `log_file` 决定,见 6.1。

---

## 九、配置文件逐项

### 9.1 config.toml

位置:`ALB_CONFIG` 指定的路径;没设时是 `$XDG_CONFIG_HOME/alb/config.toml`;`XDG_CONFIG_HOME` 也没设时是 `~/.config/alb/config.toml`。代码:`src/alb/infra/config.py`。

| 位置 | 键 | 作用 | 默认值 |
|---|---|---|---|
| 顶层 | `default_profile` | 没设 `ALB_PROFILE` 时用哪个 profile | `default` |
| `[workspace]` | `root` | **无效** | |
| `[transport.adb]` | `bin_path` | adb 程序路径,`ALB_ADB_PATH` 优先 | `adb` |
| `[transport.adb]` | `server_socket` | adb server 地址,优先级最高,见 3.4 | 不设 |
| `[transport.ssh]` | `default_user` / `default_port` / `key_path` / `known_hosts` / `connect_timeout` | 见第四节 | `root` / 22 / 不设 / 不设 / 15 |
| `[transport.serial]` | `default_baud` / `default_tcp_host` / `default_tcp_port` / `handshake_timeout` | 见 2.4 | 115200 / `localhost` / 9001 / 2.0 |
| `[transport.serial]` | `pty_link_dir` | **无效** | |
| `[transport.serial.prompts]` | `prompts` 表里的 11 个状态名 | 见 2.4 | |
| `[permissions]` | `mode` / `ask_on_ambiguous` / `log_denied` / `allow` / `deny` | **无效**,见下 | |

读文件时的两种失败,表现不一样:

- **TOML 语法错**:整份文件被忽略,全部用默认值,**不报错**(`_load_config_file` 吞掉 `TOMLDecodeError`)。配置改了却像没改,先查语法。`alb doctor` 的 config 一层会显示读的是哪个文件。
- **键名写错**(例如在 `[transport.serial]` 下写 `device = "/dev/ttyUSB0"`):读配置时报 `TypeError: … unexpected keyword argument 'device'`,读配置的命令都会失败。

`[permissions]` 无效的原因:板子上的命令一律放行(2026-10-23 定,见 [`permissions.md`](./permissions.md)),`src/alb/infra/permissions.py` 的检查总是返回 allow,没有代码读这一节。

### 9.2 profile:`<workspace>/profiles/<name>.toml`

用哪个 profile:`ALB_PROFILE`,没设就用 config.toml 的 `default_profile`。名字只能是字母数字开头、由字母数字 `_` `-` 组成、最长 64 个字符。文件不存在不报错,当成空 profile。代码:`src/alb/infra/config.py`。

| 位置 | 键 | 作用 | 默认值 |
|---|---|---|---|
| `[profile]` | `name` | 显示用的名字 | 文件名 |
| `[profile]` | `primary_transport` | 默认传输:`adb` / `ssh` / `serial` | `adb` |
| `[[devices]]` | `serial` | 必填,没有这个键的条目被跳过;值本身没有代码用 | |
| `[[devices]]` | `transport` | 和 `ssh_host` 配合用,见下 | `adb` |
| `[[devices]]` | `ssh_host` | ssh 传输没设 `ALB_SSH_HOST` 时,用第一条 `transport = "ssh"` 的这个值 | |
| `[[devices]]` | `alias` | **无效** | |
| `[[devices]]` | `ssh_port` | **无效**,端口见第四节 | |
| `[[devices]]` | `serial_tcp_port` | **无效**,串口端点见 2.2 | |
| `[permissions]` | 同 config.toml | **无效** | |

- `[[devices]]` 目前只有上面那一个用处。alb 不按这张表选 adb 设备,也不按它选串口。
- `alb --profile <name>` 这个参数**只影响 `alb status` 显示的 profile 名**,不影响用哪个传输(`src/alb/cli/main.py` 只在 `status` 里用它;`src/alb/transport/factory.py` 建传输时只看 `ALB_PROFILE`)。要切 profile,设 `ALB_PROFILE`。
- profile 的 TOML 语法错也是不报错、当成空 profile;键名写错同样报 `TypeError`。

### 9.3 boards.toml

hub 的板子表,`ALB_BENCH_BOARDS` 指向它,模板是 `deploy/hub/boards.toml.example`。代码:`src/alb/remote/bench_board.py`。

```toml
[boards.board-a]
serial_com = "COM5"
serial_baud = 1500000
note = "左边排针上的调试串口"
```

| 键 | 作用 | 取值 |
|---|---|---|
| `[boards.<name>]` | 一块板子。名字 1 到 32 个字符,只能是 `a-z 0-9 _ -`,字母或数字开头 | 必须至少一块 |
| `serial_com` | 台架主机上的串口名 | 例如 `COM5` |
| `serial_baud` | 波特率 | 300 到 12000000 的整数 |
| `note` | 备注,只用来显示 | 可不写 |

写了别的键、名字或取值不合规定,会报 `BENCH_BOARDS_INVALID`,并指出是哪个文件的哪一条。agent.conf 里 `flash_partitions.<board>` / `fastboot_product.<board>` 的 `<board>` 用的就是这里的名字。

### 9.4 hub.env

模板 `deploy/hub/hub.env.example`。它是 shell 脚本格式,由 `run-hub.sh` 用 `. hub.env` 读入,所以含空格或 `$` 的值要加引号。能写的变量见第五节和 2.3。令牌还是 `change-me` 时 `run-hub.sh` 会打警告。多人共用的机器上执行 `chmod 600 hub.env`。

### 9.5 .env.local / .env

模板是仓库根目录的 `.env.example`。代码:`src/alb/infra/env_loader.py`。

- 只有 `alb` 和 `alb-api` 读,`alb-mcp` 不读。MCP 用的变量写在 MCP 客户端配置里(写法见 [`llm-integration.md`](./llm-integration.md))。
- 一行一个 `KEY=VALUE`,可以带 `export ` 前缀,值两边的一对引号会去掉。
- `#` 只在行首算注释。`KEY=1 # 说明` 的值是 `1 # 说明`。
- shell 里已经有的变量不会被覆盖。
- `.env.local` 不进 git,放你自己的值;`.env` 放大家共用的默认值。

---

## 十、设了无效的项

下面这些写了不会报错,但没有任何效果。

| 项 | 实际情况 | 改用 |
|---|---|---|
| config.toml `[workspace] root` | 解析了,决定工作目录的函数不看它 | `ALB_WORKSPACE` |
| config.toml / profile 的 `[permissions]` | 没有代码读,板子命令一律放行 | 无 |
| `[transport.serial] pty_link_dir` | 没有代码读 | 无 |
| profile `[[devices]]` 的 `alias`、`ssh_port`、`serial_tcp_port` | 没有代码读 | `ALB_SSH_PORT`、`ALB_SERIAL_TCP` |
| `alb setup serial --device … --save` 存设备路径 | 只存了波特率 | `ALB_SERIAL_DEVICE` |
| `alb --profile <name>` 切换传输 | 只影响 `alb status` 的显示 | `ALB_PROFILE` |
| `ALB_OPENAI_COMPAT_MODEL` | 只出现在报错提示里,代码不读 | `--model` |
| `ALB_ANTHROPIC_MODEL`(同时设了 `ALB_OLLAMA_MODEL` 时) | 被 `ALB_OLLAMA_MODEL` 盖掉 | `--model` |
| hub 的 `ALB_OPENAI_COMPAT_URL` / `ALB_OPENAI_COMPAT_KEY` | hub 不读,只有 `alb chat` 读 | 无 |
| `ALB_SSH_KNOWN_HOSTS=""` 关掉密钥校验 | 空字符串被当成没设 | config.toml `known_hosts = ""` |
| `ALB_TRANSPORT=hybrid` | 没有这种传输,报 `Unknown transport: hybrid` | `adb` / `ssh` / `serial` |
| hub 的 `ALB_SERIAL_FORWARD_PORT` 改了以后,客户端什么都不设 | 客户端仍连 9001 | 客户端设 `ALB_SERIAL_TCP` |

---

## 十一、AI 怎么查配置

给通过 MCP 用 alb 的 AI:

1. 先调 `alb_status`。adb 传输会返回 `server_socket`(连的是哪个 adb server)和 `server_socket_source`(这个地址来自哪:`config` / `env` / `hub` / `default`)。来源不是 `hub`、而设备列表又是空的,先按第 3.4 节查是不是连错了 server;alb 能看出矛盾时,结果里还有一条 `hint` 直接说明。
2. 工具失败时,看返回的 `error.suggestion`,里面写了该设哪个变量或跑哪条命令。查错误码用 `alb_describe_errors`。
3. 环境问题让人跑 `alb doctor`:它列出 `ALB_WORKSPACE`、`ADB_SERVER_SOCKET`、`ALB_CONFIG`、`ALB_PROFILE` 的值,读的是哪份 config.toml,用的是哪个 profile。
4. 要知道某个变量是干什么的、谁读它,查本页。记住 `alb-mcp` 不读 `.env.local`,它的环境变量只来自 MCP 客户端配置。

---

## 附:不以 ALB_ 开头、但会影响 alb 的变量

| 变量 | 作用 | 代码 |
|---|---|---|
| `ADB_SERVER_SOCKET` | adb 自己的变量,指定 adb server 地址,见 3.4 | `src/alb/infra/adb_endpoint.py`、`src/alb/transport/adb.py` |
| `XDG_CONFIG_HOME` | config.toml 的默认目录,见 9.1 | `src/alb/infra/config.py` |
| `ANTHROPIC_API_KEY` | `ALB_ANTHROPIC_KEY` 没设时用它 | `src/alb/agent/backends/__init__.py`、`src/alb/cli/chat_cli.py` |
| `OPENAI_API_KEY` | `ALB_OPENAI_COMPAT_KEY` 没设时 `alb chat` 用它 | `src/alb/cli/chat_cli.py` |

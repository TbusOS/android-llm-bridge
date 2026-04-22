---
title: 远程板子调试打通指南（Windows → Xshell 隧道 → Linux 上的 alb）
type: setup-guide
audience: dev
created: 2026-04-17
updated: 2026-04-17
tags: [setup, board, adb, uart, xshell, ssh-tunnel]
---

# 远程板子调试打通指南

> **场景**：Android 板子用 USB + UART 连在 Windows 上，你想在一台 Linux 服务器（通过 xshell/ssh 登录）上跑 `alb` / Claude Code / 其他 MCP 客户端来操作它。板子**完全无需联网**。

本文覆盖 **ADB + UART 两条路径同时打通**，架构统一为 **Xshell 反向隧道 + Windows 端桥**。2026-04-17 首次端到端打通一块高速 UART (1500000 baud) 的 Android 板子。

---

## 一、整体架构

```
┌──────────────────────┐   Xshell SSH + 2 条反向隧道    ┌──────────────────┐        USB          ┌─────────────┐
│   Linux 服务器        │ ◄────────────────────────────► │  Windows 电脑    │ ◄───── USB ───────► │             │
│  (alb / Claude Code) │   -R 5037   (adb server)      │                 │        USB 转串口    │ Android 板子 │
│                      │   -R 19001  (serial bridge)   │  adb server     │ ◄───── UART ──────► │             │
│                      │                               │  bridge.py      │                      │             │
└──────────────────────┘                               └──────────────────┘                      └─────────────┘

Linux 上 alb 的实际数据流：
  alb shell …    → tcp:localhost:5037  → SSH -R → Windows:5037  → adb server → USB → 板子
  alb serial …   → tcp:localhost:19001 → SSH -R → Windows:19001 → bridge.py  → COMxx → 板子 UART
```

**关键点**：两条隧道在同一个 Xshell 会话里并存。会话断开两条都断，重连都恢复。

> **端口选择说明**：UART 桥默认用 `19001` 而不是 `9001` —— Windows 10 开了 Hyper-V / WSL2 时 WinNAT 会把 9001 附近保留为动态端口，非管理员 bind 直接挂 `WinError 10013`。19001 避开保留段。Linux 上两个端口都能 bind，所以脚本和文档统一用 19001。

---

## 二、Windows 端一次性配置

### 2.1 ADB（如果已跑通可跳过）

按原始 [ADB via SSH Tunnel Guide](../../adb-via-ssh-tunnel-guide.md) 配。核心要点：

- Windows 装 platform-tools，PATH 加上去
- 板子开启 USB 调试 + USB 数据线连 Windows
- `adb devices` 在 cmd 能看到设备
- adb server 默认监听 `127.0.0.1:5037`，**无需手动启动**

### 2.2 UART 串口桥

#### 前置：找到 COM 口号 + 波特率

- **COM 口号**：设备管理器 → 端口 (COM 和 LPT)，插上 USB 转串口能看到新的 `COMxx`
- **高速 UART 常用波特率**：`1500000`（1.5 Mbaud，多见于中高端 ARM SoC）；大多数平台 `115200`

#### 步骤 1：装 Python 3 + pyserial

从 https://www.python.org/downloads/ 下载 Windows installer。

**推荐 Python 3.12**（最稳）；Python 3.13 也 OK；**Python 3.14 刚发布（2025-10），和 pyserial 某些版本兼容性未完全验证**，遇到奇怪错误可降到 3.12。

安装时**必须勾 ☑ "Add Python to PATH"**，否则 cmd 找不到 python。

cmd 验证 + 装 pyserial：
```cmd
python --version
pip install pyserial
```

> **pyserial 装在 Windows，不在 Linux**。Linux 上 alb 用的是 pyserial-asyncio，项目依赖里已经有，不需要你手动装。

#### 步骤 2：下载桥脚本

从 GitHub raw 拉（桥是开源中立脚本，没硬编码真实 IP）：
```cmd
curl -o windows_serial_bridge.py https://raw.githubusercontent.com/TbusOS/android-llm-bridge/main/scripts/windows_serial_bridge.py
```

或浏览器打开这个 URL 右键另存。建议放固定位置比如 `C:\tools\windows_serial_bridge.py`，方便后续开机自启引用。

#### 步骤 3：启动桥

**新版默认值**（`--host 127.0.0.1 --port 19001`）已经对应反向隧道场景，所以命令可以很短：

```cmd
python windows_serial_bridge.py --com COM27 --baud 1500000
```

参数替换：
- `--com`：你的实际 COM 号
- `--baud`：板子 UART 波特率（高速板常用 `1500000`，多数 SoC 是 `115200`）

成功输出：
```
[bridge] opened COM27 @ 1500000 baud
[bridge] listening on 127.0.0.1:19001  (Ctrl-C to quit)
```

**这个 cmd / PowerShell 窗口保持开着**（关掉就断了）。开机自启方案见 §六。

#### 步骤 4（重要）：Xshell 串口控制台和 Python 桥不能同时开

Windows 串口是**严格独占**：一个 COM 口同时只能被一个程序打开。要么 Xshell 串口控制台 tab 开着（人眼直接看板子日志），要么 Python 桥开着（Linux 上 alb 控制），**二选一**。

要从 A 切到 B：关掉当前那边 → 等 3 秒让 Windows 驱动释放 COM 口 → 开另一边。

---

## 三、打两条反向隧道（两种方法任选）

两条隧道都要：

| 用途 | 方向 | Windows | Linux |
|---|---|---|---|
| ADB | Remote (-R) | :5037 | :5037 |
| Serial | Remote (-R) | :19001 | :19001 |

⚠️ **一定是 Remote（-R）不是 Local（-L）** —— 让 Linux 访问 Windows 上的服务。

### 方法 A（推荐 · 最简）：PowerShell 原生 ssh，一行搞定

Windows 10 build 1809+ / Windows 11 自带 OpenSSH。一条命令开会话 + 同时建两条反向隧道：

```powershell
ssh -R 5037:localhost:5037 -R 19001:localhost:19001 <user>@<linux-host>
```

好处：

- 不用配 GUI，参数写在命令里看得见
- 命令历史能记住，下次 `↑` 一下就行
- 没有"添加规则但没重连"这种静默失效的坑
- 任何带 OpenSSH 的 Windows 都能用，不依赖第三方终端

保持这个 PowerShell 窗口**不关**（关掉隧道就断）。想多开几个 Linux 终端？再开别的 PowerShell 继续 ssh（不带 `-R`）或者直接用 tmux。

### 方法 B：Xshell GUI 配置

会话属性 → 连接 → SSH → 隧道 → **添加**两条：

| 规则 | 类型 | 源主机 | 侦听端口 | 目标主机 | 目标端口 | 描述 |
|---|---|---|---|---|---|---|
| 1 (ADB) | **Remote** | localhost | 5037 | localhost | 5037 | adb-tunnel |
| 2 (Serial) | **Remote** | localhost | 19001 | localhost | 19001 | serial-tunnel |

保存 → **断开当前会话 → 重新 ssh 上 Linux**（这一步必须重连，隧道才生效）。

### 验证隧道真活着（Linux 侧必须看到两个 listener）

```bash
ss -tln | grep -E ":5037|:19001"
# 预期：
#   LISTEN  127.0.0.1:5037
#   LISTEN  127.0.0.1:19001
```

**如果只看到其中一个或都看不到**：方法 A 检查命令有没有打全；方法 B 检查规则类型（必须 Remote）+ 有没有重连会话。

---

## 四、Linux 服务器端配置

### 4.1 装依赖（仅首次）

```bash
# picocom + socat：alb serial 交互终端需要的工具（capture 和 shell 不强制要，但装上没坏处）
sudo apt install -y picocom socat
```

### 4.2 装 Linux 端 adb（如果没装）

```bash
cd ~
wget https://dl.google.com/android/repository/platform-tools-latest-linux.zip
unzip platform-tools-latest-linux.zip
echo 'export PATH=$HOME/platform-tools:$PATH' >> ~/.bashrc
```

### 4.3 环境变量（写 `~/.bashrc`）

```bash
# ADB 通过反向隧道走 Windows adb server
export ADB_SERVER_SOCKET=tcp:localhost:5037
```

`source ~/.bashrc` 或重开终端生效。

### 4.4 全局安装 alb（推荐）

项目已支持 `uv tool install --editable`，装完 `alb` / `alb-mcp` / `alb-api` 全局可用：

```bash
# 假设 alb 项目 clone 到 ~/android-llm-bridge
uv tool install --editable ~/android-llm-bridge

# 验证
which alb             # 应输出 ~/.local/bin/alb
alb --help
which alb-mcp alb-api
```

`--editable` 的好处：你修改 alb 源码后，全局命令立即反映新改动，不用重装。

> 如果没有 uv，也可以 `pip install --user -e ~/android-llm-bridge`，同效果。

### 4.5 配 `~/.config/alb/config.toml`（UART 端口持久化）

alb 的 `alb setup serial --tcp-port 19001` 只做**即时检查**，不落盘到配置。要让后续所有 `alb serial capture / shell / send / health` 命令用 19001 / 1500000 作为默认，写 config.toml：

```bash
mkdir -p ~/.config/alb
cat > ~/.config/alb/config.toml <<'EOF'
default_profile = "default"

[transport.serial]
default_tcp_host = "localhost"
default_tcp_port = 19001
default_baud = 1500000
EOF

# 验证 alb 读到了
python3 -c "from alb.infra.config import load_config; print(load_config().serial)"
# 期望: SerialConfig(default_baud=1500000, default_tcp_host='localhost', default_tcp_port=19001, ...)
```

> **TOML schema 注意**：section 名是 `[transport.serial]`（嵌套在 `transport` 下），**不是**顶层 `[serial]`。写错了 alb 会静默用默认值。

### 4.6 验证两条隧道

```bash
ss -tln | grep -E '5037|19001'
# 期望看到 2-3 行 LISTEN：
#   LISTEN 0 128  127.0.0.1:5037
#   LISTEN 0 128  127.0.0.1:19001
#   LISTEN 0 128      [::1]:19001       ← IPv6，正常
```

**都没有**：Xshell 隧道没生效，回 §三 检查。
**只有 5037 没 19001**：Windows 桥没启动或端口填错。
**有 19001 但 `nc localhost 19001` 连不上**：Windows 桥连不上 COM 口（见 §七 常见问题 5）。

### 4.7 alb setup 检查

```bash
alb setup adb
# 应看到板子 serial number

alb setup serial --tcp-host localhost --tcp-port 19001 --baud 1500000
# 应看到 "serial / method G setup: Serial ready"
# picocom / socat 缺失是 optional，不影响核心功能
```

---

## 五、闭环验证（两路都通）

```bash
# === ADB 路径 ===
alb shell 'getprop ro.product.model'         # 打印板子型号
alb shell 'getprop ro.build.version.release' # Android 版本
alb logcat --duration 10 --save              # 抓 10 秒日志到 workspace/

# === UART 路径 ===
alb serial health                            # 看连接状态 + endpoint + baud
alb serial capture --duration 30             # 抓 30 秒 UART 字节流到 workspace/
# 板子此刻静默？按一下 reset → capture 瞬间能抓到完整 u-boot → kernel → init 日志

# === 救砖组合 ===
alb shell 'reboot' &                         # 走 ADB 触发重启
alb serial capture --duration 30             # 立即切 UART 抓完整启动过程
```

### 预期捕获样例（某 ARM 安卓板启动日志片段）

```
U-Boot SPL 2017.09-... (Jun 09 2025 - 11:08:59), fwver: v1.08
DDR ... LPDDR5, 2736MHz
channel[0] BW=16 Col=10 Bk=16 CS0 Row=16 CS1 Row=16 Die BW=16 Size=4096MB
...
Trying fit image at 0x4000 sector
## Checking atf-1 ... sha256(...) + OK
## Checking uboot ... sha256(...) + OK
## Checking fdt ... + OK
## Checking optee ... + OK
Jumping to U-Boot(0x40200000) via ARM Trust...
```

---

## 六、Windows 桥做成开机自启（可选）

手动每次 cmd 跑容易忘。做成任务计划程序：

### 方案 A：任务计划程序（推荐）

1. Win + R → `taskschd.msc`
2. 创建基本任务 → 名称 `serial-bridge` → 触发器 "当计算机启动时"
3. 操作："启动程序"：
   - **程序**：`C:\Users\<你>\AppData\Local\Programs\Python\Python3xx\python.exe`
     （用 `where python` 查实际路径）
   - **参数**：`C:\tools\windows_serial_bridge.py --com COM27 --baud 1500000`
   - **起始于**：`C:\tools\`
4. 右键任务 → 属性 → 勾 "不管用户是否登录都要运行"

### 方案 B：NSSM 做成 Windows 服务

```cmd
nssm install alb-serial-bridge C:\Python3xx\python.exe "C:\tools\windows_serial_bridge.py --com COM27 --baud 1500000"
nssm start alb-serial-bridge
```

---

## 七、常见问题（踩过的坑）

### 问题 1：Windows 桥报 `WinError 10013: 以一种访问权限不允许的方式做了一个访问套接字的尝试`

**根因**：
- bind `0.0.0.0` 非管理员被拒（Windows 10+ 常见）
- 或端口 `9001` 在 Hyper-V / WinNAT 保留段里

**修复**：新版桥默认 `--host 127.0.0.1 --port 19001` 两者都规避了。如果你用老版脚本，**curl 重下新版**。

如果你有其他目的想 bind 别的端口：
```cmd
netsh interface ipv4 show excludedportrange protocol=tcp  :: 查 Windows 保留段
```
避开保留段 + 10000 以上即可。

### 问题 2：Windows 桥报 `could not open port 'COM27': PermissionError(13, '拒绝访问'`

**根因**：COM27 被别的程序占了。Windows 串口严格独占。

**排查顺序**：
1. **Xshell 里有没有开串口控制台 tab 连着 COM27？** 有的话关掉
2. **PuTTY / 串口助手 / 之前的 python 桥进程残留？**
   ```powershell
   Get-Process python -EA SilentlyContinue | Stop-Process -Force
   Start-Sleep 3
   ```
3. **刚关桥立刻重开**？Windows 驱动 linger，等 3-10 秒再开
4. **都不行** → 拔插 USB 转串口线一次（强制驱动 reset，100% 管用）

### 问题 3：`ss -tln | grep 19001` 没有 LISTEN

- Xshell 隧道没生效 → 断开重连会话
- Xshell 类型选成了 Local（-L）→ 改成 Remote（-R）
- Windows 桥没启动

### 问题 4：`nc localhost 19001` 能连但敲字符无响应

- COM 号错了（设备管理器再确认一次）
- 波特率错了（高速板常用 1500000，别的 SoC 可能是 115200 / 921600 / 9600）
- 板子此刻挂了 / 板子 UART RX 未接 / TX RX 接反
- Android 起来后 UART console **默认静默** —— 这是正常现象。按板子 reset 键 / 断电上电再 capture，启动日志会刷屏

### 问题 5：`alb serial capture --duration 30` 跑完 0 字节 0 errors

看问题 4 最后一条。**板子 Android 正常运行时 UART 默认不说话**，要触发（reboot / kernel log / 输入命令敲到 shell）才有输出。

这**不是桥 bug，不是隧道 bug** —— 先验证：让板子 reset，30s capture 抓到几 KB 启动日志就证明通路 OK。

### 问题 6：`alb serial send` 后立刻 `alb serial capture` 报 `Connection reset by peer`

这是 alb 内部每次 API 都 open/close 连接造成的 race condition，不是桥问题。workaround：两次命令之间 `sleep 1`，或者用 `alb serial shell '<cmd>'` 单次连接搞定 send+receive。

### 问题 7：`alb: command not found`

没全局安装。见 §4.4，或者 `cd ~/android-llm-bridge && uv run alb ...`。

### 问题 8：`alb shell` 报 `device not found`

```bash
echo $ADB_SERVER_SOCKET          # 必须输出 tcp:localhost:5037
ss -tln | grep 5037               # 5037 必须 LISTEN
adb devices                       # 应看到板子
```

都对的话，**Windows cmd 里 `adb devices` 再确认一下板子状态**。板子 USB 调试可能断授权（重启后需要在板子上点"允许此电脑调试"）。

### 问题 9：USB 拔插过 / 板子重启后 adb 卡住

```bash
adb kill-server                   # Linux 端清 client 缓存
# Windows cmd 里跑 adb devices 让 Windows adb server 重启
# Linux 再试
```

### 问题 10：板子 UART 能看到输出但敲回车没响应

```bash
# 换行映射问题
picocom -b 1500000 --omap crcrlf /tmp/ttyV0
```

或者板子没跑 getty，UART console 只读不交互。看板子 init.rc / `/proc/cmdline` 的 `console=ttyS*` 配置。

### 问题 11：两块板子同时调

两条桥不同端口 + 两条隧道：

Windows:
```cmd
python windows_serial_bridge.py --com COM27 --baud 1500000 --port 19001
python windows_serial_bridge.py --com COM28 --baud 1500000 --port 19002
```

Xshell 隧道 -R 19001 + -R 19002。

Linux alb profile 里两套 device 各写自己的 `serial_tcp_port`。

---

## 八、接入 Claude Code / Cursor / 其他 MCP 客户端

> 待板子 ADB 路径联调完毕后补写本节。届时步骤大致：
>
> 1. `alb-mcp` 全局命令已可直接作为 MCP server 启动（stdio 传输）
> 2. Claude Code: `claude mcp add alb /home/<you>/.local/bin/alb-mcp`
>    或编辑 Claude Code settings.json 的 `mcpServers.alb`
> 3. Cursor: settings → MCP → 添加 `alb-mcp` 命令
> 4. 在 Claude 里输入 "帮我看一下板子的 Android 版本" → 自动调 `alb_shell getprop ro.build.version.release`
> 5. 串口相关 tool：`alb_uart_capture` / `alb_uart_send` / `alb_uart_watch_panic`

---

## 九、维护 / 变更

| 日期 | 变更 | 作者 |
|---|---|---|
| 2026-04-17 | 初稿 — 覆盖 Android 板（USB + 1500000 baud UART）+ Windows Python 桥 + Xshell 隧道全流程 | sky |
| 2026-04-17 | 端到端打通后补坑点：Windows 9001 保留 → 19001；串口独占说明；config.toml schema（`[transport.serial]`）；全局安装 alb；常见问题扩到 11 条 | sky |

## 参考

- [ADB via SSH Tunnel Guide](../../adb-via-ssh-tunnel-guide.md) — ADB 侧原版（家目录，仅个人笔记）
- [docs/methods/07-uart-serial.md](methods/07-uart-serial.md) — UART 方案方法论
- [scripts/windows_serial_bridge.py](../scripts/windows_serial_bridge.py) — 本文用的 Python 桥

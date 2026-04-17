---
title: 远程板子调试打通指南（Windows → Xshell 隧道 → Linux 上的 alb）
type: setup-guide
audience: dev
created: 2026-04-17
updated: 2026-04-17
tags: [setup, rk3576, adb, uart, xshell, ssh-tunnel]
---

# 远程板子调试打通指南

> **场景**：Android 板子用 USB + UART 连在 Windows 上，但你想在一台 Linux 服务器（通过 xshell/ssh 登录）上跑 `alb` / Claude Code / 其他 MCP 客户端来操作它。板子**完全无需联网**。

本文覆盖 **ADB + UART 两条路径同时打通**，架构统一为 **Xshell 反向隧道 + 各端口桥**。

---

## 一、整体架构

```
┌──────────────────────┐   Xshell SSH + 2 条反向隧道   ┌──────────────────┐        USB           ┌─────────────┐
│   Linux 服务器        │ ◄──────────────────────────► │  Windows 电脑    │ ◄──────── USB ──────► │             │
│  (alb / Claude Code) │   -R 5037  (adb server)      │                 │        USB 转串口      │ Android 板子│
│                      │   -R 9001  (serial bridge)   │  adb server     │ ◄──── UART ────────► │  (RK3576)   │
│                      │                              │  bridge.py      │                       │             │
└──────────────────────┘                              └──────────────────┘                       └─────────────┘

Linux 上 alb 的实际数据流：
  alb shell …    → tcp:localhost:5037 → SSH -R → Windows:5037  → adb server → USB → 板子
  alb serial …   → tcp:localhost:9001 → SSH -R → Windows:9001  → bridge.py  → COMxx → 板子 UART
```

**关键点**：两条隧道在同一个 Xshell 会话里并存，会话断开两条都重连。

---

## 二、Windows 端一次性配置

### 2.1 ADB（如果已跑通可跳过）

按原有 [ADB via SSH Tunnel Guide](../../adb-via-ssh-tunnel-guide.md) 配。核心要点：

- Windows 装 platform-tools，PATH 加上去
- 板子开 USB 调试 + USB 数据线连 Windows
- `adb devices` 在 cmd 能看到设备
- adb server 默认监听 `127.0.0.1:5037`，**无需手动启动**

### 2.2 UART 串口桥

#### 前置：找到 COM 口号 + 波特率

- **COM 口号**：设备管理器 → 端口 (COM 和 LPT)，插上 USB 转串口能看到新的 `COMxx`
- **RK3576 常用波特率**：`1500000`（1.5 Mbaud）；其他平台多数 `115200`

#### 步骤 1：装 Python 3 + pyserial

从 https://www.python.org/downloads/ 下载最新 Windows installer，**安装时必须勾 ☑ "Add Python to PATH"**。

装完 cmd 验证：
```cmd
python --version
pip install pyserial
```

#### 步骤 2：下载桥脚本

从 GitHub raw 拉：
```cmd
curl -o windows_serial_bridge.py https://raw.githubusercontent.com/TbusOS/android-llm-bridge/main/scripts/windows_serial_bridge.py
```

或浏览器打开这个 URL 右键另存。

#### 步骤 3：启动桥

```cmd
python windows_serial_bridge.py --com COM27 --baud 1500000 --port 9001
```

参数替换：
- `--com`：你的实际 COM 号（设备管理器查）
- `--baud`：板子 UART 波特率（RK3576: `1500000`）
- `--port`：TCP 监听端口，默认 `9001`，和 alb `SerialConfig.default_tcp_port` 对齐

成功输出：
```
[bridge] opened COM27 @ 1500000 baud
[bridge] listening on 0.0.0.0:9001  (Ctrl-C to quit)
```

**这个 cmd 窗口保持开着**（关掉就断了）。想开机自启把它塞任务计划程序或做成 Windows 服务（见 §五）。

---

## 三、Xshell 配 2 条反向隧道

会话属性 → 连接 → SSH → 隧道 → **添加**（已有 5037 的话，保留，再加 9001）：

| 规则 | 类型 | 源主机 | 侦听端口 | 目标主机 | 目标端口 | 描述 |
|---|---|---|---|---|---|---|
| 1 (ADB) | **Remote** | localhost | 5037 | localhost | 5037 | adb-tunnel |
| 2 (Serial) | **Remote** | localhost | 9001 | localhost | 9001 | serial-tunnel |

⚠️ **一定选 Remote（-R）不是 Local（-L）** — 反向隧道方向：让 Linux 能访问 Windows 上的服务。

保存 → **断开当前会话 → 重新 ssh 上 Linux**（这一步必须重连，隧道才生效）。

---

## 四、Linux 服务器端配置

### 4.1 装依赖（仅首次）

```bash
# 需要 sudo 一次装包
sudo apt install -y picocom socat
```

`picocom` 是串口终端，`socat` 把 TCP 端口桥到 PTY 设备（让 picocom 能以"普通串口"方式打开远端 TCP）。

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

### 4.4 验证两条隧道

```bash
ss -tln | grep -E '5037|9001'
# 必须看到：
#   LISTEN 0  128  127.0.0.1:5037
#   LISTEN 0  128  127.0.0.1:9001
```

**都没有**：Xshell 隧道没生效，回 §三 检查。
**只有 5037 没 9001**：Windows 桥没启动或端口填错。
**有 9001 但 `nc localhost 9001` 接不上**：Windows 桥连不上 COM 口，检查 COM 号 / 权限。

### 4.5 配 alb

```bash
# ADB 配置 + 验证
alb setup adb
# 应看到板子 serial number

# UART 配置 + 验证
alb setup serial --tcp-host localhost --tcp-port 9001 --baud 1500000
# 应看到 "serial / method G setup: OK"
```

---

## 五、闭环验证（两路都通）

```bash
# === ADB 路径 ===
alb shell 'getprop ro.product.model'          # 打印板子型号
alb shell 'getprop ro.build.version.release'  # Android 版本
alb logcat --duration 10 --save                # 10 秒日志抓到 workspace/

# === UART 路径 ===
alb serial connect                             # 进交互，看到 shell 提示符
                                               # 退出: Ctrl-A Ctrl-X
alb serial log --duration 10 --save            # 10 秒 UART 日志

# === 救砖场景联动 ===
alb shell 'reboot'                             # 走 adb
alb serial log --duration 30 --save            # 立即抓 UART 看完整启动（adb 此时断了，UART 仍通）
```

---

## 六、Windows 桥做成服务（可选）

默认是 cmd 窗口前台跑，关了就断。想开机自启：

### 方案 A：任务计划程序（最简单）

1. Win + R → `taskschd.msc`
2. 创建基本任务 → 名称 `serial-bridge` → 触发器 "当计算机启动时"
3. 操作："启动程序"
   - 程序：`C:\Python3\python.exe`（你 python 路径，`where python` 可查）
   - 参数：`C:\tools\windows_serial_bridge.py --com COM27 --baud 1500000 --port 9001`
   - 起始于：`C:\tools\`

### 方案 B：NSSM 做成 Windows 服务

```cmd
nssm install alb-serial-bridge C:\Python3\python.exe "C:\tools\windows_serial_bridge.py --com COM27 --baud 1500000 --port 9001"
nssm start alb-serial-bridge
```

---

## 七、常见问题

### 问题 1：`ss -tln | grep 9001` 没有 LISTEN

- Xshell 隧道没生效 → 断开重连会话
- Xshell 类型选成了 Local（-L）→ 改成 Remote（-R）
- Windows 桥没启动

### 问题 2：`nc localhost 9001` 能连但敲字符无响应

- COM 号错了（设备管理器再确认一次）
- 波特率错了（RK3576 常用 1500000，别的 SoC 可能是 115200/921600/9600）
- 板子此刻挂了 / UART 口被其他程序占了

### 问题 3：`alb serial connect` 报 "picocom not found"

```bash
sudo apt install -y picocom socat
```

### 问题 4：`alb shell` 报 `device not found`

```bash
echo $ADB_SERVER_SOCKET          # 必须输出 tcp:localhost:5037
ss -tln | grep 5037               # 5037 必须 LISTEN
adb devices                       # 应看到板子
```

都对的话，Windows 那边 cmd 跑 `adb devices` 再确认一下板子状态。

### 问题 4.5：USB 拔插过/板子重启后 adb 卡住

```bash
adb kill-server                   # Linux 端清 client 缓存
# Windows 端 cmd 跑 adb devices 让 server 重启
# Linux 再试
```

### 问题 5：Windows 桥报 `could not open port 'COM27'`

- COM 号对吗？（**设备管理器可能显示 COM27，但实际要写 COM27 全大写**）
- 有其他程序占了这个口？（比如之前开的串口终端、ser2net）
- 需要管理员权限？（一般不需要，但某些驱动要）

### 问题 6：板子 UART 能看到输出但发命令没反应

```bash
# 换行映射问题，picocom 加参数
picocom -b 1500000 --omap crcrlf /tmp/ttyV0
# 或者在板子 shell 里：stty -echo （看串口是否需要本地回显）
```

### 问题 7：两块板子同时调

两条桥不同端口 + 两条隧道：

Windows:
```cmd
python windows_serial_bridge.py --com COM27 --baud 1500000 --port 9001
python windows_serial_bridge.py --com COM28 --baud 1500000 --port 9002
```

Xshell 隧道 -R 9001 + -R 9002。

Linux alb profile 里两套 device 配不同的 `serial_tcp_port`。

---

## 八、接入 Claude Code（MCP 接入）

> 待板子调通后补写本节。届时步骤大致：
>
> 1. 在 95 上 `alb-mcp` 命令已可直接作为 MCP server 启动
> 2. Claude Code 的 settings.json 加 `mcpServers.alb` 条目，指向 `alb-mcp`
> 3. 在 Claude Code 里输入 "帮我看一下板子的 Android 版本" → 自动调 `alb_shell getprop ...`
> 4. 串口相关：`alb_uart_capture` / `alb_uart_send` / `alb_uart_watch_panic` 等

---

## 九、维护 / 变更

| 日期 | 变更 | 作者 |
|---|---|---|
| 2026-04-17 | 初稿 — 覆盖 RK3576 + Windows Python 桥 + Xshell 隧道全流程 | sky |

## 参考

- [ADB via SSH Tunnel Guide](../../adb-via-ssh-tunnel-guide.md) — ADB 侧原版
- [docs/methods/07-uart-serial.md](methods/07-uart-serial.md) — UART 方案全背景
- [scripts/windows_serial_bridge.py](../scripts/windows_serial_bridge.py) — 本文用的 Python 桥

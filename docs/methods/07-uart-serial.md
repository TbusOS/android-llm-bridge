---
title: 方案 G · UART 串口调试（含 SSH 反向隧道）
type: method-guide
created: 2026-04-15
updated: 2026-11-10
owner: sky
tags: [uart, serial, method-g, boot-log, u-boot, panic]
---

# 方案 G · UART 串口调试

> **最后底牌** —— 当 adb / sshd 都失效时（板子死机、kernel panic、起不来、u-boot 阶段），UART 是唯一能看到系统底层状态的通道。

---

## 一、独占能力（为什么必须有）

| 能力 | 为什么其他方案做不到 |
|-----|-------------------|
| **看完整 boot log（u-boot → kernel → init）** | adbd / sshd 都是用户态服务，系统起来才有 |
| **进 u-boot 命令行** | 必须在启动头 3 秒按键中断，只有串口能按键 |
| **改 u-boot 环境变量 / 刷分区** | 同上 |
| **看 kernel panic 完整 stack** | 系统挂时用户态通道全死 |
| **看 watchdog 重启原因** | 重启瞬间只有串口能捕获 |
| **板子完全死机** | adb / sshd 响应的是用户态，内核还活着时 UART 仍通 |
| **排查 adb / sshd 起不来的根因** | 得先看内核起了没 |

---

## 二、远程场景：从 Linux 到板子的每一段

这是"Linux 服务器 调试 ⇨ Windows 上的板子"场景，通过 SSH 反向隧道连起来：

```
┌─────────────────┐    Xshell SSH + 反向隧道    ┌─────────────────┐       USB 转串口         UART 口
│  Linux 服务器    │ ◄────────────────────────► │  Windows 电脑    │ ◄──────────────────────►  板子
│                 │    -R 9001:localhost:9001  │                 │
│  picocom        │                             │  ser2net        │
│  /tmp/ttyV0     │                             │  COM3 ←→ tcp:9001│
│       ↓         │                             │                 │
│  socat          │                             │                 │
│  pty ↔ tcp:9001 │                             │                 │
│                 │                             │                 │
└─────────────────┘                             └─────────────────┘


                        Linux 上 picocom 的实际数据流:
  用户输入字符 → picocom → /tmp/ttyV0 (PTY) → socat → tcp:localhost:9001
  → Xshell 反向隧道 → Windows tcp:9001 → ser2net → COM3 → USB 转串口 → 板子 UART
```

**关键点**：
- 板子只需 UART 引脚引出 + 一根 USB 转串口线
- 无需任何网络、USB 数据、OTG
- 板子完全没起来、只有 bootloader 时也能通

---

## 三、配置步骤

### 第 1 步 · Windows 端配置 ser2net（把 COM 口网络化）

**方案 A**：使用 `ser2net`（开源 / 通用）

1. 下载 Windows 版 ser2net：https://sourceforge.net/projects/ser2net/
2. 解压到 `C:\ser2net\`
3. 创建配置文件 `C:\ser2net\ser2net.conf`：
   ```
   # 端口:监听类型:超时:串口:波特率及参数:选项
   9001:raw:0:COM3:115200 8DATABITS NONE 1STOPBIT -RTSCTS
   ```
   > COM3 换成你板子实际的串口号（设备管理器里查）
   > 115200 是最常见波特率，Rockchip/MediaTek/Qualcomm 多数用这个
4. 启动：
   ```cmd
   ser2net.exe -c C:\ser2net\ser2net.conf -n
   ```

**方案 B**（备选）：`hub4com` + `com2tcp`

```cmd
com2tcp --baud 115200 \\.\COM3 9001
```

### 第 2 步 · Xshell 配反向隧道

在已有的 SSH 会话里加一条隧道（除了原来的 5037 adb 隧道外再加一条）：

1. 会话属性 → 连接 → SSH → **隧道**
2. 添加：

| 字段 | 值 |
|------|----|
| 类型 | **Remote (转发)** |
| 源主机 | `localhost` |
| 侦听端口 | `9001` |
| 目标主机 | `localhost` |
| 目标端口 | `9001` |
| 描述 | `serial-tunnel` |

3. 应用 → **重新连接会话**

### 第 3 步 · Linux 端装工具

```bash
# Ubuntu / Debian
sudo apt install picocom socat minicom   # minicom 备选
```

这三个都是可选的：alb 自己读写串口不需要它们，只有手动开交互终端（第 5 步）才用到。
`alb setup deps` 会列出缺了哪个、各发行版怎么装；它不会自动装这两个（要 sudo）。

### 第 4 步 · 验证隧道

```bash
# Linux 上
ss -tlnp | grep 9001
# 应看到 9001 在 LISTEN
# 没有 → Xshell 隧道没建好，回去检查

# 简单测试：
nc localhost 9001
# 此时你敲字符应该能发到板子（board 可能回显或没反应，取决于状态）
# Ctrl-C 退出
```

### 第 5 步 · 建立 PTY + 启动 picocom

**一次性手动模式**（理解用）：

```bash
# 启动 socat 把 TCP 端口桥到 PTY 设备
socat pty,link=/tmp/ttyV0,rawer tcp:localhost:9001 &
# 此时 /tmp/ttyV0 就是一个"假的"串口设备

# 用 picocom 连接 PTY
picocom -b 115200 /tmp/ttyV0
# 退出: Ctrl-A Ctrl-X
```

**用 alb**：alb 直接连 `localhost:9001`，不需要 socat / picocom。

```bash
alb setup serial                         # 检查 localhost:9001 在监听，并试着打开一次串口
alb setup serial --tcp-port 9002 --save  # 端口不是 9001：验证通过后写进 config.toml
alb serial health                        # 连得上吗，连的是哪个地址
alb serial status                        # 板子现在处在什么状态：shell / u-boot / 启动中 / panic
alb serial capture -d 60 -o ./uart.log   # 抓 60 秒串口输出
alb serial shell 'uname -a'              # 在串口 shell 里跑一条命令，等提示符，拿回输出
alb serial send 'reboot'                 # 只发字节（末尾加换行），不等输出
alb serial break-in --reboot adb         # 经 adb 重启，并在 bootloader 倒计时里按键停住
```

alb 没有交互式终端命令。要边看边敲，用 Web 界面的 UART 控制台，或者用上面的 socat + picocom。

alb 按这个顺序找串口（`src/alb/transport/factory.py`，serial 分支）：

1. 环境变量 `ALB_SERIAL_DEVICE`（本机串口设备，如 `/dev/ttyUSB0`）
2. 环境变量 `ALB_SERIAL_TCP`（`主机:端口`，如 `localhost:9002`）
3. `~/.config/alb/config.toml` 的 `[transport.serial]` 里的 `default_tcp_host` / `default_tcp_port`，
   默认 `localhost:9001`

`alb serial capture/send/shell/...` 的 `--device` 是给日志目录用的设备名（只能是字母、数字、`._:-`），
不是串口路径，`--device /dev/ttyUSB0` 会被拒绝。

波特率只对本机串口设备有效（取 `[transport.serial] default_baud`，默认 115200）。
走 TCP 时波特率由打开 COM 口的一方决定，也就是 Windows 上的 ser2net 配置。

---

## 四、核心使用场景

### 场景 1 · 看完整启动日志

抓取先起、复位后发 —— **顺序不能反**，bootrom / bootloader 阶段只出现一次，
错过就得再重启一遍。

```bash
# 终端 A：先起抓取（时长要盖住整个启动）
alb serial capture -d 180 -o ./boot.log

# 终端 B：抓取跑着的时候复位板子
alb serial shell 'reboot'      # 软复位
# 或者直接上电（硬复位场景下 B 什么都不用做）
```

应该看到：

```
U-Boot 2021.10 ...
DRAM:  2 GiB
Loading kernel...
[0.000000] Booting Linux on ...
init: Starting service 'adbd'
console:/ $
```

产物：`-o` 指定的路径；不给 `-o` 时落在 `workspace/logs/<ts>-uart.log`，
带 `--device <名字>` 时落在 `workspace/devices/<名字>/logs/<ts>-uart.log`。

**两条省时间的性质**：

- 抓取是**增量落盘**的，不必等满时长 —— 另开一个终端
  `tail -F ./boot.log | grep -m1 'Starting service .adbd.'` 就能在启动完成的那一刻返回。
- 抓取期间**可以同时**跑 `alb serial shell` / `alb serial send` / 开 Web UART 控制台，
  三者共用同一条链路，谁也不会把谁挤掉。

> **为什么需要专门说这条**：串口不是网络端口，操作系统对它是**独占打开**的 ——
> 一个进程拿着，第二个就打不开。所以 alb 的做法是 hub 侧只开一次串口，把收到的字节
> **复制给每个读方**，写入则合并回同一条链路（UART 物理上本来就是广播介质，
> 一份字节所有人都该看见）。
> 早期版本是「一条连接开一次串口」，于是抓取期间 `serial shell` 必然报
> `BOARD_UNREACHABLE`（其实板子是好的），`serial send` 更糟 —— 报"写入成功"但字节被丢弃。
> 见 [issue #4](https://github.com/TbusOS/android-llm-bridge/issues/4)。
> 如果现在还看到 `SERIAL_PORT_BUSY`，那是**别的程序**（终端软件、厂商烧录工具）
> 占着串口，不是 alb 自己在打架。

### 场景 2 · 进 u-boot 命令行

需要在启动头几秒中断：

```bash
alb serial break-in --reboot adb    # 经 adb 重启（会先问一次，-y 跳过）
alb serial break-in --reboot serial # 经串口 shell 重启
alb serial break-in                 # 不带 --reboot：命令提示你时，手动复位或上电
```

这个命令先开始读串口，再重启板子，所以不会错过倒计时。它的步骤：

1. 等 `--expect` 匹配到倒计时提示（默认 `Hit any key to stop autoboot|Press any key|autoboot in`）
2. 反复发 `--send` 里的按键（默认空格），直到行尾出现 `--prompt` 匹配的提示符（默认匹配 `=>` 或 `xxx>`）
3. 停在提示符时退出码 0。其他结果都是 1：没看到倒计时、看到了但板子继续进了内核、
   按了键但没等到提示符、连接出错；同时打印最后收到的几行
4. 整个过程的串口日志都会保存（`-o` 指定位置）

停住以后，用 `alb serial shell` 发 u-boot 命令：

```bash
alb serial shell 'printenv'            # 看环境变量
alb serial shell 'mmc part'            # 看分区表
alb serial shell 'setenv bootargs ...' # 改启动参数
alb serial shell 'saveenv'             # 保存
alb serial send 'boot'                 # 继续启动（不等提示符）
```

### 场景 3 · 抓 kernel panic

```bash
# 场景：reboot 后偶发 panic
# 终端 A：先抓（时长盖住复现过程）
alb serial capture -d 600 -o ./panic.log

# 终端 B：触发导致 panic 的操作，然后在抓到的日志里找
alb log search 'Kernel panic|Unable to handle kernel|Oops' --path ./panic.log
```

CLI 没有「抓到 panic 就停」的命令；`alb serial capture` 抓满时长才返回。
MCP tool `alb_uart_watch_panic` 抓完整段后报告有没有 panic / Oops，并带上崩溃前后的几行。

### 场景 4 · 板子完全不响应时救场

```bash
# adb / ssh 都没，只有串口：
alb serial status

# 状态是 shell_root / shell_user：
#   → 系统还在跑，只是 adb / sshd 挂了
#   → alb serial shell 'echo b > /proc/sysrq-trigger' 强制重启

# 状态是 idle（没有输出）或 panic：
#   → 内核挂了，只能硬件复位
#   → 先起 alb serial capture -d 180，再复位，抓启动过程
```

---

## 五、常见问题

### 问题 1 · `nc localhost 9001` 连不上

```bash
ss -tlnp | grep 9001
```

- 没 LISTEN → Xshell 反向隧道没生效，**断开重连会话**
- 有 LISTEN 但 nc 不通 → Windows 端 ser2net 没起，检查 Windows 任务管理器

### 问题 2 · 敲字符没反应 / 乱码

多半是波特率不对。`alb serial status` 报 `corrupted`（输出大多不可打印）就是这种情况。

```bash
# 走 ser2net / 隧道（TCP）：波特率在 Windows 上改，改 ser2net.conf 里那一行再重启 ser2net

# 本机串口设备：让 alb 依次试几种常见波特率，按可读程度排名
alb serial probe --device /dev/ttyUSB0
alb serial probe --device /dev/ttyUSB0 --rates 115200,1500000

# 试出来以后写进 config.toml（只写 default_baud，见问题 8）
alb setup serial --device /dev/ttyUSB0 --baud 1500000 --save
```

常见波特率：

| 波特率 | 典型场景 |
|-------|---------|
| 115200 | 通用（绝大多数 SoC） |
| 921600 | 高速 debug（MTK / 部分 Qualcomm） |
| 1500000 | 某些 SoC 高速 UART（Rockchip / MediaTek / Qualcomm 高端档） |
| 9600 | 老旧设备 |

### 问题 3 · 输入了命令，但板子看不到

```bash
# 可能需要换行/流控
picocom -b 115200 --omap crcrlf /tmp/ttyV0
# --omap: output mapping
# crcrlf: 把 CR (回车) 转成 CR+LF
```

### 问题 4 · u-boot 按 Ctrl-C 中断不了

有些 bootloader 需要：
- 按空格 (space)
- 按回车多次
- 按特定字符（如 `l` 代表 "load")
- 或 u-boot 编译时 `CONFIG_AUTOBOOT_STOP_STR=" "` 这种

查询板子文档 / `include/configs/<board>.h` 的 `CONFIG_AUTOBOOT_*` 宏。

用 `--send` 换按键，用 `--expect` 换倒计时提示的正则：

```bash
alb serial break-in --send '\x03'             # 发 Ctrl-C
alb serial break-in --send ' \r'              # 空格加回车
alb serial break-in --expect 'Autoboot in \d+' # 板子的提示文字不一样时
```


### 问题 5 · 多块板子同时调

每块板子一个 TCP 端口：Windows 端 ser2net 配多行（9001、9002 …），Xshell 也各加一条隧道。
alb 这边每条命令用 `ALB_SERIAL_TCP` 选端口：

```bash
ALB_SERIAL_TCP=localhost:9002 alb serial capture -d 60 --device board-b
```

`--device board-b` 只决定日志放进 `workspace/devices/board-b/`，不选端口。
走 alb hub + Windows agent 的台架，换板子用 `alb bench board use <名字>`，
见 [`../dial-home-runbook.md`](../dial-home-runbook.md)。

### 问题 6 · 串口日志太多怎么办

alb 不切片：`alb serial capture` 每次写一个文件，`-d` 最长 3600 秒。
长时间抓就分几次跑，然后检索，不要整个打开：

```bash
alb log search 'panic|Oops|watchdog' --since 2h   # 在 workspace 里所有日志中找
alb log tail <workspace 里的日志路径>              # 只看结尾一段（只收 workspace 内的路径）
```

### 问题 7 · 想用 minicom 不用 picocom

```bash
minicom -D /tmp/ttyV0 -b 115200
# 退出: Ctrl-A Q
```

picocom 更轻，推荐。minicom 交互更丰富。

### 问题 8 · 不走 Xshell，本地接 USB 转串口

如果 Linux 服务器本机接了 USB 转串口，跳过 ser2net + 反向隧道：

```bash
ls /dev/ttyUSB* /dev/ttyACM*                 # 找到设备
alb setup serial --device /dev/ttyUSB0       # 检查设备在、能打开
export ALB_SERIAL_DEVICE=/dev/ttyUSB0        # alb serial * 用这个设备
alb serial status
```

打不开多半是权限：把自己加进 `dialout` 组（`sudo usermod -aG dialout $USER`，重新登录生效）。

注意：`alb setup serial --device /dev/ttyUSB0 --save` 只把 `--baud` 写进 `config.toml`，
**不写设备路径**（`[transport.serial]` 没有存设备路径的字段），尽管 `--help` 说会写。
所以本机串口要靠 `ALB_SERIAL_DEVICE` 选，每个要用 alb 的 shell 都要设。

---

## 六、与其他方案协作

### 典型组合：A + G

一块板子同时插 USB（调 adb）+ UART（看 boot log）：

```bash
# Xshell 配两条反向隧道:
#   5037 → adb server
#   9001 → ser2net

alb setup adb       # 方案 A
alb setup serial    # 方案 G

# 使用：
alb devices                              # 走 adb
alb logcat -d 30                         # 走 adb
alb serial capture -d 180 -o ./boot.log  # 走串口，先起（另一个终端）
alb shell "reboot"                       # 走 adb，重启过程在 boot.log 里
```

### 组合：C + G

开发阶段用 sshd 快速开发，bringup 阶段用 UART 看底层：

```bash
# 新编的 kernel 推进去后 boot 失败，立刻切 UART：
alb serial capture -d 180 -o ./boot.log &           # 先起，切 G 看
alb --transport ssh shell "cp /tmp/Image /boot/Image"   # 用 C 推
alb --transport ssh shell "reboot"
```

---

## 七、硬件接线小科普

### UART 三线 / 五线

```
板子端（典型 SoC）          USB 转串口（如 CH340 / FT232）
┌──────────────────┐        ┌─────────────────────┐
│  UART_TX  ◄──────┼────────┼──►  RX              │
│  UART_RX  ──────►┼────────┼─◄──  TX             │
│  GND      ───────┼────────┼─────  GND           │
│  VCC (3.3V/1.8V) │    不接（外供电）              │
└──────────────────┘        └─────────────────────┘
                                  │
                                  └──── USB ──→ Windows
```

**陷阱**：
- TX 对 RX（交叉），**不是** TX 对 TX
- 电平：很多 SoC 是 1.8V，普通 USB 转串口（CH340）是 3.3V 或 5V，**电平不对会烧**
  - 1.8V 需要电平转换芯片（如 TXS0108）
  - 或专用 1.8V USB 转串口（如 FTDI C232HM-EDHSL-0 + 调电平）

### 一般哪里找 UART 引脚

- 开发板：直接标注 UART_TX / UART_RX / GND
- 量产板：调试点（test point）标 TP_TX / TP_RX，或 dongle 座
- 不确定：示波器 / 逻辑分析仪 + 复位看波形

---

## 八、性能与局限

| 项 | 值 |
|----|---|
| 带宽 | 115200 bps ≈ 11 KB/s，太小不适合传文件 |
| 延迟 | 典型 < 100ms（含隧道） |
| 稳定性 | 硬件直连极稳；隧道环境下看网络 |

**不能做**：
- 大文件传输（太慢）—— 用 A/C
- 视频 / 屏幕 —— 用 E scrcpy
- adb 独有的指令（`adb reverse` / `adb bugreport`）

**能做**：
- 一切 shell 级命令（但要手动）
- 启动阶段监控
- 内核 / u-boot 调试
- 救砖

---

## 九、alb 的 UART 相关能力

| 命令 / tool | 说明 |
|----------|------|
| `alb setup serial` | 检查 TCP 端口或本机设备，试着打开一次；`--save` 写进 config.toml（本机设备只写波特率） |
| `alb serial health` | 连得上吗，连的是哪个地址 |
| `alb serial status` | 识别板子当前状态（shell / u-boot / 启动中 / panic / 乱码 / 没输出），不发命令 |
| `alb serial capture -d N` | 抓 N 秒串口输出（1–3600），写进 workspace 或 `-o` |
| `alb serial send "<text>"` | 只发字节，末尾加换行（`--no-newline` 不加），不等输出 |
| `alb serial shell "<cmd>"` | 发一条命令，等提示符，返回输出 |
| `alb serial break-in` | 在 bootloader 倒计时里按键，停在 u-boot 提示符 |
| `alb serial probe --device /dev/ttyUSB0` | 本机串口设备：依次试几种波特率，按可读程度排名 |
| `alb serial learn` | 板子的 shell 提示符认不出来时，从实际输出推出一条正则（见下） |
| MCP: `alb_uart_capture` | 抓一段串口输出 |
| MCP: `alb_uart_send` | 发字节，不读回 |
| MCP: `alb_uart_shell` | 跑一条命令并返回输出 |
| MCP: `alb_uart_watch_panic` | 抓一段，报告有没有 kernel panic / Oops |

`alb serial learn` 只打印一段 TOML，不写文件。把它贴进 `~/.config/alb/config.toml`：

```toml
[transport.serial.prompts]
shell_root = "<learn 打印的正则>"
```

键名必须是内置状态名之一（如 `shell_root`、`shell_user`）。写错了，alb 建串口连接时直接报
`unknown serial state pattern key`（`src/alb/transport/serial_state.py`），不会悄悄退回默认值。

---

## 十、总结

- **如果你只有一种方案能做**：UART 救砖场景无替代
- **如果你要组合用**：A + G 是最推荐的组合
- **如果你在调 kernel / driver / bringup**：UART 是你的主力工具，adb 是辅助
- **生产调试**（起来的机器）：UART 退居备用，让 adb / sshd 主导

---

## 参考

- [方案 A · adb USB（含 SSH 反向隧道）](./01-ssh-tunnel-adb.md)
- [ser2net 项目](https://sourceforge.net/projects/ser2net/)
- [picocom GitHub](https://github.com/npat-efault/picocom)
- [socat man page](https://linux.die.net/man/1/socat)

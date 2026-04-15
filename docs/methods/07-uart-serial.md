---
title: 方案 G · UART 串口调试（含 SSH 反向隧道）
type: method-guide
created: 2026-04-15
updated: 2026-04-15
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

## 二、完整链路（远程场景）

这是"Linux 服务器 调试 ⇨ Windows 上的板子"场景，通过 SSH 反向隧道打通：

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

# 或用户目录（无 sudo）
# picocom 源码编译，或 alb install script 自动处理
```

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

**alb 封装版**（M1 实现后）：

```bash
alb setup serial                        # 一键检测并引导配置
alb serial connect                      # 自动建 PTY + 启动 picocom
alb serial connect --baud 921600        # 指定波特率
alb serial log --duration 60 --save     # 后台保存日志 60 秒
alb serial uboot                        # 进 u-boot 专用模式（持续发 Ctrl-C 中断启动）
```

---

## 四、核心使用场景

### 场景 1 · 看完整启动日志

```bash
# 1. 断电板子
# 2. 启动采集
alb serial log --save &

# 3. 上电
# 应该看到：
#   U-Boot 2021.10 ...
#   DRAM:  2 GiB
#   Loading kernel...
#   [0.000000] Booting Linux on ...
#   [0.123456] CPU: ARMv8 ...
#   ...
#   init: Starting service 'adbd'
#   android:/ $
```

产物：`workspace/devices/<serial>/logs/2026-04-15T10-30-00-uart-boot.log`

### 场景 2 · 进 u-boot 命令行

需要在启动头几秒中断：

```bash
alb serial uboot
# 这个命令会：
# 1. 连接串口
# 2. 持续发 Ctrl-C（或配置的中断按键，如空格/回车）
# 3. 看到 "=>" 提示符后进入交互

# u-boot 下可以：
=> printenv              # 看环境变量
=> setenv bootargs ...   # 改启动参数
=> saveenv               # 保存
=> boot                  # 继续启动
=> reset                 # 重启
=> mmc part              # 看分区表
```

### 场景 3 · 抓 kernel panic

```bash
# 场景：reboot 后偶发 panic
alb serial log --save --watch-panic &

# 触发导致 panic 的操作 ...
# 一旦捕获到 "Unable to handle kernel NULL pointer dereference" 等关键字
# 自动停止采集并保存到 workspace/panic-<time>.log
```

### 场景 4 · 板子完全不响应时救场

```bash
# adb / ssh 都没，只有串口：
alb serial connect

# 如果串口有输出：
#   → 系统还在跑，只是用户态挂了
#   → 尝试 echo b > /proc/sysrq-trigger 强制重启

# 如果串口也没输出：
#   → 内核挂了，只能硬件复位
#   → 复位后用 alb serial log 抓启动
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

```bash
# 可能：波特率不对
alb serial connect --baud 9600
alb serial connect --baud 115200     # 最常见
alb serial connect --baud 921600     # 高速
alb serial connect --baud 1500000    # 某些 Rockchip
```

常见波特率：

| 波特率 | 典型场景 |
|-------|---------|
| 115200 | 通用（绝大多数 SoC） |
| 921600 | 高速 debug（MTK / 部分 Qualcomm） |
| 1500000 | Rockchip rk3576 等高速 UART |
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

`alb serial uboot --interrupt-chars " \r\x03"` 可以自定义。

### 问题 5 · 两个方案同时用，PTY 设备怎么办

```bash
alb serial connect --device serial-main    # → /tmp/ttyV-main
alb serial connect --device serial-aux     # → /tmp/ttyV-aux
```

如果多块板子同时调：`--tcp-port 9002` 用不同端口，Windows 端也配多路 ser2net。

### 问题 6 · 串口日志太多怎么办

```bash
alb serial log --save --rotate-every 10MB --keep 10
# 日志文件按 10MB 切片，保留最新 10 个
```

或用 `alb log search` 全文检索（M2）。

### 问题 7 · 想用 minicom 不用 picocom

```bash
minicom -D /tmp/ttyV0 -b 115200
# 退出: Ctrl-A Q
```

picocom 更轻，推荐。minicom 交互更丰富。

### 问题 8 · 不走 Xshell，本地接 USB 转串口

如果 Linux 服务器本机接了 USB 转串口，跳过 ser2net + 反向隧道，直接：

```bash
alb setup serial --local
# 检测 /dev/ttyUSB0 / /dev/ttyACM0
alb serial connect
```

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
alb devices         # 走 adb
alb logcat          # 走 adb
alb serial log      # 走 串口
alb shell "reboot"  # 走 adb
alb serial log      # 立即看重启过程
```

### 组合：C + G

开发阶段用 sshd 快速开发，bringup 阶段用 UART 看底层：

```bash
# 新编的 kernel 推进去后 boot 失败，立刻切 UART：
alb shell "cp /tmp/Image /boot/Image" --via ssh    # 用 C 推
alb shell "reboot" --via ssh
alb serial log --follow                             # 切 G 看
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

## 九、alb 的 UART 相关能力（M1）

| 命令 / tool | 说明 |
|----------|------|
| `alb setup serial` | 引导配置（检测依赖 / 建 PTY） |
| `alb serial connect` | 交互式连接（picocom） |
| `alb serial log --duration N --save` | 后台采集 N 秒并保存 |
| `alb serial send "<text>"` | 发送字符串到串口（含换行/特殊字符） |
| `alb serial uboot` | 进 u-boot 模式（持续中断） |
| `alb serial watch --pattern "panic|oops"` | 监听关键字并触发动作 |
| MCP: `alb_uart_capture` | LLM 直接调 |
| MCP: `alb_uart_send` | LLM 发指令 |
| MCP: `alb_uart_watch_panic` | 后台监听 panic |

---

## 十、总结

- **如果你只有一种方案能做**：UART 救砖场景无替代
- **如果你要组合用**：A + G 是最推荐的组合
- **如果你在调 kernel / driver / bringup**：UART 是你的主力工具，adb 是辅助
- **生产调试**（起来的机器）：UART 退居备用，让 adb / sshd 主导

---

## 参考

- [原版 adb-via-ssh-tunnel-guide.md](../../../adb-via-ssh-tunnel-guide.md)（方案 A 的祖本）
- [ser2net 项目](https://sourceforge.net/projects/ser2net/)
- [picocom GitHub](https://github.com/npat-efault/picocom)
- [socat man page](https://linux.die.net/man/1/socat)

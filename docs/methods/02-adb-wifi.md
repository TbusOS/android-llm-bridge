---
title: 方案 B · adb over WiFi
type: method-guide
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [adb, wifi, method-b]
---

# 方案 B · adb over WiFi

> 无线 adb —— 板子联网后，Linux 服务器通过 TCP 直连板子的 adb server。**适合无线临时调试、多设备并发**。

---

## 一、适用场景

- ✅ 板子和 Linux 服务器在**同网段**（或可路由）
- ✅ 不想拖 USB 线
- ✅ 多台板子同时调，一根 USB 不够
- ✅ 临时看看日志、推小文件

**不适合**：
- ❌ 进 recovery / fastboot（这些模式下 WiFi 不起）
- ❌ 首次鉴权（必须先 USB 授权一次）
- ❌ 网络差环境（延迟 / 丢包严重影响体验）

---

## 二、架构

```
┌─────────────────┐          TCP (同网段)        ┌──────────────┐
│  Linux 服务器    │ ◄─────────────────────────► │  Android 板子 │
│  (adb client)   │  adb connect 192.168.1.42:5555│  (adbd 监听)  │
└─────────────────┘                              └──────────────┘
```

没有中间机，没有反向隧道。直连。

---

## 三、配置步骤

### 前置：板子必须先 USB 接过一次

WiFi adb 的 RSA 授权需要板子屏幕确认，这一步**必须走一次 USB**（方案 A 场景 1）。之后换 WiFi。

### 第 1 步 · 板子切入 TCP 模式

通过 USB 或 alb（方案 A）：

```bash
# Linux 端（adb 已通过方案 A 工作）
alb shell "ip addr show wlan0"
# 记下板子 IP，如 192.168.1.42

alb reboot-to-tcp-mode 5555       # alb 封装
# 或原始 adb:
adb tcpip 5555
# (板子会重启 adbd 进 TCP 模式，监听 5555)
```

### 第 2 步 · 连接

```bash
alb setup wifi --host 192.168.1.42
# 内部执行：adb connect 192.168.1.42:5555

alb devices
# 应看到：192.168.1.42:5555    device
```

### 第 3 步 · 正常使用

所有 `alb xxx` 命令和方案 A 一样使用。底层走 WiFi。

---

## 四、和方案 A 的共存

一台板子可以同时用 A + B，两条通道并存：

```bash
# 同时有 USB 和 WiFi
alb devices
# abc123              device   (via USB, 方案 A)
# 192.168.1.42:5555   device   (via WiFi, 方案 B)

alb shell "ls" --device abc123           # 走 USB
alb shell "ls" --device 192.168.1.42:5555  # 走 WiFi
```

---

## 五、Android 11+ 的新版 "Wireless debugging"

Android 11 起支持不用 USB 引导的 `adb pair`：

1. 板子：设置 → 开发者选项 → **Wireless debugging** → 启用
2. 板子：**Pair device with pairing code**
3. Linux 上：
   ```bash
   alb setup wifi --pair 192.168.1.42:PAIR_PORT --code XXXXXX
   ```
4. 配对成功后：
   ```bash
   alb setup wifi --host 192.168.1.42 --port CONNECT_PORT
   ```

这个方式完全无需 USB。

---

## 六、常见问题

### 1 · `adb connect` 后立刻 `offline`
可能是：
- 板子重启了 → 重 `adb connect` 即可
- 板子 IP 变了 → `ip addr` 确认
- 网络闪断 → `alb reconnect`

### 2 · 想用但板子 5555 端口不通
- 板子防火墙？一般 Android 不开防火墙，但定制 ROM 可能
- 路由器隔离？检查 AP 是否开了"AP 隔离"
- 企业网 WiFi 拦截？改用 DHCP 静态或热点

### 3 · 速度慢
```bash
# 看看板子信号
alb shell "dumpsys wifi | grep -i rssi"
# RSSI > -60 dBm 为好
```
WiFi 5/6 理论能跑 几 MB/s 到几十 MB/s，远不如 USB (USB 3.0 可达 400 MB/s)。

### 4 · 想从 WiFi 切回 USB
```bash
alb shell "ip addr show wlan0"  # 记 IP 以防再用
alb disconnect 192.168.1.42     # 断开 WiFi
# 然后 USB 照常
```

---

## 七、能力 / 局限

### ✅ 能做
- shell / push / pull / logcat / dmesg
- install / uninstall
- 一切 adb 命令（除了刷机类）

### ❌ 做不到
- 进 recovery / bootloader（WiFi 在这些模式下不工作）
- 首次授权（必须先 USB 一次）
- 大文件快传（不如 USB）

---

## 八、安全提示

**方案 B 是明文协议**！

- 不要在不可信网络用（公共 WiFi / 咖啡馆）
- 不要把板子暴露在公网（`adb tcpip` 后如果端口能从外网访问 = 设备被接管）
- 企业内网建议：
  - 单独的 debug VLAN
  - 或只在私网下用

---

## 九、和其他方案对比

| 需求 | A (USB) | B (WiFi) | C (sshd) |
|------|:------:|:-------:|:--------:|
| 速度 | 最快 | 中 | 慢-中 |
| 稳定性 | 最高 | 看网络 | 高 |
| 便利性 | 要 USB 线 | 最方便 | 要装 sshd |
| 安全性 | 物理 | **明文** | ssh 加密 |
| 首次鉴权 | 必须 | 要先 USB 过 | ssh key |
| 刷机 | ✅ | ❌ | ❌ |

结论：**B 是便利性妥协方案**，不是生产调试首选。

---

## 十、alb 相关命令

```bash
alb setup wifi --host 192.168.1.42         # 配置并连接
alb setup wifi --pair ...                   # Android 11+ 无线配对
alb reboot-to-tcp-mode [port]              # 切换板子到 TCP 模式
alb disconnect <host:port>                 # 断开特定连接
alb wifi status                            # 列当前 WiFi 连接
```

---
title: 调试方案对比
type: reference
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [methods, comparison, selection]
---

# 调试方案对比

> 七种方案并存，按场景选。**互补不互斥**，实战往往同时用 2-3 种。

---

## 一、方案目录

| 方案 | 全称 | 状态 | 详细文档 |
|------|-----|------|---------|
| **A** | adb USB（+ SSH 反向隧道） | ✅ M1 | [`01-ssh-tunnel-adb.md`](./01-ssh-tunnel-adb.md) |
| **B** | adb over WiFi | ✅ M1 | [`02-adb-wifi.md`](./02-adb-wifi.md) |
| **C** | 板子内装 sshd | ✅ M1 | [`03-android-sshd.md`](./03-android-sshd.md) |
| D | USB 网络共享（IP over USB） | 📋 M3 | [`04-usb-network.md`](./04-usb-network.md) |
| E | scrcpy 屏幕镜像 | 📋 M3 | [`05-scrcpy.md`](./05-scrcpy.md) |
| F | frp / ngrok 云中转 | 📋 M3 | [`06-frp-cloud.md`](./06-frp-cloud.md) |
| **G** | UART 串口（+ SSH 隧道） | ✅ M1 | [`07-uart-serial.md`](./07-uart-serial.md) |

---

## 二、能力对比矩阵

| 能力 / 方案 | A | B | C | G | D | E | F |
|------------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| shell 命令 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| 文件 push / pull | ✅ | ✅ | ✅ | 🚫 | ✅ | ❌ | ✅ |
| 增量同步 (rsync) | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ |
| logcat 实时流 | ✅ | ✅ | ✅* | ❌ | ✅ | ❌ | ✅ |
| dmesg / kmsg | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| **看 u-boot / kernel 启动 log** | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **进 u-boot 命令行** | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **看 kernel panic 完整栈** | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **板子死机仍可用** | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| 进 recovery / fastboot | ✅ | ❌ | ❌ | 部分** | ❌ | ❌ | ❌ |
| apk install | ✅ | ✅ | ✅*** | ❌ | ✅ | ❌ | ✅ |
| 持久 session (tmux) | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ | ✅ |
| 多人并发同设备 | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ |
| sshfs 挂载板子目录 | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ |
| 端口转发 (灵活 -L/-R/-D) | 弱 | 弱 | ✅ | ❌ | ✅ | ❌ | ✅ |
| 屏幕画面 / 录屏 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 跨地理远程 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

> \*  C 的 logcat 是 ssh 进去后跑 `logcat`，不是 adb 原生协议，实时性略差
> \** G 能看 recovery 启动 log，但不能用 `adb sideload`
> \*** C 需要把 apk 文件 scp 到板子后 `pm install`

---

## 三、按场景选型

### 场景 → 方案

| 场景 | 首选 | 备用 | 原因 |
|------|------|------|------|
| 刷机、进 recovery、烧 OTA 包 | A | - | 只有 adb 协议能在 recovery 下工作 |
| 系统 bringup / 起不来 / kernel panic | **G** | - | 只有串口能看底层日志 |
| 修改 u-boot 启动参数 / 环境变量 | **G** | - | 必须串口中断启动 |
| 日常 SDK 开发：改码 → 推板子 → 试 | C | A | rsync 增量快、tmux 持久 session |
| 排 app 偶发 crash / ANR | A | C | logcat 实时 + ANR 目录拉取 |
| 多台设备并发跑回归测试 | B + C | A | 无线 / 多 session 并发 |
| 客户现场远程调试 | F | - | 走公网中转 |
| 板子在别人电脑上 (Windows) | A + G | - | 都走 Xshell 反向隧道 |
| 只有 WiFi 没有 USB | B | - | 无线 adb |
| 需要看设备屏幕实际内容 | E | - | 屏幕镜像 |
| 板子无网络、无 USB，只有串口引脚 | **G** | - | 唯一选择 |

### 方案 → 典型搭配

| 搭配 | 场景 |
|------|------|
| **A + G** | 最常见。A 做日常、G 做救砖 |
| **C + A** | 开发强化。平时用 C 快，问题时回 A |
| **A + B** | 临时无线。有线时 A，拔线后 B |
| **G + A + C** | 全套。UART bringup → adb 刷机 → sshd 开发 |

---

## 四、决策树

```
板子能亮屏吗？
├─ 不能（起不来 / panic / 黑屏）
│    └─ 用 G (UART)，看 boot log
│
└─ 能
    ├─ 要刷机 / 进 recovery？
    │    └─ 用 A (adb USB)
    │
    ├─ 在本机 USB 连？
    │    ├─ Linux 本机 → A 直接 adb
    │    ├─ Windows + SSH Linux → A (Xshell 反向隧道)
    │    └─ 只有 WiFi → B
    │
    ├─ 要跑长任务 (> 1h) / 大目录同步？
    │    └─ 用 C (sshd) + tmux + rsync
    │
    └─ 远程 / 客户现场？
         └─ 用 F (云中转，M3)
```

---

## 五、配置复杂度

| 方案 | 板子端 | 服务器端 | Windows 中介 | 首次耗时 |
|-----|--------|---------|-------------|---------|
| A | 开 USB 调试 | 装 adb | 装 adb + Xshell 反向隧道 | 15 分钟 |
| B | 开 USB 调试 + 联网 + `adb tcpip` | 装 adb | - | 10 分钟 |
| C | 装 sshd（root 或 Termux） + 配 key | 装 ssh | - | 30 分钟 |
| G | 引出 UART + USB 转串口线 | 装 picocom/socat | 装 ser2net + Xshell 反向隧道 | 20 分钟 |
| D | RNDIS 驱动 + USB 网络配置 | 装 ssh | - | 1+ 小时 |
| E | 开 USB 调试 | 装 scrcpy + 依赖 | Windows 直接装也行 | 10 分钟 |
| F | 装 frpc / ngrok + 配 token | 装 frps / 订阅 | - | 30 分钟 |

---

## 六、安全性对比

| 方案 | 加密 | 鉴权 | 风险 |
|------|-----|------|------|
| A (USB) | 局部 | USB 弹窗 + RSA | 物理接触即可接管 |
| B (WiFi) | **无** | USB 弹窗 + RSA | 同网段任何人可扫到 |
| C (sshd) | ssh | pubkey / password | 取决于 ssh 配置 |
| G (UART) | **无** | **无** | 物理接触即可 |
| F (云) | TLS | token | 中转服务商需信任 |

**推荐**：
- 生产设备 / 金融相关 → C（ssh key + 禁密码）
- 研发调试 → A + G
- 公网暴露 → 必须 F + 严格 token

---

## 七、常见误区

| 误区 | 纠正 |
|-----|------|
| "adb 足够，不需要串口" | 系统起不来时 adb 也起不来，必须 UART |
| "sshd 能替代 adb" | 进 recovery / fastboot 只有 adb 管用 |
| "adb WiFi 快" | 同等条件下 USB 比 WiFi 快 5-10 倍 |
| "UART 只能看日志" | 串口能在 u-boot 下刷分区、跑脚本 |
| "串口必须本地接" | 通过 ser2net + Xshell 反向隧道可以远程 |

---

## 八、扩展新方案

加方案 X 的步骤：

1. 写 `docs/methods/XX-<name>.md` 说明方案
2. 实现 `src/alb/transport/<name>.py` （继承 `Transport` ABC）
3. 在 `registry.py` 注册
4. 写 `scripts/setup-method-<name>.sh` 引导脚本
5. 本页对比表更新
6. PR

详见 [`tool-writing-guide.md`](../tool-writing-guide.md) 和 [`contributing.md`](../contributing.md)。

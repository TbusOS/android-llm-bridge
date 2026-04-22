---
title: 方案 A · adb USB（含 SSH 反向隧道）
type: method-guide
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [adb, usb, ssh-tunnel, method-a]
---

# 方案 A · adb USB（+ SSH 反向隧道）

> 最基础、最可靠的方案。板子全程走 USB 无需联网。**用于刷机 / recovery / 首次授权 / 生产调试**。

---

## 一、适用场景

- ✅ 板子插在本地 Linux 服务器的 USB
- ✅ 板子插在远程 Windows 机器，Linux 服务器通过 SSH 连过去调
- ✅ 需要进 recovery / fastboot / sideload（其他方案都做不到）
- ✅ 首次授权新设备（USB 调试弹窗）
- ✅ 板子无网络

---

## 二、架构（远程场景）

```
┌─────────────────┐       Xshell SSH         ┌──────────────────┐         USB
│   Linux 服务器   │ ◄─────────────────────► │  Windows 电脑    │ ◄──────────► 板子
│ (Claude Agent)  │   + 反向隧道 (-R 5037)  │  (adb server)    │
└─────────────────┘                          └──────────────────┘

  Linux 上 adb 命令的实际数据流：
  adb shell → tcp:localhost:5037 → SSH 反向隧道
           → Windows tcp:localhost:5037 → adb server → USB → 板子
```

### 本地场景（更简单）

Linux 服务器直接插 USB：省掉 Xshell 反向隧道，`adb` 二进制直连本地 server。

---

## 三、配置步骤（远程 Windows 场景）

### 第 1 步 · Windows 端装 adb 并启动

如果 `adb version` 能跑，跳过。

1. 下载：https://dl.google.com/android/repository/platform-tools-latest-windows.zip
2. 解压到 `C:\platform-tools\`
3. 加入系统 PATH（环境变量 → 系统变量 → Path → 新建 `C:\platform-tools`）
4. 板子用 USB 线连 Windows
5. 板子里**开启 USB 调试**：
   - 设置 → 关于手机 → 连续点"版本号" 7 次 → 开发者模式启用
   - 设置 → 系统 → 开发者选项 → USB 调试 (开)
6. 在 Windows cmd 里验证：
   ```
   adb devices
   ```
   - 第一次会在板子屏幕弹"允许此电脑调试" → 勾"始终允许" → 允许
   - 看到 `<serial>    device` 就行

### 第 2 步 · Linux 端装 adb

```bash
cd ~
wget https://dl.google.com/android/repository/platform-tools-latest-linux.zip
unzip platform-tools-latest-linux.zip
echo 'export PATH=$HOME/platform-tools:$PATH' >> ~/.bashrc
source ~/.bashrc
adb version
```

如果网慢，scp/rz 上传也行。

**alb 封装版**（M1 后）：
```bash
alb setup adb              # 自动检测/下载/配置
```

### 第 3 步 · Xshell 配反向隧道

这是关键步骤，让 Linux 能反过来访问 Windows 的 5037 端口。

打开会话属性（或新建）：

1. 左侧：类别 → 连接 → SSH → **隧道**
2. 添加（Remote 类型）：

| 字段 | 值 |
|------|----|
| 类型 | **Remote (转发)** ⚠️ 不是 Local |
| 源主机 | `localhost` |
| 侦听端口 | `5037` |
| 目标主机 | `localhost` |
| 目标端口 | `5037` |
| 描述 | `adb-tunnel` |

3. 应用 → **重新连接会话**

> **Xshell 术语**：Remote (转发) = SSH 的 `-R` 参数。让 SSH 服务端(Linux) 能反向访问 SSH 客户端(Windows) 的端口。**不是 Local，方向相反**。

### 第 4 步 · 验证

```bash
# Linux 上
ss -tlnp 2>/dev/null | grep 5037
# 应看到 5037 LISTEN

export ADB_SERVER_SOCKET=tcp:localhost:5037
# 永久生效：
echo 'export ADB_SERVER_SOCKET=tcp:localhost:5037' >> ~/.bashrc

adb devices
# 应该看到 Windows 上连的板子
```

### 第 5 步 · alb 接管

```bash
alb setup adb --remote-via-tunnel
alb devices
alb shell "getprop ro.build.version.sdk"
```

---

## 四、基本用法

### 设备信息

```bash
alb devices                                    # 列所有连接的设备
alb shell "getprop ro.build.version.sdk"       # SDK 版本
alb shell "getprop ro.product.model"           # 型号
alb describe-device                             # 汇总信息
```

### 日志

```bash
alb logcat -d 60                      # 60秒 logcat
alb logcat -f "*:E"                   # 只 Error
alb logcat -f "ActivityManager:I *:S" # 只 AM:I
alb logcat --clear                    # 清 buffer 后采集
alb dmesg -d 30                       # kernel log
```

### 文件传输

```bash
alb push <local> <remote>        # Linux → 板子
alb pull <remote> <local>        # 板子 → Linux
alb pull /sdcard/Download/ ~/board-files/
```

### app

```bash
alb app install xxx.apk
alb app uninstall com.example.app
alb app list --filter com.example
alb app start com.example/.MainActivity
alb app stop com.example
```

### 重启 / 状态

```bash
alb reboot                       # 正常重启
alb reboot recovery              # 进 recovery
alb reboot bootloader            # 进 bootloader (方案 A 独占能力)
alb battery                      # 电池状态
```

### 诊断

```bash
alb bugreport                    # 完整诊断 zip
alb anr pull                     # 拉 ANR 文件
alb tombstone pull               # 拉 native crash
```

---

## 五、常见问题

### 1 · `adb devices` 空
检查：
1. Windows 上能看到吗？不能 → 查 USB 线 / PATH / 授权
2. Xshell 反向隧道生效？`ss -tlnp | grep 5037` 有 LISTEN 吗
3. `ADB_SERVER_SOCKET` 设了吗？`echo $ADB_SERVER_SOCKET`

### 2 · 隧道断了后卡住
```bash
adb kill-server      # 清 Linux 侧 client cache
# Windows 重新启动 adb server（adb devices 触发即可）
adb devices
```

### 3 · 多用户冲突
每人建自己的反向隧道即可，端口不会互相覆盖（每 SSH session 独立）。

### 4 · adb 版本不一致报错
两端都用最新 platform-tools，或显式锁同版本。

### 5 · 中文乱码
```bash
adb shell
export LANG=en_US.UTF-8
```

---

## 六、能力 / 局限

### ✅ 能做
- shell 命令、push/pull、logcat、dmesg、bugreport
- install/uninstall
- 进 recovery / fastboot / sideload（**独占**）
- 首次 USB 授权（**独占**）

### ❌ 做不到
- 看 boot log / u-boot / panic 栈 → 用 G
- rsync 增量同步 → 用 C
- 持久 session / 多用户并发 → 用 C

---

## 七、撤销

```bash
# Linux 端
unset ADB_SERVER_SOCKET
sed -i '/ADB_SERVER_SOCKET/d' ~/.bashrc
sed -i '/platform-tools/d' ~/.bashrc
rm -rf ~/platform-tools

# Xshell：属性 → SSH → 隧道 → 删 adb-tunnel 规则
# Windows：保留 adb 不影响别的
```

---

## 参考
- [Google platform-tools 官方](https://developer.android.com/tools/adb)

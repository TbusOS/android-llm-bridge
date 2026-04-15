---
title: 方案 C · 板子内 sshd
type: method-guide
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [ssh, sshd, method-c, rsync, tmux, sshfs]
---

# 方案 C · 板子内装 sshd

> 把板子当 Linux 服务器用 —— Linux ssh 进板子，享受 rsync / tmux / sshfs / 端口转发 / 多人并发。**开发日常的最强生产力方案**。

---

## 一、适用场景

- ✅ SDK 日常开发：改代码 → 增量推板子 → 测试
- ✅ 长跑测试（monkey / tcpdump / stress）需要持久 session
- ✅ 多人同时调试同一设备
- ✅ 需要把板子目录挂到本地编辑器（sshfs）
- ✅ 板子作为某种服务器（跑 daemon / 开 web）

**不适合**：
- ❌ 刷机 / recovery 阶段
- ❌ 首次鉴权（要靠 A）
- ❌ bringup / 系统没起来（要靠 G）

---

## 二、能力对比（A 做不到 / C 能做）

| C 独占 | 为啥 A 顶不到 |
|-------|-------------|
| **rsync 增量同步** | adb push 全量，大目录慢几十倍 |
| **tmux / screen 持久 session** | adb shell 一断就丢 |
| **sshfs 挂载** | adb 无此能力 |
| **ssh -L/-R/-D 复杂端口转发** | adb forward 仅单 TCP |
| **多人并发 session** | adb server 单实例 |
| **工业级加密 + key 鉴权** | adb 鉴权较弱 |
| **板子里跑标准 Linux 工具**（git/python/vim 等） | adb shell 只有 toybox |
| **跨网络远程**（通过 ssh 跳板机） | adb 仅限本地/同网段 |

---

## 三、sshd 在 Android 上的三种形态

| 形态 | 要求 | 优缺点 |
|-----|------|-------|
| **dropbear**（原生编译进系统） | AOSP 源码 + root | 轻量、启动快；但需要重编镜像 |
| **Termux + openssh** | 非 root 可用 | 最易装；Termux 环境隔离 |
| **Magisk 模块 SSHelper** | Root + Magisk | 易装；需 root |
| **厂商预装** | 看 ROM | Rockchip / 小米开发版有自带 |

推荐：**开发板**用 dropbear（稳定、启动快、占资源少）；**消费机**用 Termux（无需 root）。

---

## 四、配置步骤（dropbear 版本）

### 第 1 步 · 编 dropbear 进系统

```bash
# AOSP 源码里（device/<vendor>/<board>/ 或 external/）
# 大多数 AOSP 有 external/dropbear

# BoardConfig.mk 里加：
PRODUCT_PACKAGES += dropbear dropbearkey scp

# init.<board>.rc 或 init.rc 里加：
service dropbear /system/bin/dropbear -F -R -p 0.0.0.0:2222
    class main
    user root
    group root
    oneshot

# 重编刷机（方案 A）
```

### 第 2 步 · 首次生成 key + 配 authorized_keys

```bash
# Linux 端生成 key
ssh-keygen -t ed25519 -f ~/.ssh/alb-device -C "alb-android-bridge"
```

```bash
# 通过 adb（方案 A）推 authorized_keys
alb push ~/.ssh/alb-device.pub /data/local/tmp/auth
alb shell "mkdir -p /data/local/ssh && cp /data/local/tmp/auth /data/local/ssh/authorized_keys"
alb shell "chmod 600 /data/local/ssh/authorized_keys"

# dropbear 启动命令改为：
# dropbear -F -R -p 2222 -r /data/local/ssh/dropbear_rsa_host_key -A /data/local/ssh/authorized_keys
```

### 第 3 步 · 板子联网 + 启动 sshd

```bash
alb shell "start dropbear"   # 如果 init 没 auto-start
alb shell "ip addr show wlan0"  # 拿 IP
```

### 第 4 步 · 在 Linux 端配 ssh config

```bash
# ~/.ssh/config
Host android-dev
    HostName 192.168.1.42
    Port 2222
    User root
    IdentityFile ~/.ssh/alb-device
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    ServerAliveInterval 30
```

测试：
```bash
ssh android-dev "uname -a"
# 应该返回板子 kernel 版本
```

### 第 5 步 · alb 接管

```bash
alb setup ssh --host android-dev              # 引用 ssh config 别名
# 或:
alb setup ssh --host 192.168.1.42 --port 2222 --user root --key ~/.ssh/alb-device

alb shell "ls /data"
alb rsync-sync ~/my-sdk-output/ /data/dev/
```

---

## 五、Termux 版（无 root）

```bash
# 板子里打开 Play 商店 / F-Droid，装 Termux
# Termux 里：
pkg install openssh
passwd                          # 设密码
sshd                            # 启动 sshd，默认端口 8022
```

Linux 端：
```bash
ssh -p 8022 u0_a123@192.168.1.42
```

**限制**：
- 权限受 Termux UID，只能访问 `/data/data/com.termux/files/*` 和 `/sdcard/*`
- 不能 `shell("am start ...")` （跨 UID 受限）
- 装了后可把 apk 路径挂到 Termux 里间接操作

---

## 六、能力演示

### rsync 增量同步

```bash
# 每次编译后只推改过的文件
alb rsync-sync ~/aosp/out/target/product/foo/system/ /system-dev/
# 比 adb push 快 10-100 倍（看改动量）
```

### tmux 持久 session

```bash
alb ssh-tmux start test-session "stress-ng --cpu 8 --timeout 2h"
# 会话在板子后台跑，网络断也不丢
alb ssh-tmux attach test-session    # 随时接回来看
```

### sshfs 挂载板子目录

```bash
alb sshfs-mount /data/dev /mnt/board
# 现在本地 /mnt/board/ 就是板子的 /data/dev/
# 用 vscode 直接编辑板子的文件
```

### 复杂端口转发

```bash
# 板子里跑了个 web 服务监听 8080，想在 Linux 直接访问
alb ssh-forward -L 9090:localhost:8080
# 浏览器打开 http://<linux-ip>:9090 就是板子的 web

# 或者让板子通过 Linux 上网（SOCKS 代理）
alb ssh-forward -D 1080 --on-device
```

### 多人并发

```bash
# 三个 session 同时跑，互不干扰
ssh android-dev "logcat"
ssh android-dev "top"
ssh android-dev "tcpdump -i any"
```

---

## 七、常见问题

### 1 · ssh 连上但跑命令路径不对

Android 的 PATH 和 Linux 不同：

```bash
# ~/.ssh/config 里加
SendEnv PATH
# 或 ssh 时：
ssh android-dev "export PATH=/vendor/bin:/system/bin:/system/xbin:\$PATH && your-cmd"
```

或在板子 `/data/local/.profile` 写环境变量，让 sshd 加载。

### 2 · sshd 启动后不 listen

```bash
alb shell "ps | grep dropbear"
alb shell "netstat -tlnp | grep 2222"
alb dmesg | grep dropbear
```

常见原因：
- `/data/local/ssh/dropbear_rsa_host_key` 不存在 → `dropbearkey -t rsa -f ...` 先生成
- SELinux 拦了 → 看 `adb shell dmesg | grep avc`，临时 `setenforce 0` 验证

### 3 · 权限不够做某些操作

```bash
# 跑 root shell（如果设备允许）
ssh android-dev "su -c 'your-root-cmd'"
# 或板子 build.prop 设 ro.debuggable=1 + adb root
```

### 4 · rsync not found on device
板子没装 rsync？两个办法：
- 编进系统：`PRODUCT_PACKAGES += rsync`
- 临时 push：`alb push /usr/bin/rsync /data/local/rsync && chmod +x`

### 5 · 会话断了 tmux 丢

tmux 默认是用户态，板子重启就丢。要持久：
```bash
# 板子里装 tmate 作替代（网络持久）
# 或把 tmux session 保存到 /data/
```

---

## 八、安全性

| 配置 | 强度 |
|-----|------|
| 密码登录 | ❌ 禁止 |
| Key-based | ✅ 推荐 |
| 限制 `authorized_keys` 的 `from=192.168.1.0/24` | ✅ 更安全 |
| 非默认端口 (2222) | ✅ 减少扫描命中 |
| fail2ban 自动拉黑 | ✅ 生产环境 |
| 只允许 `AllowUsers developer` | ✅ 最小权限 |

典型生产级 `sshd_config` / `dropbear` 启动参数：

```
dropbear -F -R -p 2222 \
  -r /data/local/ssh/host_rsa \
  -A /data/local/ssh/authorized_keys \
  -s      # 禁用密码登录
```

---

## 九、alb 相关命令

```bash
alb setup ssh                     # 引导：选 dropbear/Termux、生 key、推配置
alb ssh-health                    # 连通性 + 延迟检查
alb rsync-sync <local> <remote>   # 增量同步
alb ssh-forward -L 9090:host:80   # 端口转发
alb sshfs-mount <remote> <local>  # 挂载
alb ssh-tmux start/attach/list    # tmux session 管理
```

---

## 十、和其他方案协作

### C + A 黄金组合

```bash
# 开发阶段 ssh 快速迭代
alb rsync-sync out/system /system-dev/
alb shell "am restart ..."          # 走 ssh

# 出问题回方案 A
alb reboot recovery                 # 必须走 adb
alb sideload new-image.zip          # 也是 adb
```

### C + G 组合

新 kernel push 后起不来：

```bash
alb push Image /boot/ --via ssh     # C
alb reboot --via ssh
alb serial log --follow             # 切 G 看启动
```

---

## 十一、能力 / 局限回顾

### ✅ 生产力杀手锏
- rsync / tmux / sshfs / 端口转发 / 多人并发
- 工业级加密

### ❌ 做不到
- 刷机 / recovery / fastboot → 方案 A
- 启动阶段 / panic → 方案 G
- 屏幕镜像 → 方案 E

# 设备侧 agent 启用 fastboot 烧录 — 配置手册

> 这份文档是给**设备侧那台 Windows 机器上的操作者（或 AI 助手）**看的。
> 目标：让 agent 能代表 hub 执行 fastboot 操作（列设备 / 烧分区 / 退出 fastboot）。
>
> 全程只在这台 Windows 机器上操作，不需要动 hub。

---

## 0. 先搞清楚这在做什么

板子进入 fastboot 之后会**从 adb 里消失**。alb 原本能把板子推进 fastboot，却够不着
它出来 —— 唯一的恢复手段是有人走到这台机器跟前。这次配置就是把那条路补上。

配好之后，hub 那边可以：

| 操作 | 效果 |
|---|---|
| `fastboot devices` | 看见处于 fastboot 的板子（adb 里已经看不到它了） |
| 烧写单个分区 | 镜像从 hub 流过来，**agent 先核对 sha256 再动设备** |
| `fastboot reboot` | 把板子从 fastboot 拉回系统 |

**安全形状（不要试图绕过）**：hub 只发结构化字段（操作名 / 分区名 / 大小 / 摘要），
**永远不发命令行**。命令行由 agent 在本机拼装，可执行文件路径来自本机配置。
这不是麻烦，是这条通道不至于变成远程执行后门的原因。

---

## 1. 确认 fastboot 存在

在 PowerShell 或 cmd 里：

```
where fastboot
```

- **有输出**（比如 `D:\platform-tools\fastboot.exe`）→ 记下这个路径，进入第 2 步。
- **没有输出** → 需要先装 Android SDK Platform-Tools：
  1. 从 Google 官方下载 platform-tools（`https://developer.android.com/tools/releases/platform-tools`）
  2. 解压到一个固定目录，比如 `D:\platform-tools\`
  3. 记下 `fastboot.exe` 的完整路径

验证它能跑：

```
D:\platform-tools\fastboot.exe --version
```

有版本号输出就算通过。

---

## 2. 更新 agent 程序

agent 的代码文件需要更新到含作业通道的版本，否则它**永远不会报告 fastboot 能力**，
hub 那边会一直显示"不可用"。

从 hub 导出的共享目录（映射的网络盘，目录名 `alb-windows-agent`）拷贝到本机 agent 目录：

| 从共享目录 | 到本机 |
|---|---|
| `alb_agent.py` | `<agent目录>\alb_agent.py` **（必须）** |
| `agent.conf.example` | `<agent目录>\agent.conf.example` （可选，只是键名参考） |

> ⚠ **不要拷 `agent.conf`**。共享目录里那份是留档，本机这份才是在用的真配置，
> 覆盖了会把 token 和 hub 地址弄丢。

`requirements.txt` 里的 `websockets` 下限提到了 `>=13.0`。如果 agent 现在能正常跑，
说明装的已经满足，**不需要重新 pip install**。（agent 用的
`websockets.asyncio.client` 在 12.x 里根本不存在，跑得起来就说明 ≥13。）

---

## 3. 改配置

编辑本机的 `agent.conf`（**不是** `agent.conf.example`），在文件末尾追加：

```
fastboot_path=D:\platform-tools\fastboot.exe
```

把路径换成第 1 步记下来的那个。

**可选但推荐** —— 限定这台机器允许写哪些分区：

```
flash_partitions=vendor_cfg,boot,dtbo
```

留空 = 任何格式合法的分区名都收。为什么建议填：烧错分区**撤不回来**，而且板子可能
起不来告诉你结果。这份名单是最后一道能拦住手误的闸。

格式约定：一行一个 `KEY=VALUE`，不要加引号，注释单独占一行。

---

## 4. 重启 agent

关掉正在跑的 agent 窗口（Ctrl+C 一次即可，正常退出不会打印异常堆栈），
重新运行 `run-agent.bat`。

---

## 5. 验证（**这一步必须做，别跳**）

看 agent 的启动日志（控制台窗口，或 `logs\agent.log`），找这一行：

**成功长这样：**

```
fastboot: D:\platform-tools\fastboot.exe · partitions vendor_cfg,boot,dtbo
```

**失败长这样：**

```
fastboot: not found — this agent will NOT advertise the fastboot capability
```

看到 `not found` 说明路径没配对，回第 1、3 步核对：

| 现象 | 原因 | 怎么改 |
|---|---|---|
| `not found`，但 `where fastboot` 有输出 | `agent.conf` 里的路径写错了，或指向一个不存在的文件 | 用 `where fastboot` 的原样输出，注意反斜杠不要转义 |
| `not found`，`where fastboot` 也没输出 | 这台机器上确实没装 | 回第 1 步装 platform-tools |
| 启动日志根本没有 `fastboot:` 这一行 | `alb_agent.py` 还是旧版 | 回第 2 步重新拷贝 |

另外确认这一行还在（说明和 hub 的连接正常）：

```
identity: name=... agent_id=... hub=...
```

---

## 6. 告诉 hub 侧的人

配完请回话，hub 那边会执行一次状态查询确认。预期变化：

- 之前：`fastboot unavailable — no connected agent advertises the fastboot capability`
- 之后：`ready`

如果 hub 那边仍然显示 unavailable，但本机日志明明打印了 `fastboot: <路径>`，
那说明 agent 没有重连成功 —— 检查本机状态页（默认 `http://127.0.0.1:8731`）
上的 `connected` 是不是 true。

---

## 7. 之后会发生什么（供参考，不需要现在做）

hub 侧发起烧录时，这台机器上会依次发生：

1. agent 收到一个结构化请求：分区名 + 字节数 + sha256
2. 分区名过本机的形状检查和白名单；不通过直接拒绝，**不接收任何数据**
3. 镜像流式写进本机临时目录
4. **核对 sha256** —— 不一致就报错，**一个字节都不会写进设备**
5. 通过之后才调 `fastboot.exe flash <分区> <临时文件>`
6. fastboot 的输出实时回传给 hub，临时文件在结束时删除

同一时刻只允许一个作业。第二个请求会立刻收到"忙"，不会排队。

---

## 附：不需要做的事

- ❌ 不需要开放任何入站端口（agent 始终是主动往外连 hub）
- ❌ 不需要改防火墙
- ❌ 不需要动 hub 的任何配置
- ❌ 不需要重装 Python 依赖
- ❌ 不需要改 token（这次配置和 token 无关）

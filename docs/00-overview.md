---
title: android-llm-bridge 项目总览
type: design-doc
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [overview, architecture, positioning]
---

# 项目总览

> 从零开始了解 android-llm-bridge 的定位、能力边界、设计取舍与里程碑。

---

## 一、项目定位

**一句话**：让 AI Agent 像调函数一样安全、结构化地操作真实安卓设备。

**三个"面向"**：

| 面向 | 说明 |
|-----|------|
| **面向 LLM** | 结构化返回 / 错误码表 / 自描述 CLI / SKILL.md / MCP 一等公民 |
| **面向多场景** | adb 刷机、日常开发、无线联调、UART 救砖 —— 一套工具全覆盖 |
| **面向开源** | MIT License、通用不绑定厂商、可扩展（新传输 / 新能力只需实现接口） |

**不是什么**（避免误解）：

- ❌ 不是 adb / scrcpy / 串口工具的替代品 —— 底层仍然调用它们
- ❌ 不是 Android 自动化测试框架（Appium / uiautomator）—— 那是 UI 层，alb 在传输层
- ❌ 不是厂商 SDK 绑定工具 —— 刻意保持通用

---

## 二、典型使用场景

### 场景 1：日常开发调试

> "我改了一行 kernel 代码，重编 → 推板子 → 跑 dmesg → 看有没有报错。"

LLM 对话：
```
你：帮我把刚编译的 kernel Image 推到板子，然后 reboot 后抓 dmesg 里的错误
LLM：[调 alb.filesync.push() → alb.power.reboot() → alb.logging.dmesg(filter="error")]
     → 发现 3 条 error，已保存到 workspace/devices/abc/logs/2026-04-15T10-30.dmesg
     → 前两条是 wifi 驱动（无关），第三条是你刚改的 xxx.c 第 42 行 null pointer
```

### 场景 2：ANR / Crash 排查

> "用户反馈某 app 偶发崩，要复现 + 抓堆栈。"

```
你：帮我盯着这个设备，只要它出现 ANR 就立刻抓下来分析
LLM：[alb.diagnose.watch_anr(device=xxx)]
     → 15 分钟后检测到 ANR
     → 自动 pull /data/anr/*.txt 到 workspace
     → 提取堆栈关键帧：com.example.app.MainActivity.onCreate 阻塞 5s
     → 疑似：主线程调了 network I/O
```

### 场景 3：板子 bringup / 起不来

> "新板子烧完镜像不亮屏，adb 也没，看不到任何东西。"

```
你：板子不开机，只有串口线。接上 UART 看看是哪个阶段挂了
LLM：[alb.setup.serial() → alb.logging.uart_capture(duration=30)]
     → 捕获 0-30s 串口输出到 workspace
     → 看到：u-boot 加载 kernel 后，DTS 报错 "no compatible node for ..."
     → 问题指向：你的 dtsi 改动漏了某节点
```

### 场景 4：多设备 CI / 批量操作

> "实验室 10 台设备，给每台装新 apk 然后跑回归测试。"

```
你：对 profile=lab 下所有设备并行：装这个 apk，启动 test-harness，收集 result.json
LLM：[alb.profile.each("lab") {
      alb.app.install(apk) → alb.shell("am start test-harness") → alb.filesync.pull(...)
     }]
     → 10 台并行、2 分钟完成、汇总表格
```

### 场景 5：客户现场远程调试（M3）

> "客户机器 POS 死机了，他那边只有网络没有 Linux 服务器。"

（规划中，方案 F）

---

## 三、方案矩阵（多传输）

第一版落地 4 种，预留 3 种。

| 方案 | 通道 | 板子要求 | 启动log | u-boot | 板子无网 | 板子死机 | 首次鉴权 | 刷机 recovery |
|------|------|---------|:----:|:----:|:----:|:----:|:----:|:----:|
| **A** adb USB + SSH 隧道 | adb | USB调试开 | ❌ | ❌ | ✅ | ❌ | USB 弹窗 | ✅ |
| **B** adb WiFi | TCP | 联网 + 首次 USB 授权 | ❌ | ❌ | ❌ | ❌ | USB 弹窗 | ❌ |
| **C** 板子内 sshd | ssh | root 或 Termux | ❌ | ❌ | ❌ | ❌ | ssh key | ❌ |
| **G** UART 串口 | 串口 | UART 引出 | ✅ | ✅ | ✅ | ✅ | 无 | ❌ |
| D USB 网络共享 | IP-USB | RNDIS | ❌ | ❌ | ✅ | ❌ | - | ❌ |
| E scrcpy 屏幕镜像 | 走 adb | adb | ❌ | ❌ | ✅ | ❌ | - | ❌ |
| F frp / 云中转 | 公网 | 联公网 | ❌ | ❌ | ❌ | ❌ | token | ❌ |

### 各方案独占能力一览

| 独占能力 | 只有哪个方案能做 | 为啥别的不行 |
|---------|---------------|-------------|
| 看完整 boot log（u-boot → kernel → init） | **G** | 其他方案的 daemon 要等系统起来 |
| 进 u-boot 命令行（改启动参数、刷分区） | **G** | 只有串口能打断启动 |
| 看 kernel panic 完整 stack | **G** | 系统挂了 adbd/sshd 全死 |
| 进 recovery / fastboot 刷机 | **A** | 这些模式只跑 adb，不跑 sshd |
| 首次 USB 授权 / 鉴权 | **A** | 需要板子屏幕点"允许" |
| rsync 增量传 SDK 编译产物 | **C** | adb push 是全量 |
| sshfs 挂板子目录到本地 | **C** | adb 无此能力 |
| 持久 session（长跑 monkey / tcpdump） | **C** | adb shell 一断就丢 |
| 多人同时调试同一设备 | **C** | adb server 是单实例 |

详细对比见 [`methods/00-comparison.md`](./methods/00-comparison.md)。

---

## 四、业务能力（M1 ships 6）

| 能力 | CLI 入口 | MCP tool | 依赖传输 | 说明 |
|-----|---------|---------|---------|------|
| `shell` | `alb shell <cmd>` | `alb_shell` | A / B / C / G | 执行任意命令，结构化返回 |
| `logging` | `alb logcat` / `alb dmesg` / `alb uart-capture` | `alb_logcat` / `alb_dmesg` / `alb_uart_capture` | A / B / C → logcat·dmesg · G → uart | 日志收集，自动入 workspace |
| `filesync` | `alb push` / `alb pull` | `alb_push` / `alb_pull` | A / B / C | 文件传输，自动选最快通道（adb/rsync/scp） |
| `diagnose` | `alb bugreport` / `alb anr pull` / `alb tombstone` | `alb_bugreport` 等 | A / B / C | 一键拉诊断信息 |
| `power` | `alb reboot` / `alb sleep-wake` / `alb battery` | `alb_reboot` 等 | A / B / C / G | 重启 / 休眠唤醒 / 电池状态 |
| `app` | `alb app install/uninstall/start/stop` | `alb_app_*` | A / B / C | apk 管理 |

**M2+ 规划能力**：
- `perf` —— CPU / MEM / FPS / 温度 / 电流 持续采集
- `benchmark` —— 跑分集成（AnTuTu / GeekBench / 自定义）
- `network` —— 端口转发 / 抓包 / 弱网模拟
- `ui` —— 屏幕截图 / 录像 / 坐标事件

每个能力详见 [`capabilities/`](./capabilities/)。

---

## 五、三层接入（共享业务层）

```
CLI                MCP Server             Web API
(typer)            (mcp Python SDK)       (FastAPI)
  │                     │                      │
  └──────────┬──────────┴──────────┬───────────┘
             ▼                     ▼
            src/alb/capabilities/*.py        ← 业务层（唯一实现）
             │
             ▼
            src/alb/transport/*.py           ← 传输抽象
```

**关键原则**：业务函数定义一次，三层壳各自装饰。

- 新增能力 → 写 `capabilities/xxx.py`，CLI/MCP/API 三层同步自动暴露
- 新增传输 → 实现 `transport/base.py` 的 ABC，上层所有能力立即可用
- 永不重复实现

详见 [`architecture.md`](./architecture.md)。

---

## 六、核心设计原则（LLM-first 铁律）

| # | 原则 | 如何落地 |
|---|------|--------|
| 1 | **结构化胜于自由文本** | 所有 tool 返回 `Result(ok/data/error/artifacts)` dataclass |
| 2 | **错误可恢复** | error 带 code + suggestion；有 `docs/errors.md` 错误码表 |
| 3 | **危险操作默认拦截** | 权限系统（黑名单 + 多层策略）内置 |
| 4 | **产物路径可预测** | `workspace/devices/<serial>/{logs,anr,perf,...}` 固定结构 |
| 5 | **自描述 + 可发现** | `alb describe` 输出全部能力 schema；`SKILL.md` 自动生成 |
| 6 | **长任务不阻塞** | 流式 API + workspace 产物路径 + 后台任务 + 取消 |
| 7 | **反面规则先列** | `llm/CLAUDE.md` 明确列"不应做什么"（比抽象的"请小心"更有效） |

详见 [`llm-integration.md`](./llm-integration.md) 和 [`design-decisions.md`](./design-decisions.md)。

---

## 七、里程碑

| 里程碑 | 目标 | 状态 |
|-------|------|-----|
| **M0** 设计阶段 | 仓库骨架 + 完整技术方案 + 架构图 | ✅ 当前 |
| **M1** 第一版可用 | 4 传输 + 6 能力 + 权限 + CLI + MCP 骨架 + 单元测试 | 🚧 |
| **M2** Web + 长任务 | Web API / 流式日志 / 大文件 / 子 Agent 并行 / perf & benchmark | 📋 |
| **M3** Web UI + 智能 | 设备看板 / 实时图表 / LLM 日志分析 / 方案 D/E/F | 📋 |

详见 [`project-plan.md`](./project-plan.md)。

---

## 八、下一步读哪里

- 想了解**内部分层设计** → [`architecture.md`](./architecture.md)
- 想了解**为什么这么选**（语言 / 框架 / 架构取舍） → [`design-decisions.md`](./design-decisions.md)
- 想**接入大模型** → [`llm-integration.md`](./llm-integration.md)
- 想**贡献代码** → [`contributing.md`](./contributing.md) + [`tool-writing-guide.md`](./tool-writing-guide.md)
- 想查**某方案细节** → [`methods/`](./methods/)
- 想查**某能力接口** → [`capabilities/`](./capabilities/)

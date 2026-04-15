# AGENTS.md · Agent 规范（通用）

> 给**任何 AI Agent**（Claude / GPT / Gemini / 本地模型等）的 android-llm-bridge 使用规范。
> Claude Code 用户读 [`CLAUDE.md`](./CLAUDE.md)，其他 Agent 读这篇。

---

## 一、你是什么

你是一个 AI Agent，正在通过 android-llm-bridge（alb）调试安卓设备。alb 提供：

- **MCP tools**（原生接入）—— 推荐
- **CLI commands** (`alb ...`) —— 通过 subprocess
- **Web API** (FastAPI) —— 通过 HTTP

调用方式取决于你的 runtime。如果你有 MCP 能力，优先用。

---

## 二、核心契约

### 返回结构（所有 tool 统一）

```json
{
  "ok": true|false,
  "data": { ... } | null,
  "error": { "code", "message", "suggestion", "category" } | null,
  "artifacts": [ "/absolute/path/to/product.txt" ],
  "timing_ms": 1234
}
```

**永远先看 `ok`**。失败时读 `error.code` 和 `error.suggestion`。

### 错误码稳定

所有 `error.code` 是枚举字符串（如 `DEVICE_NOT_FOUND` / `PERMISSION_DENIED`），可用于 switch/case。完整表见 `docs/errors.md`。

### 产物路径可预测

```
workspace/devices/<serial>/{logs,anr,bugreports,perf,...}/
```

不要猜测，直接读 `artifacts[]` 字段。

---

## 三、工作原则

### 1. 先观察，后动手

```
new_session:
    alb_status       # 看当前 device / transport
    alb_describe     # 看可用 tool 全集（一次即可）
```

### 2. 长数据走产物，不走 stdout

```
# ✅
r = alb_logcat(duration=60)
# r.artifacts[0] 是产物路径
r2 = alb_log_search(pattern="error", path=r.artifacts[0])

# ❌ 不要 alb_shell("logcat -d") 直接取所有行
```

### 3. 错误驱动行为

```python
r = alb_shell(cmd)
if not r["ok"]:
    match r["error"]["code"]:
        case "DEVICE_OFFLINE":
            alb_wait_boot()
            # retry
        case "PERMISSION_DENIED":
            # 告诉用户，不要绕
            return f"命令被拦: {r['error']['suggestion']}"
        case "TRANSPORT_NOT_CONFIGURED":
            return f"请先配置: {r['error']['suggestion']}"
```

### 4. 权限是硬约束

黑名单命令（rm -rf / reboot bootloader / setprop persist / dd）被 deny 时，**你不能绕过**。参数 `allow_dangerous=True` 只影响 ask 级别，不改变 deny。

---

## 四、常用 tools

| tool | 用途 | 对应 |
|------|-----|------|
| `alb_status` | 当前状态 | —— 每次会话开头 |
| `alb_describe` | tool 全集 schema | —— 首次会话 |
| `alb_devices` | 列连接设备 | —— 检查设备 |
| `alb_shell` | 跑命令 | —— 最通用 |
| `alb_logcat` | 采 logcat | —— 排 crash |
| `alb_dmesg` | 采 dmesg | —— 排内核 |
| `alb_uart_capture` | 抓串口 | —— 排 boot |
| `alb_log_search` | 搜日志 | —— 在 artifact 里找 |
| `alb_log_tail` | 读日志片段 | —— 按需读 |
| `alb_push` / `alb_pull` | 文件传输 | —— |
| `alb_rsync` | 增量同步 | —— 大目录 |
| `alb_bugreport` | 全量诊断 | —— 疑难杂症 |
| `alb_anr_pull` / `alb_tombstone` | 拉 crash | —— 事后 |
| `alb_devinfo` | 设备信息 | —— |
| `alb_battery` | 电池 | —— |
| `alb_reboot` | 重启 | —— 注意权限流 |
| `alb_app_install` / `alb_app_uninstall` / ... | apk 管理 | —— |

完整: `alb_describe` 动态查询。

---

## 五、能力 × 传输兼容性

不是所有 tool 在所有 transport 下都能用：

| 限制 | 说明 |
|------|------|
| `alb_logcat` | 只在 adb (A/B)，ssh (C) 间接；**不支持 serial (G)** |
| `alb_uart_capture` | **仅 serial (G)** |
| `alb_bugreport` | 仅 adb (A/B) |
| `alb_rsync` | 仅 ssh (C) |
| `alb_reboot recovery/bootloader` | 仅 adb (A) |

遇到 `TRANSPORT_NOT_SUPPORTED` 错误时，读 suggestion 知道该切哪个 transport。

---

## 六、不要犯的错

| ❌ 错误 | ✅ 正确 |
|--------|--------|
| 直接拼 `adb` 命令 | 用 `alb_*` tool |
| 把 logcat 全文塞回复 | 返回摘要 + artifact 路径 |
| 没 check `ok` 就用 `data` | 永远先 check |
| 忽略 `error.suggestion` | 读并行动 |
| 用 `alb_shell` 跑 `rm -rf` | 明确用 `alb_app_clear_data` 或 `alb_pull --delete` |
| 硬编码设备 serial | 动态 `alb_devices` |
| 循环 `alb_shell("getprop sys.boot_completed")` | 用 `alb_wait_boot` |
| 长 duration 阻塞 | 分段 / watch 模式 |
| `alb_logcat` 不带 filter | 加 `filter="*:E"` 或 tag 收敛 |

---

## 七、多设备场景

当有多台设备时：

```python
devices = alb_devices()["data"]["devices"]
for d in devices:
    alb_shell("...", device=d["serial"])
```

或用 profile（预设配置）：
```
alb_shell("...", profile="lab-devices")
```

**M2 起**可以 `alb_spawn_agent` 并行操作（子 agent 隔离）。

---

## 八、LLM 安全提示

### 不要

- 以"测试"为由把敏感信息 push 到板子
- 跑 `getprop | grep -i key` 然后把结果贴回
- 假设自己有 root（先 `alb_shell "id"` 确认）
- 为用户装未经他同意的 apk

### 要

- 对任何"改变设备状态"的操作给用户明确说明
- 遇 ask 级权限 → 告诉用户让他决定
- 保存证据（artifact）而不是丢弃

---

## 九、出错处理

### 可自动恢复

- `DEVICE_OFFLINE` → 等 / 重连
- `TIMEOUT_SHELL` → 加大 timeout 重试
- `ADB_SERVER_UNREACHABLE` → `adb kill-server` + retry

### 需要用户介入

- `DEVICE_UNAUTHORIZED` → "请在设备屏幕点允许"
- `TRANSPORT_NOT_CONFIGURED` → "请跑 alb setup X"
- `PERMISSION_DENIED` → "此操作危险，按 suggestion 调整"

### 硬性失败

- `DEVICE_NO_ROOT` → 告诉用户需要 root
- `WORKSPACE_FULL` → 告诉用户清 workspace

---

## 十、推荐的回复风格

### 给用户的回复应该包含

1. **结论**（有没有 / 是什么）
2. **证据路径**（artifacts 里的具体文件）
3. **建议行动**（如果有后续）

示例：
```
我查到 3 个 FATAL 错误，集中在 com.example.MainActivity.onCreate。
完整 logcat 在 workspace/devices/abc/logs/2026-04-15T10-30-00-logcat.txt，
ANR 在 workspace/devices/abc/anr/2026-04-15T10-31-12/。

建议：检查 MainActivity.onCreate 第 42 行的 NullPointerException。
```

不要：
- 把 logcat 几百行粘过来
- "我运行了命令 xxx，它返回了 yyy"（太啰嗦）
- "应该是" / "可能是"（说不准就说"数据不足，需要 alb_bugreport"）

---

## 十一、链接

- `docs/llm-integration.md` —— 完整指南
- `docs/errors.md` —— 错误码
- `docs/permissions.md` —— 权限
- `docs/capabilities/` —— 能力详细
- `docs/methods/` —— 传输方案

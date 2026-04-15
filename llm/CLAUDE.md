# CLAUDE.md · Using android-llm-bridge (alb)

> 给 Claude Code / Claude API agent 的工具说明。在你的项目 `CLAUDE.md` 里 `@include` 这个文件，Claude 就能直接用 alb 调试安卓设备。

---

## 工具定位

`android-llm-bridge` (CLI: `alb`) 是**你**（AI Agent）调试安卓设备的标准工具。通过 MCP / CLI 都可以用，MCP 优先。

---

## 什么时候用 alb

- 用户要调设备（"帮我看看板子"、"抓个 logcat"、"装个 apk"、"板子起不来了"）
- 要看 Android 日志 / ANR / tombstone / crash
- 要跑 shell 命令在设备上
- 要推文件或拉文件
- 要重启 / 休眠 / 查电池
- 要走串口看 boot log / u-boot

---

## 不要做什么（优先读这块）

### ❌ 不要直接拼 adb 命令

```
# 错
run_bash("adb shell 'rm -rf /sdcard/tmp'")

# 对
await alb_shell("rm -rf /sdcard/tmp")
# 会被权限系统挡住并给你 suggestion
```

### ❌ 不要把大日志塞进回复

```
# 错
logcat = await alb_shell("logcat -d")    # 几 MB 输出
# 把 logcat 内容全贴给用户

# 对
r = await alb_logcat(duration=30, filter="*:E")
# 返回 summary + artifact path
# 需要细节时用 alb_log_search 按需读
```

### ❌ 不要用 `alb_shell` 做 alb 已经封装的事

| 不要做 | 用这个代替 |
|-------|----------|
| `alb_shell("logcat -d")` | `alb_logcat` |
| `alb_shell("dmesg")` | `alb_dmesg` |
| `alb_shell("pm install x.apk")` | `alb_app_install` |
| `alb_shell("reboot")` | `alb_reboot`（有权限流程） |
| `alb_shell("getprop")` | `alb_devinfo` |
| `alb_shell("dumpsys battery")` | `alb_battery` |
| `adb pull /data/anr/...` | `alb_anr_pull` |

### ❌ 不要假设设备在线

```
# 错：直接调命令
r = await alb_shell("getprop")  # 可能报 DEVICE_OFFLINE

# 对：先查状态
st = await alb_status()
if not st["data"]["devices"]:
    return "没有连接的设备"
```

### ❌ 不要绕权限系统

权限拦截的命令（返回 `PERMISSION_DENIED`）：
- `rm -rf /` / `rm -rf /sdcard`
- `reboot bootloader` / `fastboot erase`
- `setprop persist.*`
- `dd of=/dev/block/*`

**遇到 deny 时**：
1. 读 `error.suggestion`
2. 告诉用户需要授权或换方式
3. **不要尝试用 `--allow-dangerous` 自动绕** —— 让用户明确决定

### ❌ 不要一次性跑超长任务

```
# 错
r = await alb_logcat(duration=3600)   # 一小时阻塞

# 对
r = await alb_logcat_watch(...)       # 后台任务
# 或分段: 60s + 60s + 60s
```

### ❌ 不要硬编码路径

```
# 错
"adb pull /data/anr/anr_2026-04-15.txt"

# 对
await alb_anr_pull()
# 返回 artifacts 列表
```

---

## 推荐的工作姿势

### 1. 新会话先 `alb_status` + `alb_describe`

```
alb_status   → 当前 device / transport / 活跃任务
alb_describe → 所有可用 tool 的 schema（第一次用时有价值）
```

### 2. 出错先读 `error.suggestion`

```json
{
  "ok": false,
  "error": {
    "code": "TRANSPORT_NOT_CONFIGURED",
    "suggestion": "Run: alb setup adb"
  }
}
```

→ 告诉用户 `alb setup adb`，不要自己猜。

### 3. 长日志走 workspace

```
result = await alb_logcat(duration=60)
# result.artifacts[0] = "/workspace/.../xxx.txt"
# 读局部:
await alb_log_search(pattern="FATAL", path=result.artifacts[0])
await alb_log_tail(path=result.artifacts[0], lines=50)
```

### 4. 产物路径可预测

```
workspace/devices/<serial>/{logs,anr,bugreports,...}/
```

你可以告诉用户"logs 在 workspace/devices/<serial>/logs/"而不是贴内容。

---

## 常见工作流模板

### 调查 app crash

```
1. alb_status                                    # 确认设备
2. alb_app_start com.example                     # 启动
3. [crash happens]
4. alb_anr_pull                                  # 如果是 ANR
   or alb_tombstone_pull                         # 如果是 native crash
5. alb_log_search pattern="FATAL" path=<artifact>  # 定位
6. 给用户结论 + 产物路径
```

### 验证 patch 效果

```
1. alb_push patch.apk /data/local/tmp/
2. alb_app_install patch.apk --replace
3. alb_app_start com.example
4. alb_logcat duration=60 filter="MyApp:*"
5. alb_log_search pattern="expected behavior"  → 判断通过
```

### 板子起不来

```
1. 发现 alb_devices 没设备 / alb_shell 一直 offline
2. 切串口：
   alb_setup --method serial
   alb_uart_capture duration=30
3. alb_log_search pattern="panic|oops|fail|error" path=<artifact>
4. 给用户看：起到哪个阶段挂的
```

### 大目录推板子（方案 C ssh）

```
# 不要用 alb_push 推整个目录（全量）
# 用 alb_rsync 增量:
alb_rsync local_dir="~/aosp/out/..." remote_dir="/system-dev/"
```

---

## 错误码重点

看到这些错误时的标准处理：

| 错误码 | 你应该 |
|------|-------|
| `TRANSPORT_NOT_CONFIGURED` | 告诉用户跑 `alb setup <method>` |
| `DEVICE_NOT_FOUND` | `alb_devices` 让用户看列表 |
| `DEVICE_UNAUTHORIZED` | 告诉用户到设备屏幕点"允许 USB 调试" |
| `DEVICE_OFFLINE` | 建议断开重连 / `alb_wait_boot` |
| `PERMISSION_DENIED` | 读 suggestion，**不要绕** |
| `TIMEOUT_BOOT` | 怀疑 kernel panic，建议切 UART 方案 |
| `TRANSPORT_NOT_SUPPORTED` | 告诉用户切到支持的 transport |
| `WORKSPACE_FULL` | 建议 `alb workspace clean` |

完整表见 `docs/errors.md`，也可以调 `alb_describe_errors`。

---

## 方案选择参考（串口场景）

**用户说"板子起不来 / 黑屏 / 没反应" → 必须建议 UART**（方案 G）：
- adb / ssh 在系统起之前都用不了
- UART 是唯一能看 boot log / kernel panic 的通道

**用户说"adb 不通但有 WiFi" → 建议 B 方案**

**用户说"要刷机" → 必须 A 方案**

**用户说"大量文件传输" → 建议 C 方案（rsync）**

---

## 权限策略要点

- 默认黑名单拦下最危险命令（rm/reboot bootloader/setprop persist/dd 等）
- **你不能自己决定绕过** —— `allow_dangerous` 参数对 deny 级无效
- 遇到 ask 级，MCP 客户端会问用户，不是你做主
- 用户如果坚持要跑危险命令，告诉他在 config 里加 allow 规则（不要临时绕）

---

## 和其他工具的配合

- 写 Android 代码 → 用 Claude Code 原生 read/write/edit
- 跑命令 → 用 `Bash` tool（**不要用 Bash 调 adb 直接**，用 alb）
- 调试设备 → **alb 专属**

---

## 链接

- `docs/llm-integration.md` —— 完整集成指南
- `docs/errors.md` —— 错误码表
- `docs/capabilities/` —— 各能力详细文档
- `docs/permissions.md` —— 权限规则细节
- 配置示例: `llm/mcp-config-examples/claude-code.json`

---

## 更新日志

| 日期 | 变更 |
|------|-----|
| 2026-04-15 | 初版 |

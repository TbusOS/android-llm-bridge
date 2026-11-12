---
title: 错误码全集
type: reference
created: 2026-04-15
updated: 2026-11-10
owner: sky
tags: [errors, reference, llm-friendly]
---

# 错误码参考

> alb 的每个失败结果都带 `error.code`。第三节列出全部错误码:登记在 `src/alb/infra/errors.py` 的 `ERROR_CODES` 里的,以及代码里会返回、但还没登记的。
> 第三节由 `scripts/gen_errors_doc.py` 生成,不要手改;改了代码后运行 `uv run python scripts/gen_errors_doc.py`。`tests/test_docs_errors.py` 会检查它有没有过期。

---

## 一、错误结构

```json
{
  "code": "TRANSPORT_NOT_CONFIGURED",
  "message": "No active transport configured",
  "suggestion": "Run: alb setup adb  (or: alb setup ssh / serial)",
  "category": "transport",
  "details": {}
}
```

| 字段 | 含义 |
|-----|-----|
| `code` | 固定常量，LLM 可用于 switch/case |
| `message` | 人类可读的描述 |
| `suggestion` | LLM 下一步可执行的建议 |
| `category` | 粗分类，便于过滤 |
| `details` | 可选，结构化附加信息 |

`message` 和 `suggestion` 由出错的位置填写,常常比第三节列的默认文字更具体(带上路径、端口、命令的原始输出)。第三节列的是 `ERROR_CODES` 里的默认文字,也就是 `alb_describe_errors` 返回的那两句。

---

## 二、category 是干什么的

`category` 让调用方不必认识每个码,也能决定下一步:`transport` 先查通道,`input` 改参数再调,`timeout` 可以加大等待时间重试。没有它,每加一个新码,调用方的分支都要跟着改。

每个码的 category 是登记时人定的,同一前缀的码可能分在不同 category。例如 `FLASH_PLAN_CHECK_FAILED` 是 `device`,其余 `FLASH_*` 是 `transport`。按 category 分支时,以码自己登记的 category 为准,不要按前缀猜。8 个 category 的含义和码数见第三节开头的总览表。

---

## 三、错误码完整表

<!-- BEGIN GENERATED: scripts/gen_errors_doc.py -->
<!-- 本段由脚本生成,不要手改。改了 src/alb/infra/errors.py 或返回错误码的代码后,运行:uv run python scripts/gen_errors_doc.py -->

登记在 `ERROR_CODES` 里的码共 98 个,按 `category` 分 7 组,每组按码名字母排序。message 和 suggestion 是目录里的默认文字,原样照抄英文:`alb_describe_errors` 返回的就是这两句。

另有 61 个码代码里会返回、但没有登记,列在本节最后。

| category | 中文 | 含义 | 码数 | 码名前缀 |
|---|---|---|---|---|
| `transport` | 通道与台架 | 到板子的通道(adb / ssh / 串口)出了问题,或经台架 agent 做的操作(fastboot 烧录、9008、agent 更新与启动)失败 | 59 | `ADB_*` `AGENT_*` `BENCH_*` `BOARD_*` `EDL_*` `FASTBOOT_*` `FLASH_*` `PORT_*` `PSTORE_*` `SERIAL_*` `SHELL_*` `SSH_*` `TIMELINE_*` `TRANSPORT_*` |
| `device` | 对端状态 | 连接本身是通的,是对端(板子或台架主机)的状态或回答不对:USB 未授权、离线、adbd 不肯切 root、板子上的端口没人监听、台架主机上没有启动 agent 的任务 | 14 | `ADB_*` `AGENT_*` `BENCH_*` `BREAKIN_*` `DEVICE_*` `FLASH_*` `PORT_*` |
| `permission` | 权限 | 被权限策略拦下,或要 root 才能读 | 2 | `PERMISSION_*` `ROOT_*` |
| `timeout` | 超时 | 在限定时间内没等到预期的输出或状态 | 6 | `ADBD_*` `AGENT_*` `BREAKIN_*` `TIMEOUT_*` |
| `io` | 本地文件 | 本地文件、工作目录、路径 | 4 | `FILE_*` `REMOTE_*` `WORKSPACE_*` |
| `input` | 参数与配置 | 调用参数或 hub 配置不对,改了再试 | 10 | `AGENT_*` `BENCH_*` `BREAKIN_*` `INVALID_*` `PORT_*` |
| `system` | 主机环境与 LLM | 主机缺依赖,或 LLM 后端的上下文 / 输出被截断 | 3 | `LLM_*` `SYSTEM_*` |
| `capability` | 业务层 | `fail()` 不传 category 时的默认值;目录里目前没有这一类的码 | 0 | — |

### transport · 通道与台架(59 个)

| Code | 默认 message | 默认 suggestion |
|---|---|---|
| `ADB_BINARY_NOT_FOUND` | adb binary not found | Put adb on PATH, or set ALB_ADB_PATH to the adb executable |
| `ADB_COMMAND_FAILED` | adb command returned a non-zero exit code | Read error.details.stderr for the underlying adb message |
| `ADB_ENDPOINT_UNSUPPORTED` | The resolved adb server endpoint is not a tcp: socket | Set ADB_SERVER_SOCKET or config `server_socket` to `tcp:<host>:<port>` |
| `ADB_PROTOCOL_ERROR` | The endpoint did not answer like an adb server (bad reply, hung up, or timed out) | Check that the endpoint is an adb server: `alb status` |
| `ADB_SERVER_UNREACHABLE` | Cannot reach adb server | Check Xshell tunnel; run: ss -tlnp \| grep 5037 |
| `AGENT_START_SSH_FAILED` | ssh to the bench host failed; nothing was started | Run `ssh <host> echo ok` on the hub host: key not authorised (run setup-remote-start.bat on the bench host), sshd not running, host unreachable, or its host key not yet in known_hosts |
| `AGENT_UPDATE_CHECK_FAILED` | The new agent build failed its self-check on the bench host; nothing was replaced | Read the error text (syntax error, missing Python package, bad agent.conf key); fix it in the repo, or pip install the package on the bench host |
| `AGENT_UPDATE_NO_RETURN` | The agent restarted for an update and has not reconnected | Look at the bench host: the agent window, and .alb-agent/update.log in the agent's folder. It may still come back after its rollback finishes |
| `AGENT_UPDATE_REJECTED` | The agent refused the update bundle; it keeps running unchanged | Check the hub's clients/windows-agent folder (only flat file names, alb_agent.py present) and that the agent was started with a config file |
| `AGENT_UPDATE_ROLLED_BACK` | The new agent build did not reach the hub, so the old build was put back | `alb bench log update` shows what the update helper saw; the agent's own log (`alb bench log agent`) shows why the new build could not connect |
| `BENCH_AGENT_TOO_OLD` | The agent on the bench host predates this op | Copy the current alb_agent.py to the bench host and restart the agent |
| `BENCH_LOG_UNAVAILABLE` | The log source is configured on the agent but cannot be read right now | Check that the file exists on the bench host and the agent's account may read it; `alb bench ops` lists the sources this agent offers |
| `BENCH_OP_UNKNOWN` | The agent on the bench host has no such op | Run `alb bench ops` to see what this agent offers |
| `BENCH_PARAM_REJECTED` | A parameter value was refused (wrong type, out of range, or not one of the allowed choices) | Run `alb bench ops` — each op lists its parameters with their kinds and allowed values |
| `BENCH_TOOL_ARG_REJECTED` | An argument for the vendor tool was refused; the tool was not started | Name files relative to the package (the tool runs in it), or write {pkg}; no drive paths, network paths or '..'; {com} needs tool_usb in agent.conf; the error says which argument and which rule |
| `BENCH_TOOL_DEVICE_AMBIGUOUS` | More than one device registered for the tool (tool_usb) is attached to the bench host, and the request does not say which; the tool was not started | Choose one with port= when the tool's arguments use {com}; otherwise leave only the board to flash in download mode |
| `BENCH_TOOL_FAILED` | The vendor tool ran and failed: a line matched tool_fail, its exit code was not 0, or the tool_ok line never appeared | data.judged_by says which; read the output and the files it wrote (record: …/files/) before running it again |
| `BENCH_TOOL_NOT_STARTED` | The vendor tool could not be started on the bench host | Check tool_exe in the bench agent.conf; a missing DLL or a tool that needs administrator rights fails this way — run it once by hand on the bench host |
| `BENCH_TOOL_NO_DEVICE` | The USB device registered for the tool (tool_usb) is not attached to the bench host, or has no COM port | Put the board into the vendor's download mode; the error lists what is attached — a device with the right VID:PID but no COM port means its driver is missing |
| `BENCH_TOOL_PACKAGE_REJECTED` | The package directory is not usable on the bench host: badly formed, missing, or outside the tool's tool_root; the tool was not started | pkg is a directory relative to the tool's tool_root (`alb flash tools` shows it); copy the package there on the bench host first |
| `BENCH_TOOL_TIMEOUT` | The vendor tool did not finish within its tool_timeout and was ended, with everything it started | Read the last lines: a tool waiting for a board that never entered download mode looks like this; raise tool_timeout in agent.conf only for a tool that really runs that long |
| `BENCH_TOOL_UNKNOWN` | No vendor tool of that name is registered on the bench host | `alb flash tools` lists the registered tools; register one with tool_exe.\<name> and tool_root.\<name> in the bench agent.conf |
| `BOARD_BOOTING` | Board is still in a boot phase (SPL / kernel / init); shell not yet available | Wait a few seconds and retry, or use alb stream_read to watch progress |
| `BOARD_IN_FASTBOOT` | Board is in fastboot mode — shell commands are not applicable | Use adb / fastboot tools to leave fastboot, then retry |
| `BOARD_NEEDS_LOGIN` | Login prompt is waiting for a username | Send a username via `alb serial send`, then retry the command |
| `BOARD_PANICKED` | Board is in kernel panic; no further commands can run | Capture the tail (alb serial capture --duration 10) and reboot |
| `BOARD_UNREACHABLE` | No output observed on UART after handshake | Check power, UART wiring, baud rate, and the bridge / ser2net status |
| `EDL_FIREHOSE_FAILED` | fh_loader reported a failure (or did not report success) | Run edl-sahara first after every entry into 9008; read the tool output in the result — a write that stopped part-way leaves the board half-written, so run it again before resetting |
| `EDL_NO_DEVICE` | No board in 9008 (USB 05c6:9008 with a COM port) is attached to the agent host | Put the board into 9008 (it enters on its own after some boot errors, or by shorting the test point at power-on); `alb bench usb` shows whether it enumerated and whether the Qualcomm driver gave it a COM port |
| `EDL_PACKAGE_REJECTED` | The package directory or a file in it is not usable on the agent host: outside edl_root, missing, or not unique; nothing was sent to the board | dir is relative to edl_root in the bench agent.conf; xml and programmer are file names inside dir |
| `EDL_PORT_AMBIGUOUS` | More than one board in 9008 is attached to the agent host | Choose one with port=COMnn (the error lists the 9008 ports found) |
| `EDL_SAHARA_FAILED` | QSaharaServer did not load the firehose programmer | If edl-sahara already succeeded since the board entered 9008, go on with edl-program; otherwise put the board back into 9008 and run edl-sahara again |
| `FASTBOOT_BUSY` | Another flash job is already running on this agent | Wait for it to finish — the device can only serve one job at a time; `/agent/status` shows the job in flight |
| `FASTBOOT_NO_DEVICE` | No device is visible to fastboot on the agent host | Put the board in bootloader fastboot (`alb power reboot bootloader --allow-dangerous --no-wait-boot`); if it is there and still not listed, run `alb bench usb` to tell a missing driver from a cable that is not on the agent host |
| `FASTBOOT_UNAVAILABLE` | The connected agent has no fastboot executable | Install platform-tools on the agent host, or set fastboot_path in agent.conf, then restart the agent |
| `FLASH_AGENT_NO_SPACE` | The agent host has too little free temp space to receive the image | Free disk space on the agent host (the image is staged in its temp directory before fastboot runs) — nothing was transferred or written |
| `FLASH_BOARD_MISMATCH` | The board in fastboot is not the chosen bench board: `fastboot getvar product` answered something other than its fastboot_product in agent.conf, or nothing; nothing was written | Check which board is on the USB cable; switch with `alb bench board use <name>` if the hub has the wrong one chosen |
| `FLASH_BOARD_NOT_LISTED` | The agent keeps a partition list per board, and none applies: no bench board is chosen, or the agent has no list for the chosen one | Choose the board on the cable with `alb bench board use <name>`, or add flash_partitions.\<board> to agent.conf on the bench host |
| `FLASH_DEVICE_AMBIGUOUS` | More than one board is in fastboot on the agent host; nothing was run | Leave only the board to work on in fastboot — reboot the others to the system or unplug them; the error lists the boards found (`alb bench usb` shows them too) |
| `FLASH_FAILED` | fastboot ran but reported a failure | Read the captured fastboot output: a `FAILED (remote: '...')` line is the bootloader's own answer — it refused or could not do the command, which alb passed on as is. Only a failed flash or erase can leave a partition half-written; re-flash that partition before rebooting |
| `FLASH_IMAGE_CORRUPT` | The image did not survive the transfer intact (digest mismatch) | Retry — nothing was written to the device, the digest is checked before fastboot is invoked |
| `FLASH_OEM_REJECTED` | The agent refused the oem command before sending it — the text does not have a shape that reaches the board intact | Any oem command is passed on; only these are refused: empty, starting with '-' (fastboot would read it as its own option) or whitespace, control characters, or longer than 256 characters (see _OEM_RE in alb_agent.py). An agent older than 2026-11-05 still only passes at-lock-vboot / at-unlock-vboot — update it with `alb bench agent-update` |
| `FLASH_PARTITION_REJECTED` | The agent refused the requested partition name | Use a partition on the agent's allowlist, or widen it in agent.conf if you are certain |
| `FLASH_PLAN_CHANGED` | The plan differs from the one that was confirmed (an image, the manifest or the skips changed); no row was executed | Run the command again and confirm the plan as it is now |
| `FLASH_PLAN_REJECTED` | The flash plan has problems; no row was executed | Run `alb flash plan <manifest> --dry-run` to see every problem with its line number |
| `FLASH_VAR_REJECTED` | The agent refused the getvar variable name (it is not a well-formed name) | Use a plain variable name such as `partition-size:boot_a`, or none for `getvar all` |
| `PORT_LOCAL_BUSY` | The requested local port is already in use | Pick another with `--local`, or omit it to take a free port |
| `PSTORE_PULL_FAILED` | Crash records were listed on the device but none could be pulled | Read each file's error in the result; run `alb adb root` if they say permission denied, otherwise check `alb devices` |
| `SERIAL_BAUD_MISMATCH` | UART stream is mostly non-printable — likely baud-rate mismatch | Try common baud rates (115200 / 921600 / 1500000) or run `alb serial probe` |
| `SERIAL_LINK_LOST` | The serial link dropped during the attempt and could not be reopened | Check the bridge / agent (alb serial health); nothing about the board is implied |
| `SERIAL_LINK_REFUSED` | The serial endpoint accepted the connection then closed it without a byte — the bridge never reached the UART | Not a board fault. Check the bridge: the alb-api log carries the agent's own reason (usually another program holds the port), or `alb serial health` |
| `SERIAL_PORT_BUSY` | Serial port is held exclusively by another program | Close the terminal emulator / flashing tool holding the port, then retry (concurrent alb readers share one link and do NOT conflict) |
| `SERIAL_PORT_NOT_FOUND` | Serial port not found | Check ser2net config and the reverse tunnel |
| `SERIAL_SEND_UNCONFIRMED` | Serial write was accepted locally but the link died before the bytes could reach the UART | Check the bridge / agent is up (alb serial health), then resend |
| `SHELL_NONZERO_EXIT` | Shell command exited with a non-zero status | Inspect exit_code + stderr; fix the command or handle the failure |
| `SSH_AUTH_FAILED` | SSH authentication failed | Check key or run: ssh-copy-id \<host> |
| `TIMELINE_NO_SOURCE` | None of the requested timeline sources could be read; nothing was captured | Read the notes: dmesg usually needs `alb adb root`, uart needs `alb setup serial`, logcat needs an adb device |
| `TRANSPORT_NOT_CONFIGURED` | No active transport configured | Run: alb setup adb  (or: alb setup ssh / serial) |
| `TRANSPORT_NOT_SUPPORTED` | Operation not supported by this transport | Switch transport: alb setup \<other-method> |

### device · 对端状态(14 个)

| Code | 默认 message | 默认 suggestion |
|---|---|---|
| `ADB_REMOUNT_FAILED` | adb remount reported a failure (output passed through verbatim) | Run `alb adb root` first; if the output mentions verity, disable it yourself with `adb disable-verity` and reboot — alb never runs that automatically |
| `ADB_ROOT_REFUSED` | adbd refused to run as root (production build), or kept its uid after `adb root` | For single commands prefix `su 0 <cmd>` (toybox su takes the uid first, not `su -c`); for a root adbd flash a userdebug / eng build |
| `ADB_UNROOT_NO_EFFECT` | adb unroot restarted adbd but it came back as root; this build keeps adbd root | Nothing to retry. To run one command as the shell user, prefix `su 2000 <cmd>` |
| `AGENT_START_NO_TASK` | The bench host has no scheduled task to start the agent with | On the bench host run setup-remote-start.bat in the agent folder once |
| `AGENT_START_TASK_FAILED` | The bench host's task did not start, or ended at once | The task runs only while a user is logged on to the bench host; if one is, the agent log lines shown say why run-agent.bat stopped |
| `BENCH_SERIAL_BUSY` | Someone is reading the serial console; the board was not switched | End the capture / shell / web console first, or pass --force to cut them off |
| `BREAKIN_BOOTED_PAST` | The countdown appeared and keys were sent, but the board went on to start its kernel | The key came too late (earlier --expect, lower --repeat-ms) or was the wrong one (--send '\x03' / '\r'); if this build has the bootloader command line turned off, no key stops it |
| `DEVICE_NOT_FOUND` | Specified device serial not found | Run: alb devices |
| `DEVICE_OFFLINE` | Device is offline | Reconnect USB or run: alb devices |
| `DEVICE_UNAUTHORIZED` | Device rejected USB debugging | Accept 'Allow USB debugging' on device screen |
| `FLASH_GETVAR_FAILED` | fastboot getvar printed FAILED although it exited 0; there is no value to read | Read the quoted line. `remote: '...'` is the device's answer — most often this bootloader has no variable by that name (`alb flash getvar` with no name lists the ones it has). Anything else is a local error on the way (USB) |
| `FLASH_PLAN_CHECK_FAILED` | A getvar check in the plan reported FAILED; the rows after it were not run | Read the quoted line. `remote: '...'` is the device's answer — a firmware / board check failing usually means this package is for another board; skip the check (--skip-action getvar) only if you are sure. Anything else is a local error on the way (USB) — fix the link and run the plan again |
| `PORT_DEVICE_AMBIGUOUS` | More than one device is attached; the target must be named | Run `alb devices` and pass `--serial <one of them>` |
| `PORT_DEVICE_REFUSED` | The device refused the connection to that port (nothing is listening there) | Start the server on the device first (e.g. `gdbserver :<port> --attach <pid>`), then `alb port check <port>` |

### permission · 权限(2 个)

| Code | 默认 message | 默认 suggestion |
|---|---|---|
| `PERMISSION_DENIED` | Command blocked by permission policy | Read error.details.matched_rule; scope narrower or add allow rule |
| `ROOT_REQUIRED` | The device path cannot be read by the shell user | Run `alb adb root`, then retry |

### timeout · 超时(6 个)

| Code | 默认 message | 默认 suggestion |
|---|---|---|
| `ADBD_RESTART_TIMEOUT` | adbd did not come back with the requested uid within the wait | Run `alb devices`; over a tunnel the reconnect can take longer — retry with a larger --timeout |
| `AGENT_START_NO_CONNECT` | The agent's task is running on the bench host but no agent reached this hub | Read the agent log lines shown: hub_url / token in agent.conf, or the network between the bench host and this hub |
| `BREAKIN_NO_PROMPT` | The countdown appeared and keys were sent, but no console prompt followed | Run `alb serial status` to see where the board is; at an unrecognised prompt (a bare '#' or '$' is not accepted by default) pass --prompt; if it kept booting try --send '\r' or '\x03' and a smaller --repeat-ms |
| `BREAKIN_PATTERN_NOT_SEEN` | The bootloader countdown text never appeared on the UART within the timeout | Read the tail: if the board went straight into the kernel, this bootloader does not offer a key-press stop or prints other text (pass --expect); if nothing arrived, check that the board actually reset and the UART / baud (alb serial status) |
| `TIMEOUT_BOOT` | Device did not finish booting in time | Device may have kernel panic; check UART (method G) |
| `TIMEOUT_SHELL` | Shell command timed out | Increase timeout param or use stream_read |

### io · 本地文件(4 个)

| Code | 默认 message | 默认 suggestion |
|---|---|---|
| `FILE_NOT_FOUND` | Local file not found | Check the path |
| `FILE_NOT_READABLE` | local file cannot be read | Check file permissions |
| `REMOTE_PATH_INVALID` | remote/local path is not allowed | Path must stay inside workspace; avoid traversal |
| `WORKSPACE_FULL` | Workspace disk is full | Run: alb workspace clean --older-than 7d |

### input · 参数与配置(10 个)

| Code | 默认 message | 默认 suggestion |
|---|---|---|
| `AGENT_START_NOT_CONFIGURED` | This hub does not know how to reach the bench host over ssh | Set ALB_BENCH_SSH_HOST in deploy/hub/hub.env to a Host from the hub user's ~/.ssh/config and restart alb-api |
| `BENCH_BOARDS_INVALID` | The bench board table cannot be used | Fix the file and entry named in the message; nothing was switched |
| `BENCH_BOARDS_NOT_CONFIGURED` | This hub has no bench board table | Set ALB_BENCH_BOARDS in deploy/hub/hub.env to a boards TOML file (see deploy/hub/boards.toml.example) and restart alb-api |
| `BENCH_BOARD_UNKNOWN` | No board by that name in the bench board table | `alb bench board` lists the names; add the board to the table first |
| `BREAKIN_BAD_ARGUMENT` | A break-in option is not usable (unknown reboot mode, nothing to send) | See `alb serial break-in --help` |
| `INVALID_DEVICE_SERIAL` | device serial is malformed | Use a serial returned by alb devices |
| `INVALID_DURATION` | duration out of range | Use a value between 1 and 3600 seconds |
| `INVALID_FILTER` | filter expression is invalid | See docs/capabilities/logging.md for logcat filter syntax |
| `INVALID_SOURCE` | unknown or empty log source list | Choose at least one of: logcat, dmesg, uart |
| `PORT_BIND_NOT_LOOPBACK` | Refusing to expose a device port on a non-loopback address | Bind 127.0.0.1, or pass `--allow-remote` if sharing it on the network is intended |

### system · 主机环境与 LLM(3 个)

| Code | 默认 message | 默认 suggestion |
|---|---|---|
| `LLM_CONTEXT_TRUNCATED` | The request filled the model's whole context window; the LLM server cut the start off, so the model saw only part of it | Raise ALB_OLLAMA_NUM_CTX, turn tools off for this chat, or start a new session |
| `LLM_OUTPUT_TRUNCATED` | The answer reached the agent's output limit per model turn (alb.agent.loop.DEFAULT_MAX_OUTPUT_TOKENS) and was cut off | Ask again or ask for a shorter answer; a reply that repeats itself until the limit is the model looping, a new session usually helps |
| `SYSTEM_DEPENDENCY_MISSING` | Missing system dependency | Install the missing tool per error.details |

### 代码里会返回、但没有登记的码(61 个)

这些码会出现在 `error.code` 里,但 `ERROR_CODES` 没有它们的条目,`alb_describe_errors` 查不到;message 和 suggestion 只有出错位置当场填写的那一份。其中几个码的中文说明见「部分错误码的补充说明」一节。返回位置由脚本扫描 `src/alb` 得出,扫描规则见 `scripts/gen_errors_doc.py` 的 `_Scanner`。

| Code | 返回位置 |
|---|---|
| `AGENT_MAX_TURNS_EXCEEDED` | `src/alb/agent/loop.py` |
| `APK_TOO_LARGE` | `src/alb/api/app_route.py` |
| `APP_INSTALL_FAILED` | `src/alb/capabilities/app.py` |
| `APP_NOT_INSTALLED` | `src/alb/capabilities/app.py` |
| `BACKEND_BUILD_FAILED` | `src/alb/api/playground_route.py` |
| `BACKEND_HTTP_ERROR` | `src/alb/agent/backends/anthropic.py`<br>`src/alb/agent/backends/ollama.py`<br>`src/alb/agent/backends/openai_compat.py` |
| `BACKEND_INIT_FAILED` | `src/alb/api/chat_route.py` |
| `BACKEND_MISCONFIGURED` | `src/alb/agent/backends/anthropic.py`<br>`src/alb/agent/backends/openai_compat.py` |
| `BACKEND_NOT_IMPLEMENTED` | `src/alb/api/playground_route.py`<br>`src/alb/mcp/tools/playground.py` |
| `BACKEND_RATE_LIMITED` | `src/alb/agent/backends/anthropic.py` |
| `BACKEND_TIMEOUT` | `src/alb/agent/backends/anthropic.py`<br>`src/alb/agent/backends/ollama.py`<br>`src/alb/agent/backends/openai_compat.py` |
| `BACKEND_UNAUTHORIZED` | `src/alb/agent/backends/anthropic.py` |
| `BACKEND_UNREACHABLE` | `src/alb/agent/backends/anthropic.py`<br>`src/alb/agent/backends/ollama.py`<br>`src/alb/agent/backends/openai_compat.py` |
| `BATTERY_UNAVAILABLE` | `src/alb/capabilities/info.py` |
| `BOARD_CONFIG_BAD_DEVICE` | `src/alb/capabilities/board_config.py` |
| `BOARD_CONFIG_READ_FAILED` | `src/alb/capabilities/board_config.py` |
| `BOARD_CONFIG_SCAN_FAILED` | `src/alb/capabilities/board_config.py` |
| `BREAKIN_FAILED` | `src/alb/api/serial_route.py` |
| `BUGREPORT_FAILED` | `src/alb/capabilities/diagnose.py` |
| `CHAT_STOPPED` | `src/alb/agent/loop.py`<br>`src/alb/api/chat_route.py` |
| `COMPONENT_INVALID` | `src/alb/capabilities/app.py` |
| `INVALID_DEVICE` | `src/alb/api/terminal_route.py`<br>`src/alb/capabilities/logging.py` |
| `INVALID_FILENAME` | `src/alb/api/app_route.py` |
| `INVALID_LOGCAT_BUFFER` | `src/alb/capabilities/timeline.py` |
| `INVALID_REQUEST` | `src/alb/api/chat_route.py`<br>`src/alb/api/playground_route.py` |
| `INVALID_SESSION_ID` | `src/alb/api/chat_route.py`<br>`src/alb/api/terminal_route.py` |
| `MEMINFO_UNREADABLE` | `src/alb/capabilities/info.py` |
| `METRICS_SAMPLE_FAILED` | `src/alb/capabilities/metrics.py` |
| `NO_BATTERY` | `src/alb/capabilities/info.py` |
| `PACKAGE_NAME_INVALID` | `src/alb/capabilities/app.py` |
| `PATTERN_TIMEOUT` | `src/alb/capabilities/logging.py` |
| `PLAYGROUND_INTERNAL` | `src/alb/api/playground_route.py` |
| `PM_UNAVAILABLE` | `src/alb/capabilities/info.py` |
| `PTY_SPAWN_FAILED` | `src/alb/api/terminal_route.py` |
| `REBOOT_FAILED` | `src/alb/api/timeline_route.py` |
| `SCREENCAP_FAILED` | `src/alb/capabilities/ui.py` |
| `SCREENSHOT_NOT_PNG` | `src/alb/capabilities/ui.py` |
| `SCREENSHOT_PULL_FAILED` | `src/alb/capabilities/ui.py` |
| `SCREENSHOT_READ_FAILED` | `src/alb/capabilities/ui.py` |
| `SERIAL_PERMISSION_DENIED` | `src/alb/transport/serial.py` |
| `SESSION_NOT_FOUND` | `src/alb/api/chat_route.py` |
| `SSH_COMMAND_FAILED` | `src/alb/capabilities/app.py`<br>`src/alb/capabilities/filesync.py`<br>`src/alb/transport/ssh.py` |
| `SSH_HOST_UNREACHABLE` | `src/alb/transport/ssh.py` |
| `SSH_KEY_NOT_FOUND` | `src/alb/transport/ssh.py` |
| `TIMELINE_CAPTURE_FAILED` | `src/alb/api/timeline_route.py` |
| `TIMEOUT_CONNECT` | `src/alb/transport/ssh.py` |
| `TOOL_CALL_FAILED` | `src/alb/api/chat_route.py`<br>`src/alb/cli/chat_cli.py`<br>`src/alb/mcp/executor.py` |
| `TOOL_CONFIRM_TIMEOUT` | `src/alb/api/chat_route.py` |
| `TOOL_DECLINED` | `src/alb/api/chat_route.py` |
| `TOOL_EXECUTOR_RAISED` | `src/alb/agent/loop.py` |
| `TOOL_NOT_FOUND` | `src/alb/agent/loop.py` |
| `TRANSPORT_INIT_FAILED` | `src/alb/infra/result.py` |
| `TRANSPORT_NO_PTY` | `src/alb/api/terminal_route.py` |
| `UART_CAPTURE_FAILED` | `src/alb/capabilities/logging.py` |
| `UART_SEND_FAILED` | `src/alb/capabilities/logging.py` |
| `UIAUTOMATOR_FAILED` | `src/alb/capabilities/ui.py` |
| `UIDUMP_PARSE_FAILED` | `src/alb/capabilities/ui.py` |
| `UIDUMP_PULL_FAILED` | `src/alb/capabilities/ui.py` |
| `UIDUMP_READ_FAILED` | `src/alb/capabilities/ui.py` |
| `UNKNOWN_BACKEND` | `src/alb/api/playground_route.py` |
| `UNKNOWN_PANEL` | `src/alb/mcp/tools/info.py` |
<!-- END GENERATED -->

---

## 四、部分错误码的补充说明

第三节只有英文默认文字。下面这些码,默认文字说不清来源或范围,或者没有登记、`alb shell` 失败时只给一句 `See docs/errors.md for details`,这里补中文说明。每行都写了依据的文件和函数名。

| Code | 补充说明 |
|------|---------|
| `PERMISSION_DENIED` | 按现在的代码不会出现。2026-10-23 起板子上的命令不再拦截:默认检查 `default_check` 对所有动作都放行(`src/alb/infra/permissions.py`);配置里 `[permissions]` 的 allow / deny 只被解析(`src/alb/infra/config.py` 的 `_parse_perm`),检查时不读。`fail(code="PERMISSION_DENIED")` 的调用点还留着,给以后主机侧的规则用。 |
| `INVALID_DURATION` | 合法范围随调用处不同,以 message 为准。logcat / dmesg / 串口抓取和 timeline:1 到 3600 秒(`src/alb/capabilities/logging.py`、`timeline.py`);串口 break-in 的几个等待时间:大于 0 即可(`breakin.py`);sleep / wake 测试的循环次数(1 到 1000)越界也报这个码(`power.py` 的 `sleep_wake_test`)。 |
| `SYSTEM_DEPENDENCY_MISSING` | 缺的是主机上的东西:ssh 方式同步文件要的 `rsync` / `ssh` 可执行文件,或 ssh 用的 Python 包 `asyncssh`、串口用的 `serial_asyncio` 没装(`ImportError`)。缺哪个写在 message 里。出处:`src/alb/transport/ssh.py`、`src/alb/transport/serial.py` 的 `_classify_connect_error`。 |
| `LLM_CONTEXT_TRUNCATED` | 只在 Ollama 后端出现。判据:Ollama 回报的提示 token 数大于等于发出去的 `num_ctx`。Ollama 截掉的是开头,而工具清单和说明就在开头,所以这一轮的回答不可信。`num_ctx` 默认 16384,用 `ALB_OLLAMA_NUM_CTX` 改。出处:`src/alb/agent/backends/ollama.py` 的 `_raise_if_truncated` 与 `DEFAULT_NUM_CTX`。 |
| `LLM_OUTPUT_TRUNCATED` | 一轮回答的输出上限是 4096 token(`src/alb/agent/loop.py` 的 `DEFAULT_MAX_OUTPUT_TOKENS`),已经显示的部分保留。多半是模型在原地打转、一直重复,新开会话通常能恢复。 |
| `APP_INSTALL_FAILED` | 未登记。安装失败时,如果 pm 的输出里有 `INSTALL_FAILED_*`(例如 `INSTALL_FAILED_VERSION_DOWNGRADE`),`error.code` 直接就是这个 Android 原生的码;找不到才是 `APP_INSTALL_FAILED`(`src/alb/capabilities/app.py` 的 `_classify_install_error`)。看到 `INSTALL_FAILED_` 开头的码,查 Android `PackageManager` 的说明。 |
| `SERIAL_PERMISSION_DENIED` | 未登记。打开串口被拒绝:本机打开时抛了 `PermissionError`,或台架 agent / 串口桥回报 permission denied(`src/alb/transport/serial.py` 的 `_classify_connect_error`、`classify_open_failure`)。Linux 本机串口:把当前用户加进 `dialout` 组,重新登录后生效。经台架时看 agent 日志里的原因。 |
| `SSH_HOST_UNREACHABLE` | 未登记。ssh 连接报 connection refused、no route to host、network is unreachable 或域名解析失败。先在本机 `ping <host>`、`ssh <host>` 确认网络和对方的 sshd。出处:`src/alb/transport/ssh.py` 的 `_classify_ssh_error`,下面三行同。 |
| `TIMEOUT_CONNECT` | 未登记。ssh 连接超时。查网络、防火墙和隧道。 |
| `SSH_KEY_NOT_FOUND` | 未登记。ssh 报错里同时有 "no such file" 和 "key":配置的私钥文件不存在。检查 `ALB_SSH_KEY` 指的路径,或用 `ssh-keygen -t ed25519` 生成一把。 |
| `SSH_COMMAND_FAILED` | 未登记。ssh 失败、又不属于上面几类时的兜底码。原因看 message 里 ssh 的原话。 |

---

## 五、LLM 处理模式

### 模式 1 · 按 code 直接分支

```python
result = await alb_logcat(...)
if not result["ok"]:
    code = result["error"]["code"]
    if code == "TRANSPORT_NOT_CONFIGURED":
        # alb setup 是交互式命令，交给人来跑
        return "还没配置连接方式，请先运行 alb setup adb"
    elif code == "DEVICE_UNAUTHORIZED":
        return "请在设备上点击'允许 USB 调试'"
    elif code == "TIMEOUT_SHELL":
        # 加大 timeout 重试一次
        ...
```

### 模式 2 · 按 category 批量处理

```python
if result["error"]["category"] == "permission":
    # 用户决策 / 向用户请求
    ...
elif result["error"]["category"] == "transport":
    # 先看连接状态
    await alb_status()
```

### 模式 3 · 把 suggestion 转给人

```python
# 某些 suggestion 是具体的命令
sugg = result["error"]["suggestion"]
if sugg.startswith("Run: "):
    # 提示用户（不要自动跑，suggestion 仍需要人类判断）
    print(f"建议: {sugg}")
```

---

## 六、错误码编程访问

MCP tool `alb_describe_errors`(`src/alb/mcp/tools/devices.py`):

```python
alb_describe_errors(code="DEVICE_OFFLINE")  # 单个码:category / default_message / default_suggestion
alb_describe_errors(search="flash")         # 只要码名或 category 含这个词的,不分大小写
alb_describe_errors()                       # 整个目录;很长,优先用 search
```

它只能查到登记了的码,第三节最后一组查不到。

命令行没有 `alb describe-errors` 子命令。在命令行下查,看本页,或看 `alb skills preview` 输出里的错误码表。

---

## 七、贡献新错误码

1. 在 `src/alb/infra/errors.py` 的 `ERROR_CODES` 加条目:code、category、default_message、default_suggestion。category 取 `src/alb/infra/result.py` 里 `ErrorCategory` 的某一个。
2. 在出错的位置用 `fail(code="YOUR_NEW_CODE", ...)` 返回。
3. 运行 `uv run python scripts/gen_errors_doc.py`,重新生成第三节。要补中文说明,加到第四节的表里。
4. 运行 `uv run pytest -q tests/test_docs_errors.py`。文档过期,或第四节写了一个既没登记、代码里也没返回的码,这个测试会失败。
5. PR 里说明场景和 suggestion 的写法。

只写 `fail(code=...)` 不登记,这个码会出现在第三节最后一组。测试不拦这种情况,但请尽量登记:登记了,`alb_describe_errors` 才能解释它。

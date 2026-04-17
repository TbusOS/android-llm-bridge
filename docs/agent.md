---
title: Agent 层设计 —— 可插拔 LLM Backend + 本地小模型支持
type: design-doc
created: 2026-04-16
updated: 2026-04-16
owner: sky
tags: [agent, llm, local-model, chat, architecture]
status: skeleton (M1) / impl M2-M3
---

# Agent 层设计

> `src/alb/agent/` —— LLM 编排层。解耦"用哪个模型驱动工具"，让 `alb` 既能被 Claude Code 驱动，也能在**服务器无显卡 / 无外网**环境下靠本地小模型独立工作。

> **状态**：M1 预留骨架（ABC + NotImplementedError），具体 backend 和 UI 分 M2 / M3 实现。本文是**设计文档 + 实施路线**，骨架代码在 `src/alb/agent/`。

---

## 一、为什么要有 Agent 层

### 三种驱动方式并存，互不冲突

我们的 `alb` 工具最终要被"某个 LLM"驱动去操作设备。LLM 在哪里跑，决定了驱动方式：

| # | 场景 | 驱动方 | LLM 在哪里 | alb 这边要做什么 |
|---|------|--------|------------|-------------------|
| 1 | 开发者日常 | **Claude Code + MCP** | Anthropic 云端，Claude Code 进程 | 暴露 MCP server（M1 已就绪） |
| 2 | 开发者日常 / 脚本化 | **Claude Code + Bash** | Anthropic 云端，Claude Code 进程 | 提供 `alb <cmd>` + `SKILL.md`（M1 已就绪） |
| 3 | **无 Claude Code 环境** | **`alb chat` / Web `/chat`** | **本地小模型（纯 CPU）** 或远程 API | 本文档要设计的 agent 层 |

前两种是"外部 LLM 驱动 alb"，第三种是"alb 内嵌 LLM 编排"。**三者共享同一套 capability / transport / 权限系统**，只是入口不同。

### 为什么一定要做第三种？

常见场景：
- **客户现场 / 实验室服务器没显卡**：机器只能跑 CPU，装不了大模型
- **合规 / 内网隔离**：设备日志不允许经过外网 API
- **没有 Claude Code**：用户只想在 shell 里 `alb chat` 一句话搞定抓日志 / 升级
- **CI 自动化**：流水线里嵌一个小 agent 跑"装 apk → 看启动 → 抓日志"固定流程

这些场景下，需要一个**完全本地、纯 CPU 可跑**的编排层。

### 为什么小模型能胜任？

因为我们**只让它做"工具路由"**，不让它做"分析"：

```
用户："帮我抓 30 秒 logcat，过滤 E 级别以上"
    ↓ 小模型只需输出
{tool: "alb_logcat", args: {duration: 30, filter: "*:E"}}
    ↓ alb 执行、落盘
返回：{artifact: "workspace/.../logcat.txt", lines: 2341, errors: 12}
    ↓ 小模型组织人话
"已抓 30 秒日志，12 条 error，文件保存在 workspace/..."
```

这是 3B 级模型（Qwen2.5-3B / Llama-3.2-3B）能稳定做到的任务。**不要让它分析日志内容**——那是 Claude / GPT 级别的事。

---

## 二、分层位置

```
L1    CLI / MCP / Web API           ← 接入层
L1.5  Agent (本文档)  ─────────────  ← 新增：LLM 编排
L2    Capabilities                  ← 业务能力
L3    Transport ABC                 ← 传输抽象
L4    Drivers
L5    Infra
```

- **L1.5 是 L1 的"增强版"**：`alb chat` 是 CLI 的子命令，`/chat` 是 Web API 的路由
- **L1.5 调 L2 能力**：通过 `tool_executor` 回调复用 MCP tool 注册表
- **L1.5 不直接碰 L3/L4**：保持"编排 / 执行"解耦

### 四种驱动方式的统一视图

```
                          ┌────────────── 外部 LLM 驱动 ──────────────┐
                          │                                            │
                     Claude Code                                Claude Code
                       (MCP)                                      (Bash)
                          ↓                                         ↓
                    alb-mcp stdio                          alb <subcommand>
                          ↓                                         ↓
┌─────────────────────────┴─────────────────────────────────────────┴──────┐
│  L2 Capabilities (shell / logging / filesync / diagnose / power / app)   │
└─────────────────────────┬─────────────────────────────────────────┬──────┘
                          ↑                                         ↑
                    tool_executor                              (直接调用)
                          ↑                                         ↑
                  ┌───────┴──────┐                         ┌───────┴───────┐
                  │  AgentLoop   │                         │ 终端用户手敲   │
                  └───────┬──────┘                         └───────────────┘
                          ↑
                    LLMBackend ABC
                  ┌───────┴────────┬─────────────┬──────────┐
             OllamaBackend  OpenAICompat   LlamaCpp    Anthropic
              (本地 CPU)    (vLLM/etc)    (嵌入式)    (API 备选)
                          │
                    ┌─────┴──────┐
                alb chat      Web /chat
                  (CLI REPL)   (WebSocket / SSE)
                          │
                          └── 第三种场景：alb 内嵌 LLM 编排
```

---

## 三、模块结构（`src/alb/agent/`）

```
src/alb/agent/
├── __init__.py        # 导出 LLMBackend / AgentLoop / ChatSession / Message / ToolCall / ToolSpec
├── backend.py         # LLMBackend ABC + Message / ToolCall / ToolSpec / ChatResponse / BackendError
├── loop.py            # AgentLoop (tool-calling 循环) + DEFAULT_SYSTEM_PROMPT
├── session.py         # ChatSession (JSONL 持久化到 workspace/sessions/)
└── backends/          # M2/M3 逐个实现，M1 为空
    ├── __init__.py
    ├── ollama.py          (M2)
    ├── openai_compat.py   (M2)
    ├── llama_cpp.py       (M3)
    └── anthropic.py       (M3)
```

### 3.1 `backend.py` —— LLMBackend ABC

核心契约：

```python
class LLMBackend(ABC):
    name: str = "base"
    model: str = ""
    supports_tool_calls: bool = False
    supports_streaming: bool = False
    runs_on_cpu: bool = False

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs,
    ) -> ChatResponse: ...

    async def stream(self, ...) -> AsyncIterator[str]:
        """可选。默认 NotImplementedError。"""

    async def health(self) -> dict: ...
```

消息原语沿用 OpenAI / Anthropic 通用格式：`Message(role, content, tool_calls, tool_call_id, name)`，`ToolCall(id, name, arguments)`，`ToolSpec(name, description, parameters)`。这样 backends 可以把 `parameters` JSON-schema 直接转发给下游 API。

**错误处理**：backends 可以 `raise BackendError("CODE", "msg", suggestion="...")`；`AgentLoop` 捕获后翻译成 `Result(ok=False)`。这是唯一允许"raise 而不 wrap"的层——因为 agent loop 是它的唯一调用方。

### 3.2 `loop.py` —— AgentLoop

Shallow agent，只做 ReAct-lite：

```python
class AgentLoop:
    def __init__(
        self,
        backend: LLMBackend,
        tools: list[ToolSpec],
        tool_executor: ToolExecutor,  # async (name, args) -> result dict
        *,
        max_turns: int = 8,
        system_prompt: str | None = None,
    ): ...

    async def run(
        self,
        user_input: str,
        *,
        session: ChatSession | None = None,
    ) -> Result[str]:
        """返回 data=最终 assistant 文本，artifacts 聚合工具产物"""
```

**循环伪码**：

```
messages = [system_prompt, *session.messages(), user_input]
for turn in range(max_turns):
    resp = await backend.chat(messages, tools=tools)
    messages.append(assistant_message(resp))
    if not resp.tool_calls:
        break                                       # 模型停了
    for tc in resp.tool_calls:
        result = await tool_executor(tc.name, tc.arguments)
        messages.append(tool_message(tc.id, result))
return Result(ok=True, data=resp.content, artifacts=collected)
```

**不做**：多 agent 并行 / 工具用法学习 / 反思 / 规划。这些要留给更大的模型（Claude Code）。

### 3.3 `session.py` —— ChatSession

按 ADR-007（长内容不塞 LLM）落盘到 `workspace/sessions/<session-id>/`：

```
workspace/sessions/20260416-a3f8c2d1/
├── messages.jsonl        # 每行一条 Message（含 tool_calls / tool_results）
├── meta.json             # {backend, model, device, created}
└── summary.md            # M3: LLM 生成的会话摘要
```

- `ChatSession.create()` 新建 session，目录自动创建
- `ChatSession.load(id)` 恢复（M3 实现 JSONL 解析）
- `append(message)` 追加（M3 实现落盘 flush）

---

## 四、Backend 选型建议

### 推荐：Ollama + Qwen2.5-3B（CPU 默认）

| 项 | 值 |
|----|-----|
| 模型 | `qwen2.5:3b-instruct-q4_K_M` |
| 体积 | ~2.0 GB |
| 内存占用 | ~3 GB RAM |
| 速度（CPU） | 8-15 tokens/s（8 核 x86） |
| Tool calling | ✅ 原生支持 |
| 中文 | ✅ 优秀 |
| 英文 | ✅ 良好 |

**为什么选 Ollama 作为默认**：
- HTTP API 简单稳定（`POST /api/chat`）
- 有守护进程管理（auto-unload / 并发请求排队）
- 跨平台（Linux / macOS / Windows WSL）
- 模型下载一行：`ollama pull qwen2.5:3b`

**为什么选 Qwen2.5-3B**：
- 中文兼容最好（国内设备调试日常中文输入）
- Tool calling 训练质量高，小于 7B 也能稳
- Apache 2.0 许可

### 备选：OpenAI-compatible endpoint

适合场景：已经部署了 vLLM / LM Studio / llamafile / TGI 服务器。复用同一个 endpoint。

### 备选：llama.cpp 嵌入式

适合场景：不能装 Ollama 守护进程的极简环境（Docker container / embedded device）。
`llama-cpp-python` 直接在 `alb` 进程里加载 GGUF。

### 高阶：Anthropic Claude API

适合场景：用户愿意用 Claude 驱动 `alb chat` 而不想装 Claude Code。此时对模型能力要求高的"分析"类请求也能接住。

---

## 五、接入层：CLI Chat + Web Chat

### 5.1 `alb chat` 终端 REPL（M2）

```
$ alb chat --backend ollama --model qwen2.5:3b
alb chat (ollama/qwen2.5:3b) · session=20260416-a3f8c2d1
> 帮我抓 30 秒 logcat，只要 error
→ calling alb_logcat(duration=30, filter="*:E")
  ...
← 已抓到 12 条 error，日志保存在 workspace/devices/.../logcat-....txt
> 把这 apk 装到设备上：./build/app.apk
→ calling alb_app_install(apk="./build/app.apk")
  ...
← 安装成功，package=com.example.app
> /quit
```

特性：
- `/quit`, `/clear`, `/session <id>` 内置命令
- ANSI 高亮 tool call 区块
- 和现有 CLI（`alb shell` 等）同一个 `alb` 入口，只是 subcommand

### 5.2 Web `/chat` 端点（M2 非流式 / M3 流式）

- `POST /chat` —— 单次请求，body `{session_id, message}`，返回最终文本
- `WS /chat/ws` —— 流式，逐 token 推送 + tool_call 事件（M3）

### 5.3 Web UI（M3）

一个最小的 HTML 页面，和现有 `web/` 占位目录合并。不追求漂亮，只求"能在浏览器里聊天调用工具"。

---

## 六、和现有模块的关系

### 6.1 和 MCP server 的关系

**不重复造工具注册表**。`alb chat` 里的 `tool_executor` 是对 MCP server 内部 tool registry 的薄封装：

```python
# 伪码
from alb.mcp.server import get_registered_tools, dispatch_tool

async def mcp_tool_executor(name: str, args: dict) -> dict:
    return await dispatch_tool(name, args)

loop = AgentLoop(
    backend=OllamaBackend(),
    tools=[ToolSpec(...) for t in get_registered_tools()],
    tool_executor=mcp_tool_executor,
)
```

MCP server 改了工具，`alb chat` 自动跟着变，不漂移。

### 6.2 和权限系统的关系

权限检查留在 **capability / transport 层**（M1 已有）。Agent loop 不加额外的权限层——它只是"换了个调用方"。小模型要做 `rm -rf /sdcard`，仍然被黑名单拦截。

### 6.3 和 workspace 的关系

- Session JSONL → `workspace/sessions/<id>/messages.jsonl`
- 工具产物（logcat / bugreport）→ 还是 `workspace/devices/<serial>/...`
- Session 目录和设备目录**平行**，一次聊天可能操作多台设备

---

## 七、实施路线

### M1 · 架构预留（本提交，已完成）

- [x] `src/alb/agent/` 骨架：`__init__.py` / `backend.py` / `loop.py` / `session.py`
- [x] `LLMBackend` ABC + `Message` / `ToolCall` / `ToolSpec` / `ChatResponse` / `BackendError`
- [x] `AgentLoop` 骨架（NotImplementedError，接口签名冻结）
- [x] `ChatSession` 骨架（`create` / `load` / `append` / `messages` 可用，落盘空）
- [x] `BackendSpec` + `BACKENDS` registry（全部 `status="planned"`）
- [x] `docs/agent.md` 本文档 + `docs/design-decisions.md` ADR-016
- [x] `docs/project-plan.md` M2 / M3 条目

### M2 · 可用最小版本

- [x] `OllamaBackend` —— HTTP 调 `/api/chat`，支持 tool_calls（Ollama 0.4+）
- [ ] `OpenAICompatBackend` —— 兼容 vLLM / LM Studio / llamafile 的 /v1/chat/completions
- [x] `AgentLoop.run()` 完整实现（循环 / tool dispatch / session flush）
- [x] `ChatSession` 落盘 JSONL + `load()` 解析
- [ ] `alb chat` CLI subcommand（`src/alb/cli/chat_cli.py`）
- [ ] FastAPI `POST /chat` 路由（非流式）
- [x] 单元测试：mock backend + mock executor 跑完整循环（Ollama HTTP mock + FakeBackend 三轮 tool_call）
- [ ] 文档：`docs/methods/08-local-llm.md` 使用指南（装 ollama / 选模型 / 限制）

### M3 · 完整体验

- [ ] `LlamaCppBackend` —— `llama-cpp-python` 嵌入
- [ ] `AnthropicBackend` —— `anthropic` SDK
- [ ] 流式：`backend.stream()` 实现 + `WS /chat/ws`
- [ ] Web 简易 chat 页（和 Web UI M3 合并）
- [ ] Session 自动摘要（`summary.md`，借鉴 MemGPT）
- [ ] Known-issues 记忆库（从 messages.jsonl 沉淀，借鉴 Evo-Memory）

---

## 八、风险与约束

| 风险 | 缓解 |
|------|------|
| 小模型 tool_calls 参数格式错（缺 required 字段等） | `AgentLoop` 包一层 JSON-schema 校验；失败直接把错误喂回模型，让它自改 |
| 多轮 tool_call 陷死循环（模型反复调同一个工具） | `max_turns=8` 硬截断 + 相同 tool_call 去重 |
| 本地 CPU 推理慢，用户等不及 | 默认非流式超时 120s；提供 `alb chat --stream` 流式提前出字 |
| 模型没经过 tool-calling fine-tune，输出不了结构化 | `backend.supports_tool_calls=False` 时跳过 tool injection，降级成纯对话 |
| 日志被塞进 LLM context 爆窗口 | 严格遵循 ADR-007：工具只返回摘要 + 路径，不返回全文 |
| Backend 抽象随各家 API 演进走形 | `LLMBackend` ABC 保持极简三方法（chat / stream / health），复杂度留在子类 |
| 依赖膨胀（ollama / openai / anthropic / llama-cpp 全装会撑大） | 每个 backend 走 lazy import（参考现有 `SshTransport` / `SerialTransport` 模式），按需装 |

---

## 九、FAQ

**Q: 为什么不直接用 LangChain / LlamaIndex / smolagents？**
A: 我们需求是"shallow tool routing"，不是"build an agent framework"。依赖它们会把整棵依赖树拖进来，和我们"轻量单进程"的定位违背。50 行 AgentLoop 够用。

**Q: 为什么让 agent loop 调 MCP tool 而不是直接调 capabilities?**
A: 一层间接。好处：工具 schema / description / 参数校验的正确源在 MCP 注册表，改一次三层（MCP / Claude Code / alb chat）同步。

**Q: 小模型会乱调 reboot 怎么办？**
A: 权限系统（ADR-006）在 capability / transport 层强制，和调用方无关。`rm -rf /` / `reboot bootloader` 仍然被拦截，小模型失败后看到 `suggestion` 会自己重试。

**Q: CPU 推理这么慢，实用吗？**
A: 目标场景是"替代我手动敲 5 条 alb 命令"，不是"替代 Claude Code 做复杂分析"。8-15 tokens/s × 200 tokens 响应 ≈ 15-25 秒，对"抓个日志"够用。

**Q: 为什么不让 agent 自己分析日志？**
A: 3B 模型分析长日志准度太低，会误导用户。定位明确：**我们只做路由，分析交给人 / Claude / GPT**。日志文件路径返回给用户即可。

---

## 十、下一步

- 看本层骨架代码 → `src/alb/agent/`
- 看决策原因 → [`design-decisions.md`](./design-decisions.md) ADR-016
- 看 LLM 接入通盘 → [`llm-integration.md`](./llm-integration.md)
- 看实施排期 → [`project-plan.md`](./project-plan.md) M2 / M3

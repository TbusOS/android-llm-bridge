# 当前架构快照 · 2026-04-28

> 每次重大重构 / 新模块 ship 后由主对话更新。agents 评审时这是它们
> 拿到的"项目地图"。

## 顶层组成

```
android-llm-bridge/
├── src/alb/                          # Python 后端（核心）
│   ├── agent/                        # LLM agent 层（M1.5 + M2）
│   │   ├── backend.py                # LLMBackend ABC + Message + ToolCall
│   │   ├── backends/                 # OllamaBackend (+ OpenAICompatBackend M3)
│   │   ├── loop.py                   # AgentLoop（ReAct-lite + run_stream）
│   │   ├── session.py                # ChatSession（messages.jsonl + meta.json）
│   │   ├── playground.py             # raw chat（不走 agent loop）
│   │   └── token_sampler.py          # ★ F.1 计划：1Hz token 聚合器
│   ├── api/                          # FastAPI 路由
│   │   ├── server.py                 # create_app + include_router
│   │   ├── chat_route.py             # POST /chat + WS /chat/ws
│   │   ├── audit_route.py            # GET /audit + WS /audit/stream
│   │   ├── sessions_route.py         # GET /sessions
│   │   ├── devices_route.py          # GET /devices
│   │   ├── metrics_route.py          # WS /metrics/stream
│   │   ├── playground_route.py       # /playground/backends + /chat
│   │   ├── terminal_route.py         # WS /terminal/ws (PTY + HITL)
│   │   ├── meta_route.py             # /api/version + /api/ping
│   │   ├── ui_static.py              # mount /app
│   │   └── schema.py                 # API_VERSION + REST/WS endpoint list
│   ├── capabilities/                 # 业务能力层（M1）
│   │   ├── shell.py logging.py filesync.py diagnose.py power.py app.py metrics.py
│   ├── transport/                    # 传输抽象 + 4 实现
│   │   ├── base.py adb.py ssh.py serial.py hybrid.py
│   │   ├── interactive.py            # PTY 抽象
│   │   └── terminal_guard.py         # HITL 命令拦截
│   ├── infra/                        # 基础设施（不依赖业务）
│   │   ├── event_bus.py              # ★ EventBroadcaster（C.1 ship）
│   │   ├── workspace.py              # workspace_root + session_path
│   │   ├── errors.py                 # 错误码 catalog
│   │   ├── permissions.py            # 命令拦截规则
│   │   ├── prompt_builder.py         # 静态/动态 prompt 分界
│   │   ├── env_loader.py             # .env / .env.local
│   │   ├── config.py registry.py process.py
│   ├── mcp/                          # MCP server（21 tools）
│   │   ├── server.py executor.py transport_factory.py
│   │   └── tools/                    # shell / logging / info / diagnose / app / power / metrics / ui / devices
│   └── cli.py                        # alb CLI（typer）
├── web/                              # 前端（React 18.3 + Vite + TS strict）
│   └── src/
│       ├── lib/                      # ws.ts api.ts
│       ├── stores/app.ts             # Zustand 全局 state（lang / theme / device）
│       ├── features/                 # 按模块组织
│       │   ├── chat/                 # ChatPage + useChatStream
│       │   ├── dashboard/            # DashboardPage + 6 卡 + 5 hooks（real）
│       │   └── inspect/              # InspectPage + 5 sub tabs
│       ├── components/SubNav.tsx     # 模块内子导航（共享）
│       ├── styles/components.css     # class-based 样式（不引 Tailwind）
│       └── routes/                   # TanStack Router
├── docs/                             # GitHub Pages 站
│   ├── index.html                    # 项目首页（anthropic 风格）
│   ├── webui-preview-v2.html         # ★ React UI 视觉基线（mockup）
│   ├── on-device-*.html              # 设备端 Agent 介绍
│   ├── methods/                      # 技术方法论 markdown
│   └── app/                          # 编译后 React UI
├── tests/                            # pytest（601+ tests，全 green）
├── scripts/                          # check_sensitive_words.sh / check_offline_purity.sh / smoke_*
└── workspace/                        # 运行时数据（gitignore）
    └── sessions/<sid>/
        ├── messages.jsonl            # 单 session chat 落盘
        ├── meta.json                 # session_id / created / backend / model / device
        └── terminal.jsonl            # 终端 audit
    └── events.jsonl                  # ★ 跨 session 事件总线持久化（C.1 ship）
```

## 核心架构决策（速览，详见 decisions.md）

1. **Transport 抽象 + 4 实现**：adb / ssh / serial / hybrid，由 build_transport
   工厂选择
2. **MCP 21 tools 是 capability layer 的 tool 化包装**，不是独立实现
3. **Agent 层 ABC + 多 backend**：当前 Ollama，未来 OpenAI-compat / Llama.cpp
4. **Result envelope 全局**：`{ok, data?, error?}` 跨 CLI/MCP/HTTP 一致
5. **事件总线（C 档重构）**：in-process EventBroadcaster + events.jsonl 持久化 +
   WS /audit/stream 实时订阅。chat / terminal 是 producer，前端 timeline /
   LiveSession 是 consumer
6. **前端：React 18.3 + Vite + TS strict + TanStack Query + Zustand + WS**，
   不引 Tailwind（沿用 anthropic.css token + class-based css）
7. **mockup 优先**：所有 React 视觉改动必须先有 `docs/webui-preview-v*.html`
   静态稿走三道闸
8. **审计双写**：terminal.jsonl per-session 落盘 + events.jsonl 全局总线（互
   不依赖）

## 数据流图（C 档之后）

```
ChatRequest ──┐
              ↓
       chat_route._build_agent
              ↓
       AgentLoop.run_stream
              ↓
       OllamaBackend.stream
              ↓ (token / tool_call_start / tool_call_end / done)
       chat_route stream loop
              ├──→ ws.send_json (to chat client)
              └──→ EventBroadcaster.publish (filtered: skip token)
                          ↓
                  +────────┴────────+
                  ↓                 ↓
          events.jsonl        all subscribers Queue
            (持久化)          ↓
                       audit_stream WS / GET /audit
                              ↓
                  Web UI ActivityTimeline / LiveSessionCard
```

## 关键不变量

- `event_bus` schema 是合约，**不再变**：`{ts, session_id, source, kind, summary, data?}`
- `API_VERSION` 字符串："1"。schema 改动若 break 客户端 → 必须 bump
- `workspace/events.jsonl` append-only，绝不就地改写已有行
- `docs/webui-preview-v*.html` 改版 → 必须走三道闸再合入
- React class 名要照搬 mockup（feedback_react_ui_design_baseline 规则）

## 测试地图

- `tests/api/` — FastAPI 端点（含 WS）
- `tests/agent/` — AgentLoop / Backend / Session
- `tests/transport/` — 4 transport
- `tests/capabilities/` — 6 业务能力
- `tests/mcp/` — MCP tools + executor
- `tests/cli/` — typer CLI
- `tests/infra/` — event_bus / process / errors / config

## 当前里程碑状态

- M1: ✓ 全完
- M2: agent 层 ✓ ship；agent step 4+ 排期中（OpenAICompatBackend / Web Tier 1
  收尾）
- C 档（audit 重构）：✓ ship（5 commits, HEAD a03cbab）
- B 档（后端 GET 端点）：✓ ship（3 commits）
- D 档（Dashboard 真数据）：✓ ship（4 commits）
- F 档（tps_sample + KPI 全真）：📋 排期中

详见 `~/.claude/projects/<project>/memory/project_status.md` 或本目录
`decisions.md`。

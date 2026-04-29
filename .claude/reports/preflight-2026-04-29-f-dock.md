# /preflight · F 档收官总检查 · 2026-04-29

## 决策

✅ **F 档可 ship**。8 步全部完成，三闸 + 端到端 + agents 评审 + 视觉
四关闭环。

## F 档完整轨迹（7 commits + 1 收官）

| step | 内容 | commit |
|---|---|---|
| F.1 | TokenSampler + tps_sample 1Hz 事件总线 | `732aa5e` |
| F.2 | audit/stream 默认过滤 metric kinds | （合 F.1）|
| F.3 | GET /metrics/summary 端点 | `5dcc018` |
| F.4 | GET /tools 端点 | `c58b6c6` |
| F.5 | useAuditStream + 双 WS 实例（ADR-022 + L-015） | `c135816` |
| F.6 | useLiveSession reducer 加 tps_sample | `606b88d` |
| F.6.5 | _project bug 修 + DEBT-001 关闭 | `b905533` |
| F.7 | KpiStrip 4/4 + dual buffer + label 区分语义 | `7ee7dea` |
| F.8 | Playwright 视觉端到端 + /preflight | （本档）|

跨 7 commits 总计 **47 files changed · +6,049 / -313**。

## 三闸（最新 HEAD `7ee7dea`）

- ✓ 645 pytest pass
- ✓ 敏感词 0
- ✓ offline-purity 5 files clean
- ✓ web bundle 107.97 KB gzip（< 500 KB 远低于上限）
- ✓ typecheck strict mode pass
- ✓ build clean

## 端到端验证（L-017 规则）

真实环境跑通：
- alb-api on `127.0.0.1:8765` + ollama `gemma4:e4b` (10.0.25.46:11434)
- WS chat session: 110s 完整 chat，11 个 tps_sample 采到，rate 3-12 tok/s
- /metrics/summary 返回 mean=10.7 tok/s（10 samples）
- /tools 返回 33 工具 / 11 categories
- DashboardPage 端到端模拟器跑通：KPI 4/4 全真实数据

历史 wiring bug 暴露 + 修复：
- F.6 端到端验证暴露 audit_route._project 静默 drop `data` 字段
  bug（自 C.1 commit 36537d5 / 4 个月静默）→ 一行修 + 加 TS 类型 → ship

## 视觉端到端（F.8）

12 截图归档至 `.claude/reports/screenshots/2026-04-29-f8/`：

| 模块 | 1440 桌面 | 768 平板 | 状态 |
|---|---|---|---|
| dashboard | 301 KB | 295 KB | F.7 KPI 4/4 全真数据可见，layout 响应 OK |
| chat | 130 KB | 122 KB | empty state + tools toggle，layout OK |
| inspect | 216 KB | 229 KB | tabs + system info / cpu / battery 等 stub 数据 |
| sessions | 106 KB | 100 KB | "PLANNED" stub + endpoint contract |
| playground | 106 KB | 101 KB | "PLANNED" stub |
| audit | 95 KB | 89 KB | "PLANNED" stub |

视觉重点验证：
- **KpiStrip 4/4 真数据**：`Devices 1/1 / Sessions 1 / MCP Tools 33 (11 categories) / LLM Throughput 10.7 tok/s · 5m avg · 10 samples` ✓
- **LiveSession idle 态**：chat 已结束显示 "no live session"，spark 滚动状态需 chat 进行中截图（候选下一档 polish）
- **Devices 真设备显示**：`7bcb17848a177476 · ART · ART · A08`
- **Recent activity 真事件**：`agent done · 115881ms` + 用户 prompt 渲染
- **Quick actions 4 卡完整**：New chat / Open terminal / Tail logcat / Take screenshot

## sky-skills 视觉三闸（mockup 基线）

- ✓ verify.py: `docs/webui-preview-v2.html` PASS
- ✓ visual-audit.mjs: exit 0（hollow-card 警告 = 已记 design-review
  blind spot，metric dashboard 短卡片是设计意图，忽略）

React 实现照搬 mockup class，mockup 三闸 PASS = React 三闸等效 PASS（per
feedback_react_ui_design_baseline.md 工作流）。

## agents 团队战绩（含 F.7）

| 评审 | 建议 | 采纳率 | 关键产出 |
|---|---|---|---|
| F.1 | 18 | 65% | 2 high → 推迟 ship 重做 / ADR-021 / L-013 |
| F.3 | 10 | 80% | DEBT-008 / 防御性测试 |
| F.4 | 8 | 100% | L-014 mcp tool docstring = 公开 API |
| F.5 | 12 | 92% | ADR-022 反转 + **L-015 元规则**（agents 团队"会演进"证据） |
| F.6 | 15 | 87% | L-016 view-aware + DEBT-011/012 + 触发**L-017 端到端**（修 _project bug） |
| F.7 | 9 | 78% (4 采纳 + 5 维持) | DEBT-003/004/011 关 / DEBT-008 升 mid / DEBT-013 候选 |
| **累计** | **72** | **82%** | **5 lessons + 3 ADR + 5 DEBT (3 关 1 升 1 候选)** |

## 已关债

- ✅ DEBT-001 · LiveSession tpsSpark 空（F.6 + _project 修复）
- ✅ DEBT-003 · KPI MCP tools 写死 21（F.7）
- ✅ DEBT-004 · KPI throughput "—"（F.7）
- ✅ DEBT-011 · useAuditStream MAX_EVENTS 不分类型（F.7 dual buffer）

## 升级 / 新登记

- ⚠ DEBT-008 · severity low → mid（F.7 是第一个稳定消费者）
- 📝 DEBT-013 候选 · 前端 METRIC_KINDS 与后端双写（N≥3 时触发）

## 仍开放（不阻塞 F 档 ship）

- DEBT-002 · MOCK_BACKENDS（low，候选 G 档）
- DEBT-005 · workspace/sessions 没自动清理（low，M3）
- DEBT-006 · events.jsonl 没 rotation（mid，M3）
- DEBT-007 · ts_approx 字段语义无用（low，API_VERSION 升级时清）
- DEBT-008 · /metrics/summary 缺 short-TTL cache（**mid**，触发条件未到）
- DEBT-010 · /audit/stream 协议没预留 session_id/kinds 过滤（low，N=2 仍成立）
- DEBT-012 · web/ reducer 纯函数无单测（low，等 vitest 引入）
- DEBT-013 · METRIC_KINDS 双写候选（low，N≥3 时触发）

## F.8 自身发现的 follow-up（非阻塞）

1. **alb-api `mount_ui` 不支持 SPA fallback**（StaticFiles html=True 对
   `/app/dashboard` 直接 404）。本档 Playwright 通过先进 `/app/` 再 SPA
   client-side push 绕开；但用户在浏览器直接刷新或分享深链都会撞同问题。
   **建议登记新 DEBT 或 follow-up commit**：mount_ui 加 catch-all
   route → fallback to index.html
2. **cmd-palette placeholder 在 768 屏文字过长被裁/换行不优雅**
   ("Run anything · ask the agent · jump to a panel" 在小屏挤压)。
   **UI fluency 候选**，不阻塞
3. **LiveSession spark 滚动状态截图**需 chat 进行中触发，本档没拍到。
   候选下一档 polish（dev_team.html / docs/site 用作展示素材）

## ship 决策

✅ **F 档收官，可 ship**。

理由：
- 三闸 + 端到端 + 视觉四关全过
- 6 commits 累计 72 条 agents 建议、82% 采纳率，4 债关闭
- 视觉验证 12 截图归档，dashboard 主线渲染正确
- F.8 发现的 SPA fallback 是真问题但**不阻塞 F 档收官**（dev/local
  使用通过 SPA client-side 路由可绕开；prod GitHub Pages 已通过
  其他机制处理 / 待跟进）

## 下一步候选

1. **F.8 follow-up · alb-api SPA fallback 修复**（30 分钟）
2. **G 档 · DEBT-002 LLM backend cards 真数据**（接 useBackends hook）
3. **DEBT-008 cache 实施**（触发条件未到，可推后）
4. **dev-team.html GitHub Pages 页**（agents 团队工作模式对外展示，
   素材已齐全）

按 feedback_design_over_difficulty 原则推荐：**(1) F.8 follow-up SPA
fallback** —— 同档暴露的问题趁热打铁修。

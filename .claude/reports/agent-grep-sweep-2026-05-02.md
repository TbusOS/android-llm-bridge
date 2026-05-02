# L-030 Retroactive grep 全仓扫报告 — 2026-05-02

## 背景

L-030 lesson 写完当晚（commit `8d4eee6` 加 code-reviewer grep）立刻自审：
新规则真能发现现存 retroactive HIGH 吗？规则有 false positive 吗？

主对话直接跑 grep（不走 cron / 不走 cloud schedule · 立刻验证）。

## 扫法

```bash
grep -rnE 'max\([^)]*min\(|min\([^)]*max\(' src/ web/src/
grep -rnE 'np\.clip\(|\.clamp\(' src/ web/src/
```

## 7 命中 + 真实评估

| # | 文件:行 | 上游 | 真实评估 |
|---|---|---|---|
| 1 | `src/alb/agent/playground.py:58,61` `_clip` / `_clip_int` | Pydantic `temperature: float\|None` from HTTP body（v2 默认 `allow_inf_nan=True`） | **SAFE** Python `max(LO, min(HI, x))` 顺序在 NaN 入参时实测返回 HI（不传染）；`int()` 会对 NaN 抛 ValueError |
| 2 | `src/alb/transport/interactive.py:127` | `int(rows)` 先转 → `int(nan)` 抛 ValueError | SAFE（int() 隐式 NaN 守护） |
| 3 | `src/alb/transport/interactive.py:128` | 同上 | SAFE |
| 4 | `src/alb/capabilities/metrics.py:216` | dbf5dca 已加 NaN check（防御性 · 即使没加 Python 顺序也安全） | SAFE |
| 5 | `src/alb/api/audit_route.py:178` | 包在 `except (TypeError, ValueError)` 里 | SAFE |
| 6 | `web/src/features/inspect/UartCaptureView.tsx:84` | `Number(input) \|\| DEFAULT_DURATION`（NaN falsy → 兜底） | SAFE 上游兜底 |
| 7 | `web/src/features/dashboard/useLiveSession.ts:180` | `samples[]` 从 WS 帧 reducer 来 · 后端理论不发 NaN，前端无 `Number.isFinite` filter · JS Math 真传染 | **LOW**（DEBT-030 跟踪） |

## 扫法的 self-correction

第一轮主对话报"playground HIGH retroactive"，**事实错误**。

实测 Python 行为：

```python
>>> max(0.0, min(2.0, float('nan')))
2.0  # 不是 nan!
>>> min(60.0, float('nan'))
60.0  # Python 返回 a if a < b else b，NaN 比较 False → 返回第一个
>>> min(float('nan'), 60.0)
nan  # 但反向顺序就传染！
```

教训（已写回 L-030 v2）：
- **Python `max(LO, min(HI, x))` 标准顺序**：实际安全（clamps 到 HI）
- **Python `min(x, HI)` 反向顺序**：传染 NaN
- **JS `Math.max/min`** 任何顺序：永远传染（设计如此）
- **numpy.clip / pandas / torch**：永远传染
- **唯一可移植安全写法**：explicit NaN check（`x != x` / `Number.isFinite` /
  `np.isnan`）

## 规则评估

- **真 finding rate**：1 LOW（DEBT-030 useLiveSession） / 7 命中 = 14%
- **false positive rate**（按 v1 规则误判 HIGH）：1/7 = 14%
- **修订后规则**（v2 · 按语言 + 顺序分级）：
  - HIGH 仅限 JS Math + numpy.clip + pandas/torch clamp
  - MID 仅限 Python 反向顺序 `min(x, HI)`
  - LOW 是 Python 标准顺序 + 上游隐式守护
  - 预期 false positive 大幅下降

## 闭环

- L-030 v1 → v2 修订（lessons.md）
- code-reviewer.md grep checklist 重写（按语言 + 顺序分级）
- DEBT-030 立项（useLiveSession isFinite filter）
- 本报告留存验证轨迹（"规则刚立就自审"机制有效）

## 关键收获

**"reviewer 越用越聪明" 不只是加规则 · 也包括校准规则严重度**。第一次写
规则容易过严或过松，retroactive 全仓扫是免费的校准机会。L-030 v1 写完 30
分钟内就靠这个机制找到自己的事实错误，避免后续被规则误伤更多 PR。

**全局教训记录**（已写到 lessons.md L-030 反面教材）：写 lesson 前必须实测，
不能用"应该如此"的推演当事实。

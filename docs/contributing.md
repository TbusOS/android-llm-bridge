---
title: 贡献指南
type: contribution-guide
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [contributing, dev-setup, pr-checklist]
---

# 贡献指南

欢迎贡献！本文档讲：**开发环境 → 代码规范 → 扩展指南 → 测试 → PR checklist → Commit 规范**。

---

## 一、开发环境

### 必备

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- git

### 初始化

```bash
git clone https://github.com/<your>/android-llm-bridge
cd android-llm-bridge
uv sync --dev                # 装开发依赖
uv run alb --help            # 验证
```

### 推荐工具

- **IDE**：VS Code + Python extension / PyCharm
- **类型检查**：`uv run mypy src/alb`
- **Lint / format**：`uv run ruff check` + `uv run ruff format`
- **测试**：`uv run pytest`

### pre-commit（推荐）

```bash
uv run pre-commit install
# 每次 commit 自动跑：ruff / mypy / 敏感信息扫描
```

---

## 二、代码规范

### 风格
- 遵循 [PEP 8](https://peps.python.org/pep-0008/)（ruff 会管）
- 行长 100 字符
- 导入按 isort 顺序（ruff 管）

### 命名
| 类型 | 规则 | 例子 |
|-----|-----|-----|
| 模块 | 小写下划线 | `filesync.py` |
| 类 | PascalCase | `AdbTransport` |
| 函数 | 小写下划线 | `collect_logcat` |
| 常量 | 大写下划线 | `DEFAULT_TIMEOUT` |
| 类型别名 | PascalCase | `DeviceSerial` |
| MCP tool | 小写下划线，`alb_` 前缀 | `alb_logcat` |
| CLI 命令 | 小写连字符 | `alb logcat`, `alb log-search` |

### 类型注解
- **强制**：public 函数必须有完整 type hints
- **推荐**：私有函数也尽量写
- 用 3.11+ 语法：`list[str]` / `dict[str, int]` / `str | None`
- 新代码目标 `mypy --strict` 通过

### 文档字符串
见 [`tool-writing-guide.md`](./tool-writing-guide.md)，核心：
- `When to use` / `LLM notes` / `Args` / `Returns`
- 给出 code-ish 例子

### 异常
- 业务函数返回 `Result[T]`，**不裸抛**
- 低层驱动（`drivers/*`）可以抛 Python 异常，由上层包装成 Result

---

## 三、扩展指南

### 扩展一：加一个新 **Transport**

```
1. src/alb/transport/<name>.py    → 继承 Transport ABC
2. src/alb/infra/registry.py      → 追加 TransportSpec
3. scripts/setup-method-<name>.sh → 引导配置
4. docs/methods/XX-<name>.md      → 方案文档
5. tests/transport/test_<name>.py → 单测
6. PR
```

模板：
```python
# src/alb/transport/newproto.py
from alb.transport.base import Transport, ShellResult

class NewProtoTransport(Transport):
    name = "newproto"
    supports_boot_log = False
    supports_recovery = False

    def __init__(self, ...):
        super().__init__()
        # init connection pool / state

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        # wrap underlying command
        ...

    async def stream_read(self, source: str, **kwargs):
        async for chunk in ...:
            yield chunk

    async def push(self, local, remote): ...
    async def pull(self, remote, local): ...
    async def reboot(self, mode="normal"): ...
    async def health(self): ...
```

### 扩展二：加一个新 **Capability**

```
1. src/alb/capabilities/<name>.py   → 业务函数
2. src/alb/cli/<name>_cli.py        → typer subapp
3. src/alb/mcp/tools/<name>.py      → @mcp.tool() 装饰
4. src/alb/api/routers/<name>.py    → FastAPI router
5. src/alb/infra/registry.py        → CapabilitySpec
6. docs/capabilities/<name>.md      → 能力文档
7. tests/capabilities/test_<name>.py
8. 更新 errors.md（如果加了新错误码）
9. PR
```

遵循 [`tool-writing-guide.md`](./tool-writing-guide.md)。

### 扩展三：加一个新 **LLM 接入**（如 Slack Bot）

```
1. src/alb/bots/slack.py    → 复用 capabilities/ 业务层
2. docs/integrations/slack.md
3. llm/mcp-config-examples/ 下加配置示例（如适用）
4. PR
```

---

## 四、测试

### 分类

| 类别 | 位置 | 运行 |
|-----|-----|-----|
| 单元测试 | `tests/**/test_*.py` | `uv run pytest` |
| 集成测试（需设备） | `tests-integration/` | `uv run pytest tests-integration --device=<serial>` |
| MCP 测试 | `tests/mcp/` | 跑 `mcp` SDK 的 test client |
| CLI 测试 | `tests/cli/` | `typer.testing.CliRunner` |

### 最小要求（PR 必须含）

每个新 capability 函数：
- ✅ happy path 测试（mock transport）
- ✅ permission denied 测试
- ✅ timeout 测试
- ✅ error 路径测试（至少一种错误码）

```python
# tests/capabilities/test_shell.py
import pytest
from unittest.mock import AsyncMock
from alb.capabilities.shell import execute

@pytest.fixture
def mock_transport():
    t = AsyncMock()
    t.check_permissions.return_value = PermissionResult("allow")
    return t

@pytest.mark.asyncio
async def test_execute_happy(mock_transport):
    mock_transport.shell.return_value = ShellResult(
        ok=True, exit_code=0, stdout="hello\n", stderr="", duration_ms=10, ...
    )
    r = await execute(mock_transport, "echo hello")
    assert r.ok
    assert r.data.stdout == "hello\n"

@pytest.mark.asyncio
async def test_execute_permission_denied(mock_transport):
    mock_transport.check_permissions.return_value = PermissionResult(
        "deny", reason="match pattern rm -rf /"
    )
    r = await execute(mock_transport, "rm -rf /")
    assert not r.ok
    assert r.error.code == "PERMISSION_DENIED"
```

### 覆盖率

- M1 目标：≥ 70%
- M2+ 目标：≥ 80%
- CI 里阈值卡 65% 作为 PR 合并门槛

---

## 五、PR Checklist

提 PR 前自检：

### 代码

- [ ] `uv run ruff check` 零错误
- [ ] `uv run ruff format --check` 无改动
- [ ] `uv run mypy src/alb`（关键模块）无错
- [ ] 单元测试全过：`uv run pytest`
- [ ] 覆盖率无下降：`uv run pytest --cov=alb`

### 文档

- [ ] 新 capability 有 `docs/capabilities/<name>.md`
- [ ] 新 transport 有 `docs/methods/XX-<name>.md`
- [ ] 新错误码在 `docs/errors.md` 登记
- [ ] 新配置项在对应文档说明
- [ ] CLAUDE.md 对 LLM 有额外提示的话同步更新

### LLM 友好性

- [ ] 所有 public 函数 docstring 含 "When to use" / "Args" / "Returns"
- [ ] MCP tool 的 description 包含 "LLM notes" 段
- [ ] 返回 `Result[T]` 结构
- [ ] 错误含 `code` + `suggestion`

### 安全

- [ ] 新命令在合适位置过 `check_permissions`
- [ ] 没有硬编码密钥 / token
- [ ] 路径操作防穿越（workspace 内限制）

### Commit

- [ ] Commit message 规范（见下节）
- [ ] **无 Claude / AI 署名**（全局规则）
- [ ] 单个 PR 聚焦单一主题（分开的功能分开 PR）

---

## 六、Commit 规范

### 格式

```
<type>: <subject>

<body (optional)>

<footer (optional)>
```

### type（英文小写）

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | bug 修复 |
| `docs` | 文档 |
| `test` | 测试 |
| `refactor` | 重构（不改行为） |
| `perf` | 性能 |
| `chore` | 杂务（依赖 / 配置 / CI） |
| `build` | 构建 / 打包 |
| `ci` | CI 配置 |

### subject

- 英文 / 中文均可
- 不超过 72 字符
- 命令式动词（"Add" 不是 "Added"）
- 首字母小写
- 结尾不加句号

### 例子

```
feat: add UART transport with socat PTY bridge
fix: retry adb connect when first attempt returns offline
docs: expand UART method guide with u-boot workflow
test: add permission-denied case for filesync push
refactor: split transport/base.py into abc and shell_result
perf: use asyncio.gather in multi-device list
chore: bump mcp-python SDK to 0.5.0
```

### ⚠️ 禁止

- **禁止任何 Claude / AI 相关署名**（`Co-Authored-By: Claude ...`, `Generated with Claude`, 等）—— 全局规则
- 禁止一个 commit 同时改多个不相关主题
- 禁止长 body 里带无关讨论

### Co-author（如果真有多人参与）

仅真实人类协作者：
```
Co-authored-by: Alice <alice@example.com>
```

---

## 七、Issue 规范

### 新功能 / 增强

标签：`enhancement` + 相关能力标签（如 `capability:logging`）

模板：
```markdown
## 需求
[是什么]

## 动机
[为什么需要]

## 方案建议
[有想法的话写]

## 风险 / 影响
```

### Bug

标签：`bug` + 相关组件标签

模板：
```markdown
## 现象
## 复现步骤
## 期望
## 实际
## 环境
- alb 版本:
- Python:
- OS:
- 设备型号 / Android 版本:
- 使用的 transport (A/B/C/G):
```

---

## 八、Code Review 原则

审查者重点：

1. **架构一致性** —— 新代码是否破坏分层（L1 不能直接调 L4）
2. **LLM 友好性** —— tool docstring 够好吗？返回结构化吗？
3. **权限切点** —— 改状态的操作有 check_permissions 吗？
4. **错误处理** —— 没裸抛异常吧？code + suggestion 齐全吗？
5. **测试覆盖** —— 至少 4 case
6. **文档同步** —— capability 文档 / errors.md 更新了吗？
7. **无安全隐患** —— 无硬编码密钥 / 无路径穿越 / 无命令注入

---

## 九、发布节奏

- **patch 版本**（0.1.x）：bug 修复 + 小改进
- **minor 版本**（0.x.0）：新能力 / 新 transport 落地
- **major 版本**（x.0.0）：架构破坏性变更（尽量避免）

里程碑对应：
- M1 首发 → 0.1.0
- M2 完成 → 0.2.0
- M3 完成 → 0.3.0
- 稳定后 → 1.0.0

---

## 十、沟通渠道

- **GitHub Issues** —— bug / 需求 / 设计讨论
- **GitHub Discussions** —— 使用问题 / 闲聊
- **PR** —— 代码贡献

---

## 十一、行为准则

简单：**尊重 + 建设性**。

- 对事不对人
- 假设善意
- 不同意见以理服人，不攻击
- 新手问题耐心回答

---

## 十二、关联

- [架构设计](./architecture.md)
- [Tool 编写指南](./tool-writing-guide.md)
- [错误码参考](./errors.md)
- [权限设计](./permissions.md)
- [项目计划](./project-plan.md)

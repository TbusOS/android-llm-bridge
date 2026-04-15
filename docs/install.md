---
title: 安装 / 卸载指南
type: user-guide
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [install, uninstall, uv, python, isolation]
---

# 安装 / 卸载指南

> **核心承诺**：不需要 root、不碰系统 Python、在共享服务器上零影响其他用户。

---

## 一、隔离原理（先理解清楚）

系统上各处"谁放在哪"：

```
系统层（你不改）
  /usr/bin/python3              ← 系统 Python（比如 3.8），其他人依赖的
  /usr/lib/python3.*            ← 系统 site-packages
  /etc/*                        ← 系统配置

你的用户目录（你自己的私有空间，别人看不到）
  ~/.local/bin/uv               ← uv 本体
  ~/.local/share/uv/python/     ← uv 下载的 Python 3.11+（独立目录）
  ~/.local/share/uv/cache/      ← uv 缓存
  ~/.bashrc (可选)              ← 若 --with-uv-in-path 会加一行

项目目录（只属于这个项目）
  <repo>/.venv/                 ← 项目专用虚拟环境（uv 自动创建）
  <repo>/workspace/             ← 运行时产物（logs/anr/bugreports）
```

**为什么零影响别人**：
- `~/.local/` 是 **用户级**，别的用户 `ls ~/.local` 也看不到你的
- 不用 `sudo`，`/usr/`、`/etc/` 一根汗毛都不碰
- 系统 `python3` 还是 3.8，其他项目照样跑

**为什么不影响你自己的其他 Python 工作**：
- `uv` 只是多出来一个命令（在你的 PATH 里）
- `python3`、`pip` 仍指向系统版本
- 只有在项目里跑 `uv run ...` 才用 3.11
- `cd` 到别的目录，`python3 --version` 仍然是 3.8

---

## 二、一键部署

### 基本用法

```bash
cd android-llm-bridge
./scripts/install.sh
```

脚本会：

| 步骤 | 做什么 | 落地在哪 |
|-----|--------|---------|
| 0 | 检查不是 root；确认在正确的 repo | — |
| 1 | 装 `uv`（如果没装） | `~/.local/bin/uv` |
| 2 | 用 uv 下载 Python 3.11 | `~/.local/share/uv/python/cpython-3.11.*/` |
| 3 | `uv sync` 建 venv + 装所有依赖 | `<repo>/.venv/` |
| 4 | 跑 smoke test（`pytest tests/test_smoke.py`） | 临时 |
| 5 | 打印下一步提示 | — |

### 选项

```bash
./scripts/install.sh --with-uv-in-path
# 把 `export PATH="$HOME/.local/bin:$PATH"` 加到你的 ~/.bashrc 或 ~/.zshrc
# 只影响你自己，其他用户看不到
```

```bash
./scripts/install.sh --skip-smoke-test
# 不跑 smoke test（CI / 离线环境用）
```

```bash
./scripts/install.sh --python 3.12
# 换成 Python 3.12（默认 3.11）
```

```bash
./scripts/install.sh --help   # 完整帮助
```

### 装好后的第一步

```bash
# 确保 ~/.local/bin 在 PATH
export PATH="$HOME/.local/bin:$PATH"

cd android-llm-bridge
uv run alb describe            # 看看都有哪些 transport/capability
uv run alb status              # 健康检查
uv run alb version             # 0.0.1
uv run pytest -q               # 跑完整测试
```

### 接入 Claude Code（MCP）

写入 `~/.claude/mcp-settings.json`：

```json
{
  "mcpServers": {
    "alb": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/android-llm-bridge", "alb-mcp"]
    }
  }
}
```

重启 Claude Code，它会启动 `alb-mcp` 子进程，自动拿到全部 21 个 tool。

---

## 三、一键卸载

### 默认（保守）

```bash
./scripts/uninstall.sh
```

只删：
- `<repo>/.venv/`
- Python 缓存（`__pycache__`、`.mypy_cache`、`.ruff_cache`、`.pytest_cache`、`htmlcov`、`.coverage`）

**不删**：
- `workspace/` 下的日志 / ANR / bugreport（你的调试证据）
- `uv` 本体（可能别的项目还用）
- 下载的 Python 3.11（同上）
- `~/.bashrc` 里的 PATH 行（只提示，不改你 rc）

### 选项（破坏性，要显式加 flag）

```bash
./scripts/uninstall.sh --purge-workspace
# 清空 workspace/ 所有产物
```

```bash
./scripts/uninstall.sh --remove-uv
# 删 ~/.local/bin/uv 和 uvx
# ⚠ 只有在确定没有其他项目用 uv 时才这么做
```

```bash
./scripts/uninstall.sh --remove-uv-python
# 删 ~/.local/share/uv/ 整个目录（含所有 uv-managed Pythons 和 cache）
# ⚠ 彻底告别 uv 时才做
```

```bash
./scripts/uninstall.sh --force
# 跳过交互确认
```

### 终极清理

```bash
./scripts/uninstall.sh --force --purge-workspace --remove-uv --remove-uv-python
rm -rf android-llm-bridge
# 现在你的账户上完全没有 alb / uv / uv-managed Python 的痕迹
```

---

## 四、故障排查

### 问题 1 · `./scripts/install.sh: Permission denied`

```bash
chmod +x ./scripts/install.sh ./scripts/uninstall.sh
```

### 问题 2 · `curl: command not found` / `wget: command not found`

脚本需要其中一个下载 uv。共享服务器一般两个都装了。如果真没有：

```bash
# 手动下载 uv 到 ~/.local/bin/
mkdir -p ~/.local/bin
# 参考 https://github.com/astral-sh/uv/releases 下载对应平台二进制
# 然后：
chmod +x ~/.local/bin/uv
./scripts/install.sh   # 会检测到已存在
```

### 问题 3 · `uv python install 3.11` 失败

通常是网络问题（uv 要从 GitHub 下预编译 Python）。解决：

```bash
# 看看 uv 尝试连哪
uv python install 3.11 -v

# 公司网络有代理？
export HTTPS_PROXY=http://proxy.company.com:8080

# 离线环境？手动下载：
# https://github.com/indygreg/python-build-standalone/releases
# 解压到 ~/.local/share/uv/python/cpython-3.11.x-*/
```

### 问题 4 · `uv sync` 安装某依赖失败

```bash
# 看详细错误
uv sync -v

# 常见：网络 / 某 C 扩展需要系统库（比如 python-serial 极少情况下）
# 按报错安装对应 -dev 包（如果你有 sudo）或换版本
```

### 问题 5 · 装完 `uv run alb` 报 command not found

说明 `~/.local/bin` 不在 PATH。两个办法：

```bash
# 临时
export PATH="$HOME/.local/bin:$PATH"
uv run alb --help

# 永久
./scripts/install.sh --with-uv-in-path   # 自动加进 ~/.bashrc
# 或手动编辑 ~/.bashrc / ~/.zshrc
```

### 问题 6 · 我的 Python 3.11 被其他工具共享了吗？

**不会**。uv 管的 Python 在 `~/.local/share/uv/python/cpython-3.11.*/`，里面的 `bin/python3.11` 没被软链到 `/usr/bin/` 或 `~/.local/bin/`，系统 `which python3` 依旧是系统 3.8。

**只有** `uv run` 命令知道怎么定位这个 Python。

### 问题 7 · 别的用户能用我的 alb 吗？

不能，也不应该：
- `~/.local/` 只你有权限
- 别的用户要用自己跑一遍 `./scripts/install.sh`（互不影响）

### 问题 8 · 想确认没污染系统

```bash
# 这些应该仍然是系统值，没变：
which python3                          # /usr/bin/python3
python3 --version                      # Python 3.8.x
ls /usr/local/bin/uv 2>/dev/null       # 无输出 = 正常
sudo -n ls /etc/pip.conf 2>/dev/null   # 未创建 = 正常

# 这些是你的私有空间：
ls ~/.local/bin/uv                     # 存在
ls ~/.local/share/uv/python/           # 有 cpython-3.11.*
ls ./.venv/                            # 项目 venv
```

---

## 五、高级：多项目共享一套 uv

如果你还有别的 Python 项目也用 uv，`install.sh` 会自动检测到已装的 uv 并复用：

```
~/project-a/  ← 用 uv sync （alb 项目）
~/project-b/  ← 用 uv sync （另一个项目）
```

它们共享：
- `~/.local/bin/uv`（同一个二进制）
- `~/.local/share/uv/python/`（可能共享同一 Python 3.11 或装不同版本）
- `~/.local/share/uv/cache/`（pip 下载缓存）

各自独立：
- `~/project-a/.venv/`
- `~/project-b/.venv/`

---

## 六、对比其他安装方式

| 方式 | 优点 | 缺点 | 推荐场景 |
|-----|------|------|---------|
| **`./scripts/install.sh`** | 零污染 / 自动 / 可卸载 | 单机器 | 绝大多数场景 |
| 手动 `uv sync` | 灵活 | 要自己先装 uv + Python 3.11 | 已有 uv 用户 |
| `pip install -e .` (到系统) | 简单 | 污染系统 site-packages | **不推荐** |
| Docker image | 完全隔离 | 有 daemon / 镜像体积 | CI / 分发 |
| conda env | 隔离 | 启动慢 / 工程依赖重 | 已是 conda 用户 |

---

## 七、相关文件

- `scripts/install.sh` / `scripts/uninstall.sh` — 安装卸载脚本
- `pyproject.toml` — 项目声明和依赖
- `.python-version` — 指定 Python 3.11
- `uv.lock` — 锁文件（install 时自动生成/更新）
- [`docs/project-plan.md`](./project-plan.md) — 里程碑
- [`docs/contributing.md`](./contributing.md) — 开发者流程

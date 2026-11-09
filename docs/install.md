---
title: 安装 / 卸载指南
type: user-guide
created: 2026-04-15
updated: 2026-11-09
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
| 3 | `uv sync` 建 venv + 装所有依赖；失败时打印代理 / 镜像的办法 | `<repo>/.venv/` |
| 4 | 跑 smoke test（`pytest tests/test_smoke.py`） | 临时 |
| 5 | 查 pip 装不了的外部工具（`alb setup deps --install`）：缺 adb / fastboot 就下载；要 sudo 的列出命令。见下面「外部工具」 | `~/.local/share/alb/`，链接在 `~/.local/bin/` |
| 6 | 打印下一步提示；有必需的工具没装上，退出码 1 | — |

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
./scripts/install.sh --dev
# 再装跑测试要的：web/ 的 npm 包、Playwright 的 Chromium（要先有 Node.js，没有会告诉你怎么装）
```

```bash
./scripts/install.sh --yes
# 下载 Android Platform-Tools 前不再询问（无人值守安装用）
```

```bash
./scripts/install.sh --help   # 完整帮助
```

### 外部工具：pip / uv 装不了的那些

alb 和它的测试还要用到一些不是 Python 包的程序。pip / uv 安装 Python 包时不能顺带执行别的安装步骤，
所以由 install.sh 的最后一步 `alb setup deps --install` 来查，不用 root 能装的就装上。
用别的方式装了 alb 的人，自己跑一次 `alb setup deps`（加 `--install` 装，加 `--dev` 连测试工具一起查）。
`alb doctor` 的 binaries 那一层查的是同一份清单（`src/alb/infra/host_deps.py`）。

| 名字 | 档 | 干什么用 | 自动装吗 | 自己装 |
|---|---|---|---|---|
| `adb` | 必需 | 经 adb 连板子，alb 的主要通道 | 是：下载 Android SDK Platform-Tools 到 `~/.local/share/alb/platform-tools`，链接进 `~/.local/bin`。下载前问一次（要同意 Google 的许可），`--yes` 不问 | 见下面「adb 下载失败」 |
| `fastboot` | 可选 | 本机直连板子时烧录（台架烧录在 Windows 那头做） | 是，和 adb 一起装 | 同 adb |
| `ssh` | 可选 | ssh 通道（板子上跑 sshd），含 ssh 和 scp | 否，要 sudo | `sudo apt install openssh-client` |
| `rsync` | 可选 | `alb fs rsync` | 否，要 sudo | `sudo apt install rsync` |
| `picocom` | 可选 | alb 之外的交互串口终端，alb 本身不用它 | 否，要 sudo | `sudo apt install picocom` |
| `socat` | 可选 | 手动把串口桥到 pty / TCP，alb 本身不用它 | 否，要 sudo | `sudo apt install socat` |
| `ollama` | 可选 | 网页助手和 `alb chat` 的模型后端，可以装在另一台机器 | 否：官方安装要 root，而且常装在别的机器上 | <https://ollama.com/download>；在另一台机器就 `export ALB_OLLAMA_URL=http://<host>:11434` |
| `node` | 测试 | 跑网页测试、重新打包网页，版本要 `^20.19.0 \|\| ^22.12.0 \|\| >=24.0.0` | 否：开发者常用 nvm 等工具自己管 Node 版本 | 用 nvm（不要 root）：`nvm install 22`；或把官方压缩包解压到 `~/.local` |
| `web-packages` | 测试 | `web/node_modules` 里的 vitest 和钉死版本的 `@playwright/test` | 是：`npm ci` | `cd web && npm ci` |
| `playwright-browsers` | 测试 | Playwright 的 Chromium，完整版和精简版（headless shell）各一份 | 是：`npx playwright install chromium` | 见下面「浏览器下载失败」 |
| `browser-libs` | 测试 | Chromium 启动要的系统库（只查 Linux） | 否，要 sudo | `cd web && sudo npx playwright install-deps chromium` |
| `python-dev-tools` | 测试 | pytest、ruff、mypy | 是：`uv sync` | `uv sync` |
| `mksh` | 测试·可缺 | 一条 pytest 要用 Android 的 shell，没有就跳过 | 是，只在 Debian / Ubuntu 上：`apt-get download` 加 `dpkg-deb -x`，不要 root | `sudo apt install mksh` |
| `playwright-cli` | 测试·可缺 | 让 AI 编程助手操作浏览器 | 否 | `npm install -g @playwright/cli`；`playwright-cli install --skills` 要在家目录跑，它会往当前目录写 `.claude/skills/` |

「必需」缺了，`alb setup deps` 退出码 1；加 `--dev` 时，「测试」档缺了也是 1；
「可选」和「测试·可缺」只列出来，不影响退出码。每一项缺了，命令都会打印上表最后一列那样的安装办法。

#### adb 下载失败怎么办

```bash
# 1. 自己下载 zip（用浏览器下也行），交给 alb 解压和链接
#    https://dl.google.com/android/repository/platform-tools-latest-linux.zip   （macOS 把 linux 换成 darwin）
alb setup deps --install --platform-tools-zip ~/Downloads/platform-tools-latest-linux.zip

# 2. 走代理，或者从镜像下载
export HTTPS_PROXY=http://<proxy>:<port>             # 然后再跑 alb setup deps --install
export ALB_PLATFORM_TOOLS_URL=<镜像上那个 zip 的地址>

# 3. 解压到任意位置，告诉 alb 用哪个 adb
export ALB_ADB_PATH=/path/to/platform-tools/adb

# 4. 发行版自带的包（版本常常偏旧）
sudo apt install adb fastboot
```

PATH 里已经有 adb 时，不会在它前面再放一个：版本不同的 adb 客户端会让它连上的 adb server 重启，
而经隧道连的 server 往往是别人正在用的那个。

#### 浏览器下载失败怎么办

- 走代理：`export HTTPS_PROXY=http://<proxy>:<port>`；从镜像下：`export PLAYWRIGHT_DOWNLOAD_HOST=<镜像地址>`
- 手动下：`cd web && npx playwright install chromium --dry-run` 会列出每个 zip 的下载地址和安装目录；
  下载后解压到对应目录，再在那个目录里建一个空文件 `INSTALLATION_COMPLETE`
- 报 `does not support chromium on <系统>`：这一版 Playwright 不再为这个系统出包。`web/` 钉的 1.59.1
  还支持 Ubuntu 20.04；以后升级碰到这个，用 `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64`
  下通用的 Linux 包（系统 glibc 够新才能运行）

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
./scripts/uninstall.sh --remove-host-tools
# 删 alb setup deps --install 放进家目录的东西：~/.local/share/alb/（Platform-Tools、mksh）
# 和 ~/.local/bin 里指向它的链接。web/node_modules 和 Playwright 的浏览器
# （~/.cache/ms-playwright，别的项目也可能在用）不动
```

```bash
./scripts/uninstall.sh --force
# 跳过交互确认
```

### 终极清理

```bash
./scripts/uninstall.sh --force --purge-workspace --remove-uv --remove-uv-python --remove-host-tools
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

# 网络不通：走代理，或换 PyPI 镜像，再跑一次 install.sh
export HTTPS_PROXY=http://<proxy>:<port>
export UV_DEFAULT_INDEX=<PyPI 镜像地址>     # 旧版 uv 用 UV_INDEX_URL
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

# CLAUDE.md — hard rules for any AI agent working on this repo

This file is loaded automatically by Claude Code and similar AI assistants.
It lists **non-negotiable** rules that protect the project's open-source
posture. Violations here are not style preferences — they create legal risk
or betray user trust.

---

## 1. Banned words / identifiers (ABSOLUTE)

The following strings MUST NEVER appear in:

- tracked source / documentation / config / tests
- commit messages
- pull-request titles or bodies
- issue titles / comments we author
- filenames / path components
- GitHub Pages HTML under `docs/`
- any asset that ships to a public surface

### Banned categories

This public file names the **kinds** of identifiers that are banned, never
the values themselves — writing a real value here would publish it.

| Category | Examples of the kind (not real values) |
|---|---|
| Employer / company names, brand tokens, their domains and package prefixes | `<company>`, `<company>.com`, `com.<company>` |
| Customer-specific SoC models and vendor SDK names | `<customer SoC>`, `<vendor> SDK` |
| Internal handles and real user names | `<handle>` |
| Absolute paths under a real home directory | `/home/<user>/...`, `/Users/<user>/...` |
| Machine names | `<laptop>.local`, `<build-server>` |
| Addresses of private networks you actually use | any real `10.x`, `172.16–31.x`, `192.168.x` host or subnet |
| Board serial numbers, instance IDs, tokens | `<serial>`, `<token>` |

The public GitHub handle `skyzhangbinghua` IS allowed in LICENSE / NOTICE /
pyproject / author-attribution contexts: it is the legitimate open-source
maintainer identity.

### Where the real values live

The real values are kept in a **private word list** outside this repository
(one extended regex per line). `scripts/check_sensitive_words.sh` reads it from
`$DHANDS_SENSITIVE_WORDS_FILE`, default
`~/.config/devicehands/sensitive-words.txt`. Maintainers keep that file, and
every other machine-specific setting (hub address, tokens, board serials), in
a private repository that is never made public. Without the list the guard
only runs its generic patterns, and says so.

### Why

This project is **open-source, brand-neutral**, and is being published under
the Anthropic-style public site at <https://tbusos.github.io/android-llm-bridge/>.
Leaking employer names, internal IPs, or customer-specific SoC identifiers
exposes the maintainer and the project to legal risk and breaks the neutrality
the README promises. The guard is `scripts/check_sensitive_words.sh`, run by
the `pre-commit` hooks in `.pre-commit-config.yaml` on staged files and on the
commit message.

## 2. How to write about hardware / networks generically

When you need to describe a real-world setup, pick the generic form:

| Instead of | Write |
|---|---|
| a customer's SoC model | `a high-speed UART target board` / `an ARM SoC` / `your board` |
| the SoC vendor plus model | `certain Rockchip / MediaTek / Qualcomm SoCs with high-speed UART` |
| a real host address | `<llm-host>`, `ollama-host.internal`, or a documentation address such as `192.0.2.10` |
| a path under your home directory | `~/xxx` or `<your-workspace>/xxx` |
| your employer or its domain | (never mention — remove the line) |
| vendor-specific baud tables | keep as speeds + broad families only |

Specific baud rates (`115200`, `1500000`) and protocol names (`adb`, `ssh`,
`uart`) are fine — they are public technical facts.

## 3. Before committing

1. Run `./scripts/check_sensitive_words.sh` (or let the pre-commit hook do it).
2. If the hook flags something: **stop, remove the term, re-stage**. Never
   `--no-verify`.
3. A new real identifier to block goes into the private word list, not into
   this repository. A new generic pattern (a kind of leak, no real value) goes
   into `scripts/check_sensitive_words.sh` with a self-test case — open a PR
   and discuss first.

## 4. Never un-sensitive these files

Some files have repeated AI drive-by edits that leak names. Extra caution:

- `docs/setup-remote-android-debug.md`
- `docs/methods/0*-*.md`
- `scripts/windows_serial_bridge.py`
- Any new doc that describes a real debugging session.

## 5. Historical clean-ups

The git history has been rewritten twice to remove leaked content
(see `docs/contributing/history-rewrites.md` if present). If you need to
propose another rewrite: **coordinate with the maintainer first**, do not
force-push unilaterally.

## 6. `clients/windows-agent/` is the single source of truth

Deployments of the Windows device agent exist as **copies** of this
directory (a private internal mirror repo, and an operator-facing staging
folder on a file share — real `agent.conf` values live only in those
copies, never here).

Rule for all edits: **change the files here first**, then sync outward
(mirror repo via its normal git sync; staging folder by copying files over,
preserving its local `agent.conf`). Never edit a downstream copy directly —
a fix applied only there is lost on the next sync, and the copies silently
diverge.

## 7. Scope of this file

These rules are enforced for all AI agents working on this repo (Claude Code,
Cursor, etc.). Personal / cross-project rules belong in the user's
`~/.claude/CLAUDE.md`, not here.

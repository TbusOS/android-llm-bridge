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

### Banned list

```
pax           PAX           paxsz         paxsz.com        com.pax
rk3576        RK3576        rk-sdk        RK SDK           rockchip-sdk
rk3566/3568   — name the family generically (see §2)
zhangbh       (short internal handle; word-bounded match — the public
              github handle `skyzhangbinghua` IS allowed in LICENSE /
              pyproject / author-attribution contexts, since this is
              a legitimate open-source maintainer identifier)
/home/zhangbh /home/<any-real-username>/<project>
10.0.25.*     10.0.25.46     10.0.25.71    172.16.*  (any RFC1918 internal IP that belongs to a private network)
```

### Why

This project is **open-source, brand-neutral**, and is being published under
the Anthropic-style public site at <https://tbusos.github.io/android-llm-bridge/>.
Leaking employer names, internal IPs, or customer-specific SoC identifiers
exposes the maintainer and the project to legal risk and breaks the neutrality
the README promises. The list is enforced by `scripts/check_sensitive_words.sh`
and by the `pre-commit` hook in `.pre-commit-config.yaml`.

## 2. How to write about hardware / networks generically

When you need to describe a real-world setup, pick the generic form:

| Instead of | Write |
|---|---|
| `RK3576` | `a high-speed UART target board` / `an ARM SoC` / `your board` |
| `Rockchip rk3576` | `certain Rockchip / MediaTek / Qualcomm SoCs with high-speed UART` |
| `10.0.25.46` | `<llm-host>` or `ollama-host.internal` |
| `/home/zhangbh/xxx` | `~/xxx` or `<your-workspace>/xxx` |
| `paxsz.com` | (never mention — remove the line) |
| vendor-specific baud tables | keep as speeds + broad families only |

Specific baud rates (`115200`, `1500000`) and protocol names (`adb`, `ssh`,
`uart`) are fine — they are public technical facts.

## 3. Before committing

1. Run `./scripts/check_sensitive_words.sh` (or let the pre-commit hook do it).
2. If the hook flags something: **stop, remove the term, re-stage**. Never
   `--no-verify`.
3. If you genuinely need to add a new word that shouldn't match, extend
   `scripts/check_sensitive_words.sh` carefully — open a PR and discuss first.

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

## 6. Scope of this file

These rules are enforced for all AI agents working on this repo (Claude Code,
Cursor, etc.). Personal / cross-project rules belong in the user's
`~/.claude/CLAUDE.md`, not here.

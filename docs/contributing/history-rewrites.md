---
title: Git history rewrites
type: maintenance-record
created: 2026-06-18
owner: sky
tags: [contributing, history, security, neutrality]
---

# Git history rewrites

This project is open-source and **brand-neutral** (see
[`../../CLAUDE.md`](../../CLAUDE.md) §1 and [`../contributing.md`](../contributing.md)).
Banned identifiers — employer/brand tokens, a specific customer SoC model,
vendor SDK names, internal private-network (RFC1918) IPs, absolute home-directory
paths, and an internal short developer handle — must never appear in tracked
content. The guard `scripts/check_sensitive_words.sh` enforces this on every
commit.

When such an identifier leaks and is later removed from the working tree, the
**old versions still live in git history** — anyone browsing an old commit on
GitHub can still see them. Deleting them from the current tree is not enough;
the history itself has to be rewritten.

This file is the standing record of every history rewrite, so future
contributors know it happened and can coordinate before doing another.

## Policy (CLAUDE.md §5)

- A history rewrite is a **destructive, force-pushed** operation. It is only
  done to remove leaked sensitive content, never for cosmetic reasons.
- **Never force-push unilaterally.** Coordinate with the maintainer first.
- Always keep a **full local backup mirror** of the pre-rewrite history outside
  the working tree before force-pushing, so a bad rewrite can be rolled back.
- A rewrite is only safe to force-push when there are **no forks and no other
  active contributors** whose history would be broken. Verify this first.
- Note the limit: a rewrite stops *future* exposure (browsing, search indexing,
  fresh clones). Content already cloned or cached elsewhere cannot be recalled.

## Record

### Rewrite #3 — 2026-06-18 (neutrality scrub of historical blobs)

- **Why**: the current tree was already clean, but earlier commits still carried
  banned identifiers in historical versions of several tracked files (most
  heavily a long-lived notes file). Tracked internally as finding `SEC-1`.
- **Tool**: `git filter-repo --replace-text` (run on a throwaway clone, not the
  working repo).
- **Substitution categories** (literal banned strings intentionally omitted from
  this public record; each maps a leaked category to a neutral form):
  - company / brand tokens → `vendor`
  - product package prefix → `com.example`
  - a specific customer SoC model identifier → `arm-soc`
  - vendor SDK names → `soc-sdk`
  - absolute home-directory paths → `~`
  - an internal short developer handle → `dev`
    (the public maintainer handle `skyzhangbinghua` is preserved — it is a
    legitimate attribution identifier)
  - internal private-network IPs → `<llm-host>` / `<internal-ip>`
- **Before → after**: `main` moved from `e78da8c` to `9375e84` (a fully divergent
  chain — every commit hash from the first scrubbed commit onward changed, which
  is why a force-push was required).
- **Restore commit**: the rewrite was followed by one commit restoring the
  rule-defining files (allowlisted by the guard) and the built site assets, so
  the final tree matches the pre-rewrite tree exactly.
- **Safety**: 0 forks, no other active contributors → force-push broke no one. A
  full backup mirror of the pre-rewrite history was retained outside the repo.
- **Verification**:
  - rewritten tree at `HEAD` byte-identical to the pre-rewrite tree;
  - guard `scripts/check_sensitive_words.sh --all` exits 0 on the tree;
  - full-history scan across all commits finds **0** real-source hits (the only
    regex match is a coincidental case-insensitive substring inside a
    third-party minified bundle, `docs/app/assets/xterm-*.js` — part of a
    camelCase option name in the library, not a brand token);
  - commit count intact;
  - **post-push** re-check: a fresh clone from `origin` re-ran the full-history
    scan → 0 real-source hits.

### Rewrites #1 and #2 — earlier (leaked-content removal)

Two earlier history rewrites removed leaked content before this record was
started. No detailed change log was kept for them at the time; they are noted
here for completeness per CLAUDE.md §5. From rewrite #3 onward every rewrite is
logged in this file.

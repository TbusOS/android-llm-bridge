# Contributing to android-llm-bridge

Thanks for considering a contribution. A few ground rules before you open a PR.

## 1. Neutrality is not optional

This project is open source and brand-neutral. Contributions must not leak:

- employer / company names
- customer-specific SoC identifiers or model numbers
- internal IP addresses / hostnames / path conventions
- maintainer's personal handles or home-directory paths

See [CLAUDE.md](./CLAUDE.md) § "Banned words" for the full list and the
`scripts/check_sensitive_words.sh` guard.

## 2. Before you commit

1. Install pre-commit: `pip install pre-commit && pre-commit install`
2. Stage your changes.
3. Run `./scripts/check_sensitive_words.sh` or let the hook do it.
4. If anything gets flagged: rephrase, re-stage. **Do not use `--no-verify`.**

## 3. Writing style

Describe hardware / network setups generically. Example:

- ❌ "Tested on RK3576 at 10.0.25.46 from /home/alice"
- ✅ "Tested on a 1500000-baud UART target from a remote dev host"

Specific baud rates, protocol names, and public SoC family names
(`ARMv8.2-A`, `Cortex-A76`, etc.) are fine — they are public facts.

## 4. Tests

- `uv sync` (first time)
- `uv run pytest -q --no-cov` — must pass before PR
- New features should add tests under `tests/`
- Transport / capability changes should add a golden-path fixture under
  `tests/golden/` so regressions are caught

## 5. Commit messages

- Use conventional prefixes: `feat(scope): ...`, `fix(scope): ...`, `docs(scope): ...`, `refactor(scope): ...`
- Body: **why**, not **what** (the diff shows the what)
- No emojis unless explicitly helpful
- No AI signature lines (no `Co-Authored-By: Claude ...`)

## 6. Pull requests

- One logical change per PR
- Reference the Discussion / Issue it came from
- If it touches public HTML under `docs/`, run the three design gates
  (see `README.md` or the PR template)

## 7. Questions

Open a [Discussion](https://github.com/TbusOS/android-llm-bridge/discussions)
with the relevant tag. Ideas welcome.

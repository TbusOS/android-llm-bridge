"""Regenerate the per-code listing in docs/errors.md from the error catalog.

The catalog is ``alb.infra.errors.ERROR_CODES``. docs/errors.md used to be
kept in step by hand and fell behind: it listed 23 of the 88 registered
codes, and 22 codes that nothing in the code base returned. The listing
is now a generated block between two marker comments; everything outside
the markers stays hand-written.

The block also lists codes that the code returns but never registered
(found by scanning ``src/alb``, see :func:`scan_returned_codes`). Such a
code reaches the caller with no catalog entry, so ``alb_describe_errors``
cannot explain it.

Run:
    uv run python scripts/gen_errors_doc.py           # rewrite the block in place
    uv run python scripts/gen_errors_doc.py --check   # exit 1 if docs/errors.md is stale

tests/test_docs_errors.py runs the same check, so a catalog change without a
regenerated doc fails the test suite.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import get_args

from alb.infra.errors import ERROR_CODES, ErrorSpec
from alb.infra.result import ErrorCategory

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "errors.md"
SRC = REPO / "src" / "alb"
REGISTRY = SRC / "infra" / "errors.py"

BEGIN = "<!-- BEGIN GENERATED: scripts/gen_errors_doc.py -->"
END = "<!-- END GENERATED -->"
REGEN_HINT = "uv run python scripts/gen_errors_doc.py"

# Chinese label and meaning per category, in the order of
# alb.infra.result.ErrorCategory. A category used in ERROR_CODES but missing
# here stops the generator: the doc would otherwise group codes under a
# heading nobody explained.
CATEGORY_TEXT: dict[str, tuple[str, str]] = {
    "transport": (
        "通道与台架",
        "到板子的通道(adb / ssh / 串口)出了问题,或经台架 agent 做的操作"
        "(fastboot 烧录、9008、agent 更新与启动)失败",
    ),
    "device": (
        "对端状态",
        "连接本身是通的,是对端(板子或台架主机)的状态或回答不对:USB 未授权、离线、"
        "adbd 不肯切 root、板子上的端口没人监听、台架主机上没有启动 agent 的任务",
    ),
    "permission": ("权限", "被权限策略拦下,或要 root 才能读"),
    "timeout": ("超时", "在限定时间内没等到预期的输出或状态"),
    "io": ("本地文件", "本地文件、工作目录、路径"),
    "input": ("参数与配置", "调用参数或 hub 配置不对,改了再试"),
    "system": ("主机环境与 LLM", "主机缺依赖,或 LLM 后端的上下文 / 输出被截断"),
    "capability": (
        "业务层",
        "`fail()` 不传 category 时的默认值;目录里目前没有这一类的码",
    ),
}

# ─── scan: codes the code base puts into an error ───────────────────

CODE_SHAPE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
# Parameter / keyword / dict-key / attribute names that carry an error code.
_CODE_NAMES = frozenset({"code", "error_code"})
# Calls whose result is never a code of ours even when it flows into one
# (`detail.get("code", "X")` is handled as a special case instead).
_NOT_CODE_CALLS = frozenset({"get", "str", "getattr"})


@dataclass
class _Module:
    path: Path
    tree: ast.Module
    # name -> every expression assigned to it anywhere in this file. Scopes
    # are merged on purpose: precise enough for constants and local
    # variables, and a code assigned to a variable named like this in the
    # same file is a code either way.
    assigned: dict[str, list[ast.expr]] = field(default_factory=dict)


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _positional(args: ast.arguments) -> list[str]:
    names = [a.arg for a in (*args.posonlyargs, *args.args)]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    return names


def _is_dataclass(node: ast.ClassDef) -> bool:
    return any("dataclass" in ast.unparse(d) for d in node.decorator_list)


def _record_assignment(mod: _Module, target: ast.expr, value: ast.expr) -> None:
    if isinstance(target, ast.Name):
        mod.assigned.setdefault(target.id, []).append(value)
    elif isinstance(target, ast.Tuple):
        if isinstance(value, ast.Tuple) and len(value.elts) == len(target.elts):
            for t, v in zip(target.elts, value.elts, strict=True):
                _record_assignment(mod, t, v)
        else:
            # `params, code, problem = check_params(...)`: each name may
            # come from the call; the call's returns are what get scanned.
            for t in target.elts:
                _record_assignment(mod, t, value)


class _Scanner:
    """Finds string literals that end up as an error code.

    A literal counts when it sits in a *value position* of an expression
    that is used as a code. Code expressions are:

    * the ``code`` / ``error_code`` argument of any function, class or
      dataclass in src/alb that has a parameter or field by that name
      (positional or keyword; this is how ``fail("X")``,
      ``_declined(name, "X", why)`` and ``ShellResult(error_code="X")`` are found);
    * any call's ``code=`` / ``error_code=`` keyword;
    * the value of a ``"code"`` / ``"error_code"`` key in a dict literal,
      and of ``x["code"] = ...`` / ``x.code = ...`` assignments.

    Value positions follow the ways a code is chosen: the expression itself,
    both branches of ``a if c else b``, each operand of ``x or "X"``, tuple
    elements, a variable (resolved through its assignments in the same
    file), a lookup ``TABLE[key]`` / ``TABLE.get(key, "X")`` in a dict
    literal of the same file (its values and the default), and a call to a
    function of src/alb (resolved through that function's ``return``
    statements, so ``error_code=_classify_stderr(...)`` yields what the
    classifier returns).

    Not followed: attribute reads such as ``e.code`` (the code was put there
    by a constructor call the scan already sees), f-strings, codes received
    from another process (the bench agent's codes arrive as data; their
    registration is checked by tests/remote/test_agent_codes_in_catalog.py),
    and tables imported from another file. A code that only travels such a
    way is missed.
    """

    def __init__(self, modules: list[_Module], rel_to: Path) -> None:
        self.modules = modules
        self.rel_to = rel_to
        self.takes_code: dict[str, set[int]] = {}
        self.functions: dict[str, list[tuple[_Module, ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
        self.found: dict[str, set[str]] = {}
        self._pending_funcs: set[str] = set()
        self._done_funcs: set[str] = set()

    # pass 1 ---------------------------------------------------------
    def index(self) -> None:
        for mod in self.modules:
            for node in ast.walk(mod.tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    self.functions.setdefault(node.name, []).append((mod, node))
                    self._note_params(node.name, _positional(node.args))
                elif isinstance(node, ast.ClassDef):
                    self._index_class(node)
                elif isinstance(node, ast.Assign):
                    for t in node.targets:
                        _record_assignment(mod, t, node.value)
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    _record_assignment(mod, node.target, node.value)

    def _note_params(self, name: str, params: list[str]) -> None:
        for i, p in enumerate(params):
            if p in _CODE_NAMES:
                self.takes_code.setdefault(name, set()).add(i)

    def _index_class(self, node: ast.ClassDef) -> None:
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                self._note_params(node.name, _positional(item.args))
        if _is_dataclass(node):
            fields = [
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            ]
            self._note_params(node.name, fields)

    # pass 2 ---------------------------------------------------------
    def scan(self) -> dict[str, set[str]]:
        self.index()
        for mod in self.modules:
            for expr in self._code_expressions(mod):
                self._take(mod, expr, set())
        while self._pending_funcs:
            name = self._pending_funcs.pop()
            self._done_funcs.add(name)
            for mod, fn in self.functions.get(name, []):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Return) and node.value is not None:
                        self._take(mod, node.value, set())
        return self.found

    def _code_expressions(self, mod: _Module) -> Iterator[ast.expr]:
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.Call):
                for i in sorted(self.takes_code.get(_callee(node) or "", ())):
                    if i < len(node.args) and not isinstance(node.args[i], ast.Starred):
                        yield node.args[i]
                for kw in node.keywords:
                    if kw.arg in _CODE_NAMES:
                        yield kw.value
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values, strict=True):
                    if isinstance(k, ast.Constant) and k.value in _CODE_NAMES:
                        yield v
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if (isinstance(t, ast.Attribute) and t.attr in _CODE_NAMES) or (
                        isinstance(t, ast.Subscript)
                        and isinstance(t.slice, ast.Constant)
                        and t.slice.value in _CODE_NAMES
                    ):
                        yield node.value

    def _take(self, mod: _Module, expr: ast.expr, seen: set[str]) -> None:
        if isinstance(expr, ast.Constant):
            if isinstance(expr.value, str) and CODE_SHAPE.fullmatch(expr.value):
                where = mod.path.relative_to(self.rel_to).as_posix()
                self.found.setdefault(expr.value, set()).add(where)
        elif isinstance(expr, ast.IfExp):
            self._take(mod, expr.body, seen)
            self._take(mod, expr.orelse, seen)
        elif isinstance(expr, ast.BoolOp):
            for v in expr.values:
                self._take(mod, v, seen)
        elif isinstance(expr, ast.Tuple):
            for v in expr.elts:
                self._take(mod, v, seen)
        elif isinstance(expr, ast.Await):
            self._take(mod, expr.value, seen)
        elif isinstance(expr, ast.Name):
            if expr.id not in seen:
                for value in mod.assigned.get(expr.id, []):
                    self._take(mod, value, seen | {expr.id})
        elif isinstance(expr, ast.Subscript):
            # `code = _STATE_REJECT[state]`: the codes are the table's values.
            self._take_values(mod, expr.value, seen)
        elif isinstance(expr, ast.Call):
            name = _callee(expr)
            if name == "get" and isinstance(expr.func, ast.Attribute):
                self._take_values(mod, expr.func.value, seen)
                if len(expr.args) >= 2:
                    self._take(mod, expr.args[1], seen)
            elif name and name not in _NOT_CODE_CALLS and name not in self._done_funcs:
                self._pending_funcs.add(name)

    def _take_values(self, mod: _Module, mapping: ast.expr, seen: set[str]) -> None:
        """The values of a dict literal that ``mapping`` is, or names."""
        if isinstance(mapping, ast.Dict):
            for v in mapping.values:
                self._take(mod, v, seen)
        elif isinstance(mapping, ast.Name) and mapping.id not in seen:
            for value in mod.assigned.get(mapping.id, []):
                self._take_values(mod, value, seen | {mapping.id})


def scan_returned_codes(src_root: Path = SRC, rel_to: Path = REPO) -> dict[str, set[str]]:
    """Every code that Python under ``src_root`` puts into an error, mapped
    to the files (relative to ``rel_to``) where it does. The registry file
    itself is skipped: defining a code is not returning it."""
    modules = [
        _Module(path=p, tree=ast.parse(p.read_text(encoding="utf-8"), filename=str(p)))
        for p in sorted(src_root.rglob("*.py"))
        if p.resolve() != REGISTRY.resolve()
    ]
    return _Scanner(modules, rel_to).scan()


# ─── render ─────────────────────────────────────────────────────────

_CODE_SPAN = re.compile(r"(`[^`]*`)")


def _cell(text: str) -> str:
    """A catalog sentence as a table cell, rendering to the same characters.

    Outside code spans `<` is escaped so `<host>` is not read as an HTML tag
    and dropped. `|` is escaped everywhere: it would end the cell."""
    parts = _CODE_SPAN.split(text)  # odd indexes are the code spans
    out = [p if i % 2 else p.replace("<", r"\<") for i, p in enumerate(parts)]
    return "".join(out).replace("|", r"\|")


def _prefixes(codes: list[str]) -> str:
    return " ".join(f"`{p}_*`" for p in sorted({c.split("_", 1)[0] for c in codes}))


def render_block(specs: Mapping[str, ErrorSpec], returned: Mapping[str, set[str]]) -> str:
    """The text between the markers (markers excluded)."""
    order = list(get_args(ErrorCategory))
    unknown = sorted({s.category for s in specs.values()} - set(order))
    if unknown:
        raise SystemExit(
            f"category {unknown} is used in ERROR_CODES but is not in "
            "alb.infra.result.ErrorCategory; fix the spec or the Literal"
        )
    missing_text = [c for c in order if c not in CATEGORY_TEXT]
    if missing_text:
        raise SystemExit(f"add a description for category {missing_text} to CATEGORY_TEXT")

    by_cat: dict[str, list[ErrorSpec]] = {c: [] for c in order}
    for spec in specs.values():
        by_cat[spec.category].append(spec)
    for lst in by_cat.values():
        lst.sort(key=lambda s: s.code)
    unregistered = sorted(c for c in returned if c not in specs)
    used = [c for c in order if by_cat[c]]

    lines: list[str] = [
        "<!-- 本段由脚本生成,不要手改。改了 src/alb/infra/errors.py 或返回错误码的代码后,"
        f"运行:{REGEN_HINT} -->",
        "",
        f"登记在 `ERROR_CODES` 里的码共 {len(specs)} 个,按 `category` 分 {len(used)} 组,"
        "每组按码名字母排序。message 和 suggestion 是目录里的默认文字,原样照抄英文:"
        "`alb_describe_errors` 返回的就是这两句。",
        "",
        f"另有 {len(unregistered)} 个码代码里会返回、但没有登记,列在本节最后。",
        "",
        "| category | 中文 | 含义 | 码数 | 码名前缀 |",
        "|---|---|---|---|---|",
    ]
    for cat in order:
        label, meaning = CATEGORY_TEXT[cat]
        codes = [s.code for s in by_cat[cat]]
        prefixes = _prefixes(codes) if codes else "—"
        lines.append(f"| `{cat}` | {label} | {meaning} | {len(codes)} | {prefixes} |")

    for cat in used:
        label, _ = CATEGORY_TEXT[cat]
        lines += [
            "",
            f"### {cat} · {label}({len(by_cat[cat])} 个)",
            "",
            "| Code | 默认 message | 默认 suggestion |",
            "|---|---|---|",
        ]
        for s in by_cat[cat]:
            lines.append(
                f"| `{s.code}` | {_cell(s.default_message)} | {_cell(s.default_suggestion)} |"
            )

    lines += [
        "",
        f"### 代码里会返回、但没有登记的码({len(unregistered)} 个)",
        "",
        "这些码会出现在 `error.code` 里,但 `ERROR_CODES` 没有它们的条目,"
        "`alb_describe_errors` 查不到;message 和 suggestion 只有出错位置当场填写的那一份。"
        "其中几个码的中文说明见「部分错误码的补充说明」一节。"
        "返回位置由脚本扫描 `src/alb` 得出,扫描规则见 `scripts/gen_errors_doc.py` 的 `_Scanner`。",
        "",
    ]
    if unregistered:
        lines += ["| Code | 返回位置 |", "|---|---|"]
        for code in unregistered:
            where = "<br>".join(f"`{p}`" for p in sorted(returned[code]))
            lines.append(f"| `{code}` | {where} |")
    else:
        lines.append("目前没有。")
    return "\n".join(lines)


# ─── doc I/O and check ──────────────────────────────────────────────

_NOTE_ROW = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|", re.MULTILINE)


def _split(doc: str) -> tuple[str, str, str]:
    """(before BEGIN incl. marker, block, END marker and after)."""
    if doc.count(BEGIN) != 1 or doc.count(END) != 1:
        raise SystemExit(
            f"docs/errors.md must contain each marker exactly once:\n  {BEGIN}\n  {END}"
        )
    head, rest = doc.split(BEGIN, 1)
    block, tail = rest.split(END, 1)
    return head + BEGIN, block, END + tail


def build(doc: str, returned: Mapping[str, set[str]] | None = None) -> str:
    """The doc with its generated block brought up to date."""
    head, _, tail = _split(doc)
    block = render_block(ERROR_CODES, scan_returned_codes() if returned is None else returned)
    return f"{head}\n{block}\n{tail}"


def check(doc_path: Path = DOC) -> list[str]:
    """Problems with docs/errors.md; empty when it is up to date.

    Two kinds: the generated block differs from what the catalog and the
    code produce now (shown as a diff), or a hand-written table row outside
    the block names a code that is neither registered nor returned."""
    doc = doc_path.read_text(encoding="utf-8")
    returned = scan_returned_codes()
    problems: list[str] = []
    want = build(doc, returned)
    if want != doc:
        diff = difflib.unified_diff(
            doc.splitlines(),
            want.splitlines(),
            fromfile=f"{doc_path.name} (now)",
            tofile=f"{doc_path.name} (regenerated)",
            lineterm="",
            n=1,
        )
        lines = list(diff)
        shown = "\n".join(lines[:60])
        if len(lines) > 60:
            shown += f"\n... ({len(lines) - 60} more diff lines)"
        problems.append(f"{doc_path} is stale; run: {REGEN_HINT}\n{shown}")

    head, _, tail = _split(doc)
    known = set(ERROR_CODES) | set(returned)
    for code in _NOTE_ROW.findall(head + "\n" + tail):
        if code not in known:
            problems.append(
                f"{doc_path}: a hand-written row names {code}, which is neither in "
                "ERROR_CODES nor returned anywhere in src/alb; update or drop the row"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 and print what differs if docs/errors.md is stale",
    )
    args = ap.parse_args(argv)

    if args.check:
        problems = check()
        for p in problems:
            print(p)
        if problems:
            return 1
        print(f"{DOC.relative_to(REPO)} is up to date")
        return 0

    doc = DOC.read_text(encoding="utf-8")
    new = build(doc)
    if new == doc:
        print(f"{DOC.relative_to(REPO)} already up to date")
    else:
        DOC.write_text(new, encoding="utf-8")
        print(f"wrote {DOC.relative_to(REPO)}")
    leftover = check()
    for p in leftover:
        print(p)
    return 1 if leftover else 0


if __name__ == "__main__":
    sys.exit(main())

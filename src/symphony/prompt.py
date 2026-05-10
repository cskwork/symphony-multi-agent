"""SPEC §5.4, §12 — strict Liquid-compatible-semantics template renderer.

Strict means:
- Unknown variables MUST fail rendering (TemplateRenderError).
- Unknown filters MUST fail rendering.
- Parse errors raise TemplateParseError.

Implemented subset (sufficient for prompt rendering):

    {{ var.path }}
    {{ var | filter }}
    {{ var | filter: "arg" }}
    {% if expr %} ... {% elsif expr %} ... {% else %} ... {% endif %}
    {% for item in list %} ... {% endfor %}
    {% raw %} ... {% endraw %}

Filters: upcase, downcase, capitalize, size, default, join, escape, strip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .errors import TemplateParseError, TemplateRenderError


_TAG_PATTERN = re.compile(
    r"\{\{(?P<expr>.+?)\}\}|\{%(?P<tag>.+?)%\}", re.DOTALL
)


# ---------------------------------------------------------------------------
# Lexer / parser
# ---------------------------------------------------------------------------


@dataclass
class _Text:
    text: str


@dataclass
class _Output:
    expr: str


@dataclass
class _If:
    branches: list[tuple[str, list[Any]]]  # (condition, body)
    else_body: list[Any] | None


@dataclass
class _For:
    var_name: str
    iterable_expr: str
    body: list[Any]


@dataclass
class _Raw:
    text: str


def _parse(text: str) -> list[Any]:
    tokens: list[Any] = []
    pos = 0
    for m in _TAG_PATTERN.finditer(text):
        if m.start() > pos:
            tokens.append(("text", text[pos : m.start()]))
        if m.group("expr") is not None:
            tokens.append(("output", m.group("expr").strip()))
        else:
            tag = m.group("tag").strip()
            tokens.append(("tag", tag))
        pos = m.end()
    if pos < len(text):
        tokens.append(("text", text[pos:]))

    nodes, idx = _parse_block(tokens, 0, end_tags=())
    if idx != len(tokens):
        raise TemplateParseError("unexpected trailing tokens")
    return nodes


def _parse_block(
    tokens: list[tuple[str, str]], idx: int, end_tags: tuple[str, ...]
) -> tuple[list[Any], int]:
    nodes: list[Any] = []
    while idx < len(tokens):
        kind, value = tokens[idx]
        if kind == "text":
            nodes.append(_Text(text=value))
            idx += 1
            continue
        if kind == "output":
            nodes.append(_Output(expr=value))
            idx += 1
            continue
        # kind == "tag"
        head = value.split(None, 1)[0] if value else ""
        if head in end_tags:
            return nodes, idx
        if head == "if":
            cond = value[len(head) :].strip()
            if not cond:
                raise TemplateParseError("empty if condition")
            branches: list[tuple[str, list[Any]]] = []
            else_body: list[Any] | None = None
            body, idx = _parse_block(
                tokens, idx + 1, end_tags=("elsif", "else", "endif")
            )
            branches.append((cond, body))
            terminated = False
            while idx < len(tokens):
                k2, v2 = tokens[idx]
                if k2 != "tag":
                    raise TemplateParseError("expected tag in if branches")
                head2 = v2.split(None, 1)[0]
                if head2 == "elsif":
                    sub_cond = v2[len(head2) :].strip()
                    if not sub_cond:
                        raise TemplateParseError("empty elsif condition")
                    body, idx = _parse_block(
                        tokens, idx + 1, end_tags=("elsif", "else", "endif")
                    )
                    branches.append((sub_cond, body))
                elif head2 == "else":
                    body, idx = _parse_block(tokens, idx + 1, end_tags=("endif",))
                    else_body = body
                elif head2 == "endif":
                    idx += 1
                    terminated = True
                    break
                else:
                    raise TemplateParseError(f"unexpected tag in if: {v2}")
            if not terminated:
                raise TemplateParseError("unterminated if")
            nodes.append(_If(branches=branches, else_body=else_body))
            continue
        if head == "for":
            rest = value[len(head) :].strip()
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.+)$", rest)
            if not m:
                raise TemplateParseError(f"malformed for tag: {value}")
            body, idx = _parse_block(tokens, idx + 1, end_tags=("endfor",))
            if idx >= len(tokens):
                raise TemplateParseError("unterminated for")
            nodes.append(_For(var_name=m.group(1), iterable_expr=m.group(2).strip(), body=body))
            idx += 1  # skip endfor
            continue
        if head == "raw":
            # collect until {% endraw %}
            buf: list[str] = []
            idx += 1
            terminated_raw = False
            while idx < len(tokens):
                k2, v2 = tokens[idx]
                if k2 == "tag" and v2.strip() == "endraw":
                    idx += 1
                    terminated_raw = True
                    break
                if k2 == "text":
                    buf.append(v2)
                elif k2 == "output":
                    buf.append("{{" + v2 + "}}")
                else:
                    buf.append("{%" + v2 + "%}")
                idx += 1
            if not terminated_raw:
                raise TemplateParseError("unterminated raw")
            nodes.append(_Raw(text="".join(buf)))
            continue
        raise TemplateParseError(f"unknown tag: {value}")
    if end_tags:
        raise TemplateParseError(f"missing end tag: expected one of {end_tags}")
    return nodes, idx


# ---------------------------------------------------------------------------
# Expression evaluator (variable access + filters)
# ---------------------------------------------------------------------------


_FILTERS = {
    "upcase": lambda v, *_: ("" if v is None else str(v)).upper(),
    "downcase": lambda v, *_: ("" if v is None else str(v)).lower(),
    "capitalize": lambda v, *_: ("" if v is None else str(v)).capitalize(),
    "size": lambda v, *_: 0 if v is None else len(v) if hasattr(v, "__len__") else 0,
    "join": lambda v, sep=", ": sep.join(str(x) for x in (v or [])),
    "default": lambda v, fallback="": v if v not in (None, "", [], {}) else fallback,
    "escape": lambda v, *_: (
        "" if v is None else str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ),
    "strip": lambda v, *_: ("" if v is None else str(v)).strip(),
}


_LITERAL_RE = re.compile(
    r'^(?:"(?P<dq>(?:\\.|[^"\\])*)"|\'(?P<sq>(?:\\.|[^\'\\])*)\'|(?P<int>-?\d+)|(?P<bool>true|false|nil|null))$'
)
_PATH_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$")


_MISSING = object()


def _resolve_path(path: str, env: dict[str, Any]) -> Any:
    """Walk a dotted/indexed path strictly. Missing -> _MISSING."""
    # Split top-level identifier
    if not path:
        return _MISSING
    head_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", path)
    if not head_match:
        raise TemplateRenderError(f"invalid variable name: {path}")
    head = head_match.group(1)
    if head not in env:
        return _MISSING
    current: Any = env[head]
    rest = path[head_match.end() :]
    while rest:
        if rest.startswith("."):
            sub_match = re.match(r"^\.([A-Za-z_][A-Za-z0-9_]*)", rest)
            if not sub_match:
                raise TemplateRenderError(f"invalid attribute access: {path}")
            attr = sub_match.group(1)
            if isinstance(current, dict):
                if attr not in current:
                    return _MISSING
                current = current[attr]
            else:
                if not hasattr(current, attr):
                    return _MISSING
                current = getattr(current, attr)
            rest = rest[sub_match.end() :]
        elif rest.startswith("["):
            sub_match = re.match(r"^\[(-?\d+)\]", rest)
            if not sub_match:
                raise TemplateRenderError(f"invalid index access: {path}")
            idx = int(sub_match.group(1))
            try:
                current = current[idx]
            except (KeyError, IndexError, TypeError):
                return _MISSING
            rest = rest[sub_match.end() :]
        else:
            raise TemplateRenderError(f"invalid path tail: {path}")
    return current


def _eval_value(token: str, env: dict[str, Any]) -> Any:
    token = token.strip()
    lit = _LITERAL_RE.match(token)
    if lit:
        if lit.group("dq") is not None:
            return bytes(lit.group("dq"), "utf-8").decode("unicode_escape")
        if lit.group("sq") is not None:
            return bytes(lit.group("sq"), "utf-8").decode("unicode_escape")
        if lit.group("int") is not None:
            return int(lit.group("int"))
        kw = lit.group("bool")
        if kw == "true":
            return True
        if kw == "false":
            return False
        return None
    if not _PATH_TOKEN_RE.match(token):
        raise TemplateRenderError(f"invalid expression: {token}")
    val = _resolve_path(token, env)
    if val is _MISSING:
        raise TemplateRenderError(f"unknown variable: {token}")
    return val


def _split_pipeline(expr: str) -> list[str]:
    """Split on `|` outside quotes."""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(expr):
        c = expr[i]
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < len(expr):
                buf.append(expr[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in '"\'':
            quote = c
            buf.append(c)
        elif c == "|":
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf).strip())
    return parts


def _eval_output_expr(expr: str, env: dict[str, Any]) -> Any:
    parts = _split_pipeline(expr)
    value = _eval_value(parts[0], env)
    for filter_segment in parts[1:]:
        if not filter_segment:
            raise TemplateRenderError("empty filter segment")
        if ":" in filter_segment:
            name, raw_args = filter_segment.split(":", 1)
            name = name.strip()
            args = [_eval_value(a, env) for a in _split_args(raw_args)]
        else:
            name = filter_segment.strip()
            args = []
        if name not in _FILTERS:
            raise TemplateRenderError(f"unknown filter: {name}")
        value = _FILTERS[name](value, *args)
    return value


def _split_args(raw: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(raw):
        c = raw[i]
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < len(raw):
                buf.append(raw[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in '"\'':
            quote = c
            buf.append(c)
        elif c == ",":
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _eval_condition(expr: str, env: dict[str, Any]) -> bool:
    expr = expr.strip()
    # Support: A == B, A != B, A, !A
    for op in (" == ", " != "):
        if op in expr:
            left, right = expr.split(op, 1)
            lv = _eval_value(left.strip(), env)
            rv = _eval_value(right.strip(), env)
            return (lv == rv) if op.strip() == "==" else (lv != rv)
    if expr.startswith("!"):
        return not _truthy(_eval_value(expr[1:].strip(), env))
    return _truthy(_eval_value(expr, env))


def _truthy(value: Any) -> bool:
    if value is None or value is False:
        return False
    if value == 0:
        return False
    if isinstance(value, (str, list, tuple, dict)) and len(value) == 0:
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render(template: str, env: dict[str, Any]) -> str:
    """§5.4 — strict template render. Unknown vars/filters fail."""
    try:
        ast = _parse(template)
    except TemplateParseError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise TemplateParseError(str(exc)) from exc
    return _render_nodes(ast, env)


def _render_nodes(nodes: Iterable[Any], env: dict[str, Any]) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, _Text):
            out.append(node.text)
        elif isinstance(node, _Raw):
            out.append(node.text)
        elif isinstance(node, _Output):
            value = _eval_output_expr(node.expr, env)
            out.append("" if value is None else str(value))
        elif isinstance(node, _If):
            rendered = False
            for cond, body in node.branches:
                if _eval_condition(cond, env):
                    out.append(_render_nodes(body, env))
                    rendered = True
                    break
            if not rendered and node.else_body is not None:
                out.append(_render_nodes(node.else_body, env))
        elif isinstance(node, _For):
            iterable = _eval_value(node.iterable_expr, env)
            if iterable is None:
                continue
            if isinstance(iterable, dict):
                items = list(iterable.items())
            elif isinstance(iterable, (list, tuple)):
                items = list(iterable)
            else:
                raise TemplateRenderError(
                    f"for-loop target is not iterable: {node.iterable_expr}"
                )
            for item in items:
                child = dict(env)
                child[node.var_name] = item
                out.append(_render_nodes(node.body, child))
        else:  # pragma: no cover - defensive
            raise TemplateRenderError(f"unknown node: {type(node).__name__}")
    return "".join(out)


def build_prompt_env(
    issue_obj: Any,
    attempt: int | None,
    language: str | None = None,
) -> dict[str, Any]:
    """§12.1 — input variables for prompt rendering.

    `language` is exposed to the template as `{{ language }}` (normalized
    to a supported code: `en` / `ko`, EN fallback) so WORKFLOW.md authors
    can branch on it with `{% if language == 'ko' %}…{% endif %}`. Default
    is English to keep behavior stable for callers that don't pass it.
    """
    if hasattr(issue_obj, "to_template_dict"):
        issue_dict = issue_obj.to_template_dict()
    else:
        issue_dict = dict(issue_obj)
    from .i18n import normalize_language

    return {
        "issue": issue_dict,
        "attempt": attempt,
        "language": normalize_language(language),
    }


def build_first_turn_prompt(
    *,
    prompt_template: str,
    issue: Any,
    attempt: int | None,
    language: str,
    max_turns: int,
) -> tuple[str, dict[str, Any]]:
    """Construct the first-turn prompt sent to a worker.

    Prepends a one-line doc-language directive (resolved from `language`
    via `i18n.doc_language_preamble`) before the rendered WORKFLOW.md
    body so artefacts come back in the operator-chosen language even if
    the WORKFLOW.md body itself is written in a different one.

    Returns `(final_prompt, env)` so callers can keep `env` for later
    bookkeeping (e.g. logging, tests).
    """
    from .i18n import doc_language_preamble

    preamble = doc_language_preamble(language)
    env = build_prompt_env(issue, attempt, language=language)
    env["turn_number"] = 1
    env["max_turns"] = max_turns
    body = render(prompt_template, env)
    return preamble + "\n\n" + body, env


def build_continuation_prompt(
    *,
    language: str,
    turn_number: int,
    max_turns: int,
) -> str:
    """Construct the prompt for turn 2+ of a multi-turn run.

    The continuation message is a small fixed string (no WORKFLOW.md body
    is re-rendered). The doc-language directive is re-prepended every turn
    because long runs drift back toward the model's default locale once
    the first turn rotates out of the active context window.
    """
    from .i18n import doc_language_preamble

    preamble = doc_language_preamble(language)
    body = (
        "Continue working on the issue. Re-check the tracker if needed. "
        f"This is turn {turn_number} of up to {max_turns}."
    )
    return preamble + "\n\n" + body

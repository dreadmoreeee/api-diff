"""Markdown and JSON renderers."""
from __future__ import annotations

import json

from .diff import BREAKING, INFO, NON_BREAKING, SEVERITIES, Change

_TITLES = {BREAKING: "Breaking changes", NON_BREAKING: "Non-breaking changes", INFO: "Info"}


def summary(changes: list[Change]) -> dict:
    s = {sev: sum(1 for c in changes if c.severity == sev) for sev in SEVERITIES}
    s["total"] = len(changes)
    return s


def to_json(changes: list[Change]) -> str:
    return json.dumps({"summary": summary(changes), "changes": [c.to_dict() for c in changes]},
                      indent=2, ensure_ascii=True)


def _val(v) -> str:
    text = json.dumps(v, ensure_ascii=True, sort_keys=True)
    return text if len(text) <= 60 else text[:57] + "..."


def _where(c: Change) -> str:
    parts = [p for p in (c.method, c.path) if p]
    where = " ".join(parts) if parts else "(global)"
    return where + (f" `{c.pointer}`" if c.pointer else "")


def to_markdown(changes: list[Change]) -> str:
    s = summary(changes)
    lines = ["# API diff", "",
             f"**{s[BREAKING]} breaking**, {s[NON_BREAKING]} non-breaking, {s[INFO]} info "
             f"({s['total']} changes)", ""]
    for sev in SEVERITIES:
        group = [c for c in changes if c.severity == sev]
        if not group:
            continue
        lines += [f"## {_TITLES[sev]} ({len(group)})", ""]
        for c in group:
            line = f"- `{c.rule}` {_where(c)}: {c.message}"
            if c.old is not None or c.new is not None:
                line += f" (old: {_val(c.old)}, new: {_val(c.new)})"
            lines.append(line)
        lines.append("")
    if not changes:
        lines += ["No differences found.", ""]
    return "\n".join(lines)

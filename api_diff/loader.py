"""Load OpenAPI documents and resolve local $ref pointers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class ApiDiffError(Exception):
    """Usage or parse error (CLI exit code 2)."""


def parse_spec(text: str, hint: str = "") -> dict:
    """Parse YAML or JSON text. `hint` is a file name used to pick the parser."""
    ext = Path(hint).suffix.lower()
    try:
        if ext == ".json" or (ext not in (".yaml", ".yml") and text.lstrip().startswith(("{", "["))):
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:
        raise ApiDiffError(f"cannot parse {hint or 'input'}: {exc}") from exc
    if not isinstance(data, dict):
        raise ApiDiffError(f"{hint or 'input'}: top level must be a mapping")
    version = str(data.get("openapi", ""))
    if not version.startswith("3."):
        raise ApiDiffError(f"{hint or 'input'}: not an OpenAPI 3.x document (missing 'openapi: 3.x')")
    return data


def load_spec(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ApiDiffError(f"cannot read {path}: {exc}") from exc
    return parse_spec(text, path)


def _lookup(root: dict, ref: str) -> Any:
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(ref)
    return node


def resolve_refs(node: Any, root: dict, _stack: tuple = ()) -> Any:
    """Return a copy of `node` with local refs inlined.

    A ref that points back into itself is replaced with {"x-circular-ref": ref};
    external or dangling refs are kept as {"$ref": ..., "x-unresolved": True}.
    """
    if isinstance(node, list):
        return [resolve_refs(i, root, _stack) for i in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith("#/"):
            return {"$ref": ref, "x-unresolved": True}
        if ref in _stack:
            return {"x-circular-ref": ref}
        try:
            target = _lookup(root, ref)
        except KeyError:
            return {"$ref": ref, "x-unresolved": True}
        resolved = resolve_refs(target, root, _stack + (ref,))
        # sibling keys (OpenAPI 3.1) override the target
        extra = {k: resolve_refs(v, root, _stack) for k, v in node.items() if k != "$ref"}
        if extra and isinstance(resolved, dict):
            resolved = {**resolved, **extra}
        return resolved
    return {k: resolve_refs(v, root, _stack) for k, v in node.items()}

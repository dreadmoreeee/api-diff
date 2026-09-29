"""Rules that classify differences between two OpenAPI 3.x documents."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .loader import resolve_refs

BREAKING = "breaking"
NON_BREAKING = "non-breaking"
INFO = "info"
SEVERITIES = (BREAKING, NON_BREAKING, INFO)
METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


@dataclass
class Change:
    rule: str
    severity: str
    path: str
    method: str
    pointer: str
    message: str
    old: Any = None
    new: Any = None
    probable_rename: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["probable_rename"]:
            del d["probable_rename"]
        return d


def _esc(part: str) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


def normalize_schema(s: Any) -> Any:
    """Flatten allOf into a single object schema (properties, required, type)."""
    if not isinstance(s, dict) or "allOf" not in s:
        return s
    merged = {k: v for k, v in s.items() if k != "allOf"}
    props = dict(merged.get("properties") or {})
    required = list(merged.get("required") or [])
    for part in s["allOf"]:
        part = normalize_schema(part)
        if not isinstance(part, dict):
            continue
        props.update(part.get("properties") or {})
        required += [r for r in part.get("required") or [] if r not in required]
        for k, v in part.items():
            if k not in ("properties", "required") and k not in merged:
                merged[k] = v
    if props:
        merged["properties"] = props
    if required:
        merged["required"] = required
    return merged


def _norm_security(reqs: Any) -> frozenset:
    out = set()
    for alt in reqs or []:
        if isinstance(alt, dict):
            out.add(frozenset((k, tuple(sorted(v or []))) for k, v in alt.items()))
    return frozenset(out)


def _fmt_security(fs: frozenset) -> list:
    return sorted(sorted(f"{n}[{','.join(sc)}]" if sc else n for n, sc in alt) for alt in fs)


class Differ:
    def __init__(self, old: dict, new: dict):
        self.old, self.new = old, new
        self.changes: list[Change] = []

    def add(self, rule, severity, loc, pointer, message, old=None, new=None, **kw):
        self.changes.append(Change(rule, severity, loc[0], loc[1], pointer, message, old, new, **kw))

    # ---- schemas -------------------------------------------------------
    def schema(self, o, n, loc, ptr, direction):
        o, n = normalize_schema(o), normalize_schema(n)
        if not isinstance(o, dict) or not isinstance(n, dict):
            return
        ot, nt = o.get("type"), n.get("type")
        if ot is not None and nt is not None and ot != nt:
            self.add("type-changed", BREAKING, loc, ptr + "/type",
                     f"type changed from {ot} to {nt}", ot, nt)
            return
        oe, ne = o.get("enum"), n.get("enum")
        if isinstance(oe, list) and isinstance(ne, list):
            removed = [v for v in oe if v not in ne]
            added = [v for v in ne if v not in oe]
            if removed:
                self.add("enum-values-removed", BREAKING, loc, ptr + "/enum",
                         f"enum values removed: {removed}", oe, ne)
            if added:
                self.add("enum-values-added", NON_BREAKING, loc, ptr + "/enum",
                         f"enum values added: {added}", oe, ne)
        elif isinstance(ne, list) and oe is None and direction == "request":
            self.add("enum-added", BREAKING, loc, ptr + "/enum",
                     "request value restricted to an enum", None, ne)
        if ("oneOf" in o or "oneOf" in n or "anyOf" in o or "anyOf" in n) and \
                any(o.get(k) != n.get(k) for k in ("oneOf", "anyOf")):
            self.add("composition-changed", INFO, loc, ptr,
                     "oneOf/anyOf changed; not analysed in depth")
        op, np_ = o.get("properties") or {}, n.get("properties") or {}
        oreq, nreq = o.get("required") or [], n.get("required") or []
        if isinstance(op, dict) and isinstance(np_, dict):
            for name in op:
                if name not in np_:
                    if direction == "response":
                        self.add("response-field-removed", BREAKING, loc, f"{ptr}/properties/{_esc(name)}",
                                 f"response field '{name}' removed", op[name], None)
                    else:
                        self.add("request-field-removed", INFO, loc, f"{ptr}/properties/{_esc(name)}",
                                 f"request field '{name}' removed", op[name], None)
            for name in np_:
                p = f"{ptr}/properties/{_esc(name)}"
                if name not in op:
                    if direction == "response":
                        self.add("response-field-added", NON_BREAKING, loc, p,
                                 f"response field '{name}' added", None, np_[name])
                    elif name in nreq:
                        self.add("request-field-required-added", BREAKING, loc, p,
                                 f"new required request field '{name}'", None, np_[name])
                    else:
                        self.add("request-field-added", NON_BREAKING, loc, p,
                                 f"optional request field '{name}' added", None, np_[name])
                else:
                    self.schema(op[name], np_[name], loc, p, direction)
        if direction == "request":
            for name in nreq:
                if name not in oreq and name in op:
                    self.add("request-field-became-required", BREAKING, loc,
                             f"{ptr}/required", f"request field '{name}' became required",
                             False, True)
        if isinstance(o.get("items"), dict) and isinstance(n.get("items"), dict):
            self.schema(o["items"], n["items"], loc, ptr + "/items", direction)
        if isinstance(o.get("additionalProperties"), dict) and isinstance(n.get("additionalProperties"), dict):
            self.schema(o["additionalProperties"], n["additionalProperties"], loc,
                        ptr + "/additionalProperties", direction)

    def content(self, o, n, loc, ptr, direction):
        o, n = o or {}, n or {}
        for mt in o:
            if mt not in n:
                rule = "request-media-type-removed" if direction == "request" else "response-media-type-removed"
                self.add(rule, BREAKING, loc, f"{ptr}/{_esc(mt)}",
                         f"{direction} media type {mt} removed", mt, None)
        for mt in n:
            if mt not in o:
                self.add("media-type-added", NON_BREAKING, loc, f"{ptr}/{_esc(mt)}",
                         f"media type {mt} added", None, mt)
            else:
                self.schema((o[mt] or {}).get("schema"), (n[mt] or {}).get("schema"),
                            loc, f"{ptr}/{_esc(mt)}/schema", direction)

    # ---- operations ----------------------------------------------------
    @staticmethod
    def params(path_item, op):
        merged = {}
        for p in list(path_item.get("parameters") or []) + list(op.get("parameters") or []):
            if isinstance(p, dict) and "name" in p and "in" in p:
                merged[(p["in"], p["name"])] = p
        return merged

    def parameters(self, po, oo, pn, on, loc):
        a, b = self.params(po, oo), self.params(pn, on)
        removed = [k for k in a if k not in b]
        added = [k for k in b if k not in a]

        def ptype(p):
            return (p.get("schema") or {}).get("type")

        renames: dict = {}
        free = list(added)
        for k in removed:
            for cand in free:
                if cand[0] == k[0] and ptype(a[k]) == ptype(b[cand]):
                    renames[k] = cand
                    free.remove(cand)
                    break
        renamed_new = set(renames.values())
        for k in removed:
            ptr = f"/parameters/{k[0]}/{_esc(k[1])}"
            if k in renames:
                self.add("param-removed", BREAKING, loc, ptr,
                         f"{k[0]} parameter '{k[1]}' removed (probable rename to '{renames[k][1]}')",
                         k[1], None, probable_rename=True)
            else:
                self.add("param-removed", BREAKING, loc, ptr,
                         f"{k[0]} parameter '{k[1]}' removed", k[1], None)
        for k in added:
            ptr = f"/parameters/{k[0]}/{_esc(k[1])}"
            rn = k in renamed_new
            hint = " (probable rename)" if rn else ""
            if b[k].get("required") or k[0] == "path":
                self.add("param-required-added", BREAKING, loc, ptr,
                         f"new required {k[0]} parameter '{k[1]}'{hint}", None, k[1],
                         probable_rename=rn)
            else:
                self.add("param-added", NON_BREAKING, loc, ptr,
                         f"optional {k[0]} parameter '{k[1]}' added{hint}", None, k[1],
                         probable_rename=rn)
        for k in a:
            if k not in b:
                continue
            ptr = f"/parameters/{k[0]}/{_esc(k[1])}"
            ro, rn_ = bool(a[k].get("required")), bool(b[k].get("required"))
            if not ro and rn_:
                self.add("param-became-required", BREAKING, loc, ptr + "/required",
                         f"{k[0]} parameter '{k[1]}' became required", False, True)
            elif ro and not rn_:
                self.add("param-became-optional", NON_BREAKING, loc, ptr + "/required",
                         f"{k[0]} parameter '{k[1]}' became optional", True, False)
            self.schema(a[k].get("schema"), b[k].get("schema"), loc, ptr + "/schema", "request")
            self.docs(a[k], b[k], loc, ptr)

    def docs(self, o, n, loc, ptr):
        for key in ("summary", "description"):
            if o.get(key) != n.get(key) and (o.get(key) or n.get(key)):
                self.add(f"{key}-changed", INFO, loc, f"{ptr}/{key}", f"{key} changed",
                         o.get(key), n.get(key))
        if not o.get("deprecated") and n.get("deprecated"):
            self.add("deprecated-added", INFO, loc, ptr + "/deprecated", "marked deprecated",
                     False, True)
        elif o.get("deprecated") and not n.get("deprecated"):
            self.add("deprecated-removed", INFO, loc, ptr + "/deprecated", "no longer deprecated",
                     True, False)

    def request_body(self, o, n, loc):
        ptr = "/requestBody"
        if not o and not n:
            return
        if not o:
            req = bool(n.get("required"))
            self.add("request-body-required-added" if req else "request-body-added",
                     BREAKING if req else NON_BREAKING, loc, ptr, "request body added",
                     None, "required" if req else "optional")
            return
        if not n:
            self.add("request-body-removed", INFO, loc, ptr, "request body removed", "present", None)
            return
        if not o.get("required") and n.get("required"):
            self.add("request-body-became-required", BREAKING, loc, ptr + "/required",
                     "request body became required", False, True)
        self.content(o.get("content"), n.get("content"), loc, ptr + "/content", "request")

    def responses(self, o, n, loc):
        o = {str(k): v for k, v in (o or {}).items()}
        n = {str(k): v for k, v in (n or {}).items()}
        for code in o:
            ptr = f"/responses/{code}"
            if code not in n:
                if code.startswith("2"):
                    self.add("success-response-removed", BREAKING, loc, ptr,
                             f"success response {code} removed", code, None)
                else:
                    self.add("response-removed", INFO, loc, ptr, f"response {code} removed", code, None)
            else:
                self.content((o[code] or {}).get("content"), (n[code] or {}).get("content"),
                             loc, ptr + "/content", "response")
        for code in n:
            if code not in o:
                self.add("response-added", NON_BREAKING, loc, f"/responses/{code}",
                         f"response {code} added", None, code)

    def security(self, o_eff, n_eff, loc, ptr, where):
        a, b = _norm_security(o_eff), _norm_security(n_eff)
        if a == b:
            return
        old_f, new_f = _fmt_security(a), _fmt_security(b)
        if not a and b:
            self.add("auth-added", BREAKING, loc, ptr, f"{where}: authentication now required",
                     old_f, new_f)
        elif a and not b:
            self.add("auth-removed", NON_BREAKING, loc, ptr,
                     f"{where}: authentication no longer required", old_f, new_f)
        elif a <= b:
            self.add("auth-relaxed", NON_BREAKING, loc, ptr,
                     f"{where}: additional security alternative accepted", old_f, new_f)
        else:
            self.add("auth-changed", BREAKING, loc, ptr,
                     f"{where}: security requirements changed", old_f, new_f)

    def operation(self, po, oo, pn, on, loc):
        self.docs(oo, on, loc, "")
        self.parameters(po, oo, pn, on, loc)
        self.request_body(oo.get("requestBody"), on.get("requestBody"), loc)
        self.responses(oo.get("responses"), on.get("responses"), loc)
        if "security" in oo or "security" in on:
            self.security(oo.get("security", self.old.get("security")),
                          on.get("security", self.new.get("security")),
                          loc, "/security", "operation")

    def run(self) -> list[Change]:
        op_, np_ = self.old.get("paths") or {}, self.new.get("paths") or {}
        for path in op_:
            if path not in np_:
                self.add("path-removed", BREAKING, (path, ""), "", f"path {path} removed", path, None)
                continue
            po, pn = op_[path] or {}, np_[path] or {}
            for m in METHODS:
                if m in po and m not in pn:
                    self.add("operation-removed", BREAKING, (path, m.upper()), "",
                             f"operation {m.upper()} {path} removed", m.upper(), None)
                elif m in po:
                    self.operation(po, po[m] or {}, pn, pn[m] or {}, (path, m.upper()))
        for path in np_:
            for m in METHODS:
                if m in (np_[path] or {}) and m not in (op_.get(path) or {}):
                    self.add("operation-added", NON_BREAKING, (path, m.upper()), "",
                             f"operation {m.upper()} {path} added", None, m.upper())
        # global security and schemes
        if "security" in self.old or "security" in self.new:
            self.security(self.old.get("security"), self.new.get("security"),
                          ("", ""), "/security", "global")
        os_ = (self.old.get("components") or {}).get("securitySchemes") or {}
        ns_ = (self.new.get("components") or {}).get("securitySchemes") or {}
        for name, s in os_.items():
            ptr = f"/components/securitySchemes/{_esc(name)}"
            if name not in ns_:
                self.add("security-scheme-removed", BREAKING, ("", ""), ptr,
                         f"security scheme '{name}' removed", s.get("type"), None)
            elif s.get("type") != ns_[name].get("type"):
                self.add("security-scheme-type-changed", BREAKING, ("", ""), ptr + "/type",
                         f"security scheme '{name}' type changed from {s.get('type')} to {ns_[name].get('type')}",
                         s.get("type"), ns_[name].get("type"))
            else:
                for k in ("scheme", "in", "name"):
                    if s.get(k) != ns_[name].get(k):
                        self.add("security-scheme-changed", BREAKING, ("", ""), f"{ptr}/{k}",
                                 f"security scheme '{name}' {k} changed", s.get(k), ns_[name].get(k))
        for name in ns_:
            if name not in os_:
                self.add("security-scheme-added", NON_BREAKING, ("", ""),
                         f"/components/securitySchemes/{_esc(name)}",
                         f"security scheme '{name}' added", None, ns_[name].get("type"))
        order = {s: i for i, s in enumerate(SEVERITIES)}
        self.changes.sort(key=lambda c: order[c.severity])
        return self.changes


def diff_specs(old: dict, new: dict) -> list[Change]:
    """Diff two parsed OpenAPI documents (refs are resolved here)."""
    def prep(doc):
        d = dict(doc)
        d["paths"] = resolve_refs(doc.get("paths") or {}, doc)
        return d
    return Differ(prep(old), prep(new)).run()

import copy
import json

import pytest

from api_diff import diff_specs, parse_spec, resolve_refs, to_json, to_markdown
from api_diff.loader import ApiDiffError


def spec(paths=None, **extra):
    d = {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": paths or {}}
    d.update(extra)
    return d


def op(**kw):
    kw.setdefault("responses", {"200": {"description": "ok"}})
    return kw


def rules(old, new, severity=None):
    return {(c.rule) for c in diff_specs(old, new) if severity in (None, c.severity)}


def obj(props, required=()):
    s = {"type": "object", "properties": props}
    if required:
        s["required"] = list(required)
    return s


def body(schema, required=True):
    return {"required": required, "content": {"application/json": {"schema": schema}}}


def resp(schema, code="200"):
    return {code: {"description": "ok", "content": {"application/json": {"schema": schema}}}}


def test_identical_specs_no_changes():
    s = spec({"/a": {"get": op()}})
    assert diff_specs(s, copy.deepcopy(s)) == []


def test_path_removed_and_operation_removed():
    old = spec({"/a": {"get": op(), "post": op()}, "/b": {"get": op()}})
    new = spec({"/a": {"get": op()}})
    got = {c.rule: c for c in diff_specs(old, new)}
    assert got["path-removed"].severity == "breaking" and got["path-removed"].path == "/b"
    assert got["operation-removed"].method == "POST"


def test_operation_and_path_added_non_breaking():
    old = spec({"/a": {"get": op()}})
    new = spec({"/a": {"get": op(), "post": op()}, "/c": {"get": op()}})
    changes = diff_specs(old, new)
    assert [c.rule for c in changes] == ["operation-added"] * 2
    assert all(c.severity == "non-breaking" for c in changes)


def q(name, required=False, type_="string", **kw):
    return {"name": name, "in": "query", "required": required, "schema": {"type": type_, **kw}}


def test_param_removed_breaking():
    old = spec({"/a": {"get": op(parameters=[q("x")])}})
    new = spec({"/a": {"get": op()}})
    assert rules(old, new, "breaking") == {"param-removed"}


def test_param_rename_flagged_probable():
    old = spec({"/a": {"get": op(parameters=[q("owner_id")])}})
    new = spec({"/a": {"get": op(parameters=[q("ownerId")])}})
    changes = diff_specs(old, new)
    assert {c.rule for c in changes} == {"param-removed", "param-added"}
    assert all(c.probable_rename for c in changes)
    assert "probable rename" in changes[0].message + changes[1].message


def test_rename_not_flagged_when_type_differs():
    old = spec({"/a": {"get": op(parameters=[q("a", type_="string")])}})
    new = spec({"/a": {"get": op(parameters=[q("b", type_="integer")])}})
    assert not any(c.probable_rename for c in diff_specs(old, new))


def test_optional_param_became_required_and_back():
    a = spec({"/a": {"get": op(parameters=[q("x", False)])}})
    b = spec({"/a": {"get": op(parameters=[q("x", True)])}})
    assert rules(a, b) == {"param-became-required"}
    assert rules(a, b, "breaking") == {"param-became-required"}
    assert rules(b, a, "non-breaking") == {"param-became-optional"}


def test_new_required_and_optional_param():
    old = spec({"/a": {"get": op()}})
    assert rules(old, spec({"/a": {"get": op(parameters=[q("x", True)])}}), "breaking") == {"param-required-added"}
    assert rules(old, spec({"/a": {"get": op(parameters=[q("x")])}}), "non-breaking") == {"param-added"}


def test_path_level_parameters_merged():
    old = spec({"/a": {"parameters": [q("x", True)], "get": op()}})
    new = spec({"/a": {"get": op()}})
    assert rules(old, new) == {"param-removed"}


def test_param_type_change():
    old = spec({"/a": {"get": op(parameters=[q("x", type_="integer")])}})
    new = spec({"/a": {"get": op(parameters=[q("x", type_="string")])}})
    c = diff_specs(old, new)[0]
    assert (c.rule, c.severity, c.old, c.new) == ("type-changed", "breaking", "integer", "string")
    assert c.pointer == "/parameters/query/x/schema/type"


def test_request_body_field_became_required():
    old = spec({"/a": {"post": op(requestBody=body(obj({"n": {"type": "string"}})))}})
    new = spec({"/a": {"post": op(requestBody=body(obj({"n": {"type": "string"}}, ["n"])))}})
    assert rules(old, new, "breaking") == {"request-field-became-required"}


def test_request_body_new_required_and_optional_field():
    base = obj({"n": {"type": "string"}})
    old = spec({"/a": {"post": op(requestBody=body(base))}})
    req = spec({"/a": {"post": op(requestBody=body(obj({"n": {"type": "string"}, "m": {"type": "string"}}, ["m"])))}})
    opt = spec({"/a": {"post": op(requestBody=body(obj({"n": {"type": "string"}, "m": {"type": "string"}})))}})
    assert rules(old, req, "breaking") == {"request-field-required-added"}
    assert rules(old, opt, "non-breaking") == {"request-field-added"}


def test_request_body_became_required():
    old = spec({"/a": {"post": op(requestBody=body({"type": "object"}, required=False))}})
    new = spec({"/a": {"post": op(requestBody=body({"type": "object"}, required=True))}})
    assert rules(old, new) == {"request-body-became-required"}


def test_response_field_removed_and_added():
    old = spec({"/a": {"get": op(responses=resp(obj({"a": {"type": "string"}, "b": {"type": "string"}})))}})
    new = spec({"/a": {"get": op(responses=resp(obj({"a": {"type": "string"}, "c": {"type": "string"}})))}})
    got = {c.rule: c.severity for c in diff_specs(old, new)}
    assert got == {"response-field-removed": "breaking", "response-field-added": "non-breaking"}


def test_nested_response_field_removed_pointer():
    inner_old = obj({"user": obj({"id": {"type": "integer"}, "email": {"type": "string"}})})
    inner_new = obj({"user": obj({"id": {"type": "integer"}})})
    old = spec({"/a": {"get": op(responses=resp(inner_old))}})
    new = spec({"/a": {"get": op(responses=resp(inner_new))}})
    c = diff_specs(old, new)[0]
    assert c.pointer == ("/responses/200/content/application~1json/schema"
                         "/properties/user/properties/email")


def test_response_type_change_in_array_items():
    old = spec({"/a": {"get": op(responses=resp({"type": "array", "items": {"type": "integer"}}))}})
    new = spec({"/a": {"get": op(responses=resp({"type": "array", "items": {"type": "string"}}))}})
    c = diff_specs(old, new)[0]
    assert c.rule == "type-changed" and c.pointer.endswith("/schema/items/type")


def test_enum_removed_and_added():
    old = spec({"/a": {"get": op(parameters=[q("s", enum=["a", "b"])])}})
    new = spec({"/a": {"get": op(parameters=[q("s", enum=["b", "c"])])}})
    got = {c.rule: c.severity for c in diff_specs(old, new)}
    assert got == {"enum-values-removed": "breaking", "enum-values-added": "non-breaking"}


def test_enum_removed_in_response():
    old = spec({"/a": {"get": op(responses=resp({"type": "string", "enum": ["x", "y"]}))}})
    new = spec({"/a": {"get": op(responses=resp({"type": "string", "enum": ["x"]}))}})
    assert rules(old, new) == {"enum-values-removed"}


def test_success_response_removed():
    old = spec({"/a": {"post": op(responses={"200": {"description": "o"}, "201": {"description": "c"},
                                             "404": {"description": "n"}})}})
    new = spec({"/a": {"post": op(responses={"201": {"description": "c"}})}})
    got = {c.rule: c.severity for c in diff_specs(old, new)}
    assert got == {"success-response-removed": "breaking", "response-removed": "info"}


def test_integer_status_codes_from_yaml():
    old = parse_spec("openapi: 3.0.0\npaths:\n  /a:\n    get:\n      responses:\n        200: {description: ok}\n")
    new = parse_spec("openapi: 3.0.0\npaths:\n  /a:\n    get:\n      responses:\n        204: {description: ok}\n")
    assert rules(old, new, "breaking") == {"success-response-removed"}


def test_info_changes():
    old = spec({"/a": {"get": op(summary="one", description="d")}})
    new = spec({"/a": {"get": op(summary="two", description="e", deprecated=True)}})
    changes = diff_specs(old, new)
    assert {c.rule for c in changes} == {"summary-changed", "description-changed", "deprecated-added"}
    assert all(c.severity == "info" for c in changes)


def test_global_security_added_and_changed():
    old = spec({"/a": {"get": op()}})
    new = spec({"/a": {"get": op()}}, security=[{"key": []}])
    assert rules(old, new, "breaking") == {"auth-added"}
    other = spec({"/a": {"get": op()}}, security=[{"oauth": ["r"]}])
    assert rules(new, other, "breaking") == {"auth-changed"}
    scopes = spec({"/a": {"get": op()}}, security=[{"oauth": ["r", "w"]}])
    assert rules(other, scopes, "breaking") == {"auth-changed"}


def test_operation_security_added_overrides_global():
    old = spec({"/a": {"get": op()}}, security=[{"key": []}])
    new = spec({"/a": {"get": op(security=[{"oauth": []}])}}, security=[{"key": []}])
    c = diff_specs(old, new)[0]
    assert (c.rule, c.severity, c.method) == ("auth-changed", "breaking", "GET")


def test_operation_security_removed_non_breaking():
    old = spec({"/a": {"get": op(security=[{"key": []}])}})
    new = spec({"/a": {"get": op(security=[])}})
    assert rules(old, new) == {"auth-removed"}
    assert rules(old, new, "non-breaking") == {"auth-removed"}


def test_extra_alternative_is_relaxed():
    old = spec({"/a": {"get": op(security=[{"key": []}])}})
    new = spec({"/a": {"get": op(security=[{"key": []}, {"oauth": []}])}})
    assert rules(old, new, "non-breaking") == {"auth-relaxed"}


def test_security_scheme_removed_and_type_changed():
    old = spec(components={"securitySchemes": {
        "a": {"type": "apiKey", "in": "header", "name": "K"},
        "b": {"type": "http", "scheme": "basic"}}})
    new = spec(components={"securitySchemes": {"b": {"type": "oauth2"}}})
    got = {c.rule for c in diff_specs(old, new) if c.severity == "breaking"}
    assert got == {"security-scheme-removed", "security-scheme-type-changed"}


def test_security_scheme_detail_changed():
    old = spec(components={"securitySchemes": {"a": {"type": "apiKey", "in": "header", "name": "K"}}})
    new = spec(components={"securitySchemes": {"a": {"type": "apiKey", "in": "query", "name": "K"}}})
    assert rules(old, new, "breaking") == {"security-scheme-changed"}


# ---- $ref resolution ---------------------------------------------------

def refspec(schemas, prop_ref="#/components/schemas/A"):
    return spec({"/a": {"get": op(responses=resp({"$ref": prop_ref}))}},
                components={"schemas": schemas})


def test_ref_resolved_and_nested():
    old = refspec({"A": obj({"b": {"$ref": "#/components/schemas/B"}}),
                   "B": obj({"c": {"$ref": "#/components/schemas/C"}}),
                   "C": obj({"x": {"type": "string"}, "y": {"type": "string"}})})
    new = copy.deepcopy(old)
    del new["components"]["schemas"]["C"]["properties"]["y"]
    c = diff_specs(old, new)[0]
    assert c.rule == "response-field-removed"
    assert c.pointer.endswith("/properties/b/properties/c/properties/y")


def test_ref_change_in_component_seen_through_ref_only():
    old = refspec({"A": obj({"x": {"type": "integer"}})})
    new = refspec({"A": obj({"x": {"type": "string"}})})
    assert rules(old, new) == {"type-changed"}


def test_cyclic_ref_terminates():
    cyc = {"Node": obj({"next": {"$ref": "#/components/schemas/Node"}, "v": {"type": "string"}})}
    old = refspec(cyc, "#/components/schemas/Node")
    new = copy.deepcopy(old)
    new["components"]["schemas"]["Node"]["properties"]["v"]["type"] = "integer"
    changes = diff_specs(old, new)
    assert [c.rule for c in changes] == ["type-changed"]


def test_mutual_cycle_resolve():
    doc = {"components": {"schemas": {
        "A": {"properties": {"b": {"$ref": "#/components/schemas/B"}}},
        "B": {"properties": {"a": {"$ref": "#/components/schemas/A"}}}}}}
    out = resolve_refs({"$ref": "#/components/schemas/A"}, doc)
    assert out == {"properties": {"b": {"properties": {"a": {"x-circular-ref": "#/components/schemas/A"}}}}}


def test_external_and_dangling_refs_left_alone():
    out = resolve_refs({"a": {"$ref": "other.yaml#/X"}, "b": {"$ref": "#/nope"}}, {})
    assert out["a"]["x-unresolved"] and out["b"]["x-unresolved"]


def test_ref_parameters_and_request_body():
    comps = {"parameters": {"Lim": q("limit", True)},
             "requestBodies": {"B": body(obj({"n": {"type": "string"}}))}}
    old = spec({"/a": {"post": op(parameters=[{"$ref": "#/components/parameters/Lim"}],
                                  requestBody={"$ref": "#/components/requestBodies/B"})}}, components=comps)
    new = copy.deepcopy(old)
    new["components"]["parameters"]["Lim"]["required"] = False
    new["components"]["requestBodies"]["B"]["content"]["application/json"]["schema"]["properties"]["n"]["type"] = "integer"
    assert rules(old, new) == {"param-became-optional", "type-changed"}


def test_allof_merged():
    old = refspec({"A": {"allOf": [obj({"a": {"type": "string"}}), obj({"b": {"type": "string"}})]}})
    new = refspec({"A": {"allOf": [obj({"a": {"type": "string"}}), obj({})]}})
    assert rules(old, new) == {"response-field-removed"}


# ---- input and output --------------------------------------------------

YAML_SPEC = "openapi: 3.0.0\ninfo: {title: t, version: '1'}\npaths:\n  /a:\n    get:\n      responses:\n        '200': {description: ok}\n"


def test_parse_yaml_and_json_equivalent():
    as_json = json.dumps(parse_spec(YAML_SPEC))
    assert parse_spec(as_json) == parse_spec(YAML_SPEC)
    assert parse_spec(as_json, "x.json") == parse_spec(YAML_SPEC, "x.yaml")


@pytest.mark.parametrize("text", ["{not json", "a: [1", "- 1\n- 2", "swagger: '2.0'", "openapi: 2.0"])
def test_parse_errors(text):
    with pytest.raises(ApiDiffError):
        parse_spec(text)


def test_markdown_output_grouped():
    old = spec({"/a": {"get": op(summary="x")}, "/b": {"get": op()}})
    new = spec({"/a": {"get": op(summary="y"), "post": op()}})
    md = to_markdown(diff_specs(old, new))
    assert "**1 breaking**, 1 non-breaking, 1 info (3 changes)" in md
    assert md.index("## Breaking") < md.index("## Non-breaking") < md.index("## Info")
    assert "`path-removed` /b" in md
    assert to_markdown([]).count("No differences found.") == 1


def test_json_output_shape():
    old = spec({"/a": {"get": op(parameters=[q("x")])}})
    new = spec({"/a": {"get": op()}})
    data = json.loads(to_json(diff_specs(old, new)))
    assert data["summary"] == {"breaking": 1, "non-breaking": 0, "info": 0, "total": 1}
    ch = data["changes"][0]
    assert set(ch) >= {"rule", "severity", "path", "method", "pointer", "message", "old", "new"}
    assert (ch["path"], ch["method"], ch["old"], ch["new"]) == ("/a", "GET", "x", None)

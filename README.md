# api-diff

Compare two OpenAPI 3.x specs and classify **every change** as **breaking**, non-breaking or info, with a rule id, a location, and the old and new values.

```
$ python -m api_diff old.yaml new.yaml --fail-on-breaking
# API diff

**16 breaking**, 11 non-breaking, 2 info (29 changes)
```

Why another diff tool:

- **Answers one question: will this break my clients?** Removed paths, operations, parameters and response fields; optional things that became required; type changes; removed enum values; changed auth; removed success responses.
- **Understands `$ref`.** Local `#/components/...` refs are resolved recursively (nested, and cyclic ones are cut safely), so a change inside a shared schema is reported at every endpoint that uses it.
- **Flags probable renames.** A removed parameter plus an added one at the same location and type is reported as remove + add, both marked as a probable rename.
- **CI friendly.** Markdown or JSON output, and `--fail-on-breaking` for a non-zero exit.
- **Tiny.** One dependency (PyYAML), Python 3.10+.

## Install

```
pip install .            # from a clone; provides the `api-diff` command
```

## Usage

```
python -m api_diff old.yaml new.yaml [--format md|json] [-o FILE] [--fail-on-breaking]
```

- Input is YAML or JSON, picked by file extension, else by content.
- Exit code `0` normally; `1` with `--fail-on-breaking` when there are breaking changes; `2` on usage or parse errors.

JSON output:

```json
{
  "summary": {"breaking": 1, "non-breaking": 0, "info": 0, "total": 1},
  "changes": [
    {"rule": "param-removed", "severity": "breaking", "path": "/pets", "method": "GET",
     "pointer": "/parameters/query/limit", "message": "query parameter 'limit' removed",
     "old": "limit", "new": null}
  ]
}
```

## Rules

| Severity | Rules |
|---|---|
| breaking | `path-removed`, `operation-removed`, `param-removed`, `param-became-required`, `param-required-added`, `request-field-became-required`, `request-field-required-added`, `request-body-became-required`, `request-body-required-added`, `response-field-removed`, `type-changed`, `enum-values-removed`, `enum-added` (request), `request-media-type-removed`, `response-media-type-removed`, `success-response-removed`, `auth-added`, `auth-changed`, `security-scheme-removed`, `security-scheme-type-changed`, `security-scheme-changed` |
| non-breaking | `operation-added`, `param-added`, `param-became-optional`, `request-field-added`, `response-field-added`, `enum-values-added`, `response-added`, `media-type-added`, `auth-removed`, `auth-relaxed`, `security-scheme-added`, `request-body-added` |
| info | `summary-changed`, `description-changed`, `deprecated-added`, `deprecated-removed`, `request-field-removed`, `response-removed`, `request-body-removed`, `composition-changed` |

## GitHub Actions

Fail a pull request when the spec has breaking changes against the base branch:

```yaml
name: api-diff
on:
  pull_request:
    paths: ["openapi.yaml"]

jobs:
  api-diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install PyYAML && pip install git+https://github.com/YOUR-USER/api-diff
      - name: Diff against base branch
        run: |
          git show origin/${{ github.base_ref }}:openapi.yaml > /tmp/base.yaml
          api-diff /tmp/base.yaml openapi.yaml --fail-on-breaking | tee -a "$GITHUB_STEP_SUMMARY"
```

(Replace the install URL with wherever you host the package.)

## Measured result

Output of the bundled example pair (`examples/petstore-v1.yaml` to `examples/petstore-v2.yaml`); this is the start of the real report, the full one has 29 changes:

```
$ python -m api_diff examples/petstore-v1.yaml examples/petstore-v2.yaml
# API diff

**16 breaking**, 11 non-breaking, 2 info (29 changes)

## Breaking changes (16)

- `param-removed` GET /pets `/parameters/query/owner_id`: query parameter 'owner_id' removed (probable rename to 'ownerId') (old: "owner_id", new: null)
- `param-became-required` GET /pets `/parameters/query/limit/required`: query parameter 'limit' became required (old: false, new: true)
- `enum-values-removed` GET /pets `/parameters/query/status/schema/enum`: enum values removed: ['pending'] (old: ["available", "pending", "sold"], new: ["available", "sold", "archived"])
- `response-field-removed` GET /pets `/responses/200/content/application~1json/schema/items/properties/tag`: response field 'tag' removed (old: {"type": "string"}, new: null)
- `request-field-required-added` POST /pets `/requestBody/content/application~1json/schema/properties/species`: new required request field 'species' (old: null, new: {"type": "string"})
- `success-response-removed` POST /pets `/responses/200`: success response 200 removed (old: "200", new: null)
- `type-changed` GET /pets/{petId} `/parameters/path/petId/schema/type`: type changed from integer to string (old: "integer", new: "string")
- `path-removed` /owners: path /owners removed (old: "/owners", new: null)
- `auth-changed` (global) `/security`: global: security requirements changed (old: [["apiKey"]], new: [["oauth[pets.read]"]])
- `security-scheme-removed` (global) `/components/securitySchemes/apiKey`: security scheme 'apiKey' removed (old: "apiKey", new: null)
...
```

(Lines elided with `...` and a few repeated `$ref` findings omitted for length.)

```
$ python -m pytest -q
50 passed
```

## Limitations

- **OpenAPI 3.x only.** Swagger 2.0 is rejected with exit code 2.
- **Only local `$ref`s** (`#/...`). References to other files or URLs are left unresolved and not compared. Circular refs are cut at the point they repeat.
- Only `paths` (and security schemes) are compared. Webhooks, callbacks, links, examples, headers and servers are ignored.
- `oneOf` / `anyOf` changes are reported as one `info` item, not analysed in depth. `allOf` is merged for properties and `required`.
- Path templates are matched literally: renaming `/users/{id}` to `/users/{userId}` is reported as a removed path plus an added operation.
- A removed request field is `info` (servers usually ignore extra input); a response field becoming optional is not reported.
- Semantic rules are heuristics, not a proof of compatibility. Review the report; it is not a replacement for contract tests.

## Author

Marvin Palencia, founder of [DeMark Studio](https://demarkstudio.ca), Miramichi, New Brunswick, Canada. Portfolio: [marvin.demarkstudio.ca](https://marvin.demarkstudio.ca)

MIT License.

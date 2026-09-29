"""api-diff: classify changes between two OpenAPI 3.x specs."""
from .diff import Change, diff_specs
from .loader import ApiDiffError, load_spec, parse_spec, resolve_refs
from .report import to_json, to_markdown

__all__ = ["Change", "ApiDiffError", "diff_specs", "load_spec", "parse_spec",
           "resolve_refs", "to_json", "to_markdown"]
__version__ = "0.1.0"

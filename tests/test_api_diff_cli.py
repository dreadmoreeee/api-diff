import json
import os
import subprocess
import sys
from pathlib import Path

from api_diff.cli import main

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
V1, V2 = str(EX / "petstore-v1.yaml"), str(EX / "petstore-v2.yaml")

OLD = {"openapi": "3.0.0", "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}}}
NEW = {"openapi": "3.0.0", "paths": {}}


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_exit_0_by_default_even_with_breaking(tmp_path, capsys):
    assert main([write(tmp_path, "o.json", OLD), write(tmp_path, "n.json", NEW)]) == 0
    assert "path-removed" in capsys.readouterr().out


def test_fail_on_breaking_exit_1(tmp_path, capsys):
    o, n = write(tmp_path, "o.json", OLD), write(tmp_path, "n.json", NEW)
    assert main([o, n, "--fail-on-breaking"]) == 1
    assert main([o, o, "--fail-on-breaking"]) == 0


def test_json_format_and_output_file(tmp_path):
    out = tmp_path / "r.json"
    rc = main([write(tmp_path, "o.json", OLD), write(tmp_path, "n.json", NEW),
               "--format", "json", "-o", str(out)])
    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["breaking"] == 1


def test_missing_file_exit_2(tmp_path, capsys):
    assert main([str(tmp_path / "nope.yaml"), V1]) == 2
    assert "error" in capsys.readouterr().err


def test_bad_spec_exit_2(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("swagger: '2.0'\n", encoding="utf-8")
    assert main([str(bad), V1]) == 2


def test_usage_error_exit_2():
    assert main([]) == 2
    assert main([V1, V2, "--format", "xml"]) == 2


def test_examples_expected_findings(capsys):
    assert main([V1, V2, "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    rules = {c["rule"] for c in data["changes"]}
    assert data["summary"]["breaking"] >= 8
    assert {"param-became-required", "request-field-required-added", "type-changed",
            "enum-values-removed", "auth-changed", "success-response-removed",
            "path-removed", "response-field-removed", "security-scheme-removed"} <= rules
    assert any(c["probable_rename"] for c in data["changes"])


def run_module(*args):
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-m", "api_diff", *args], capture_output=True,
                          text=True, encoding="utf-8", env=env, cwd=str(ROOT))


def test_subprocess_exit_codes():
    assert run_module(V1, V2).returncode == 0
    r = run_module(V1, V2, "--fail-on-breaking")
    assert r.returncode == 1 and "# API diff" in r.stdout
    assert run_module("missing.yaml", V2).returncode == 2

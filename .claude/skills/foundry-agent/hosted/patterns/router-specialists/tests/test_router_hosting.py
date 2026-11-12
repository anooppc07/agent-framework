"""Task 3 hosting tests (plan cases 66-70 for the router): the entrypoint uses the standard native
workflow host with no custom protocol, session or checkpoint code. The router is hosted with
workflow= (the supported native shape), not the deprecated agent=workflow.as_agent() wrapper.
These are local construction checks; they do not prove hosted persistence or isolation.
Run with the template requirements installed, PYTHONPATH unset:  python -m pytest tests -q
"""

import ast
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
ASSETS = HERE.parent.parent / "assets"
sys.path.insert(0, str(HERE))  # the router's agent.py; never put assets/ on sys.path (it has its own agent.py)


def _load_preflight():
    """Import the shared preflight by file path so assets/agent.py cannot shadow the router's agent.py."""
    spec = importlib.util.spec_from_file_location("preflight_shared", ASSETS / "preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _main_text():
    path = HERE / "main.py"
    assert path.exists(), "router main.py is missing"
    return path.read_text(encoding="utf-8")


def _clean_env():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update({"FOUNDRY_PROJECT_ENDPOINT": "https://example.invalid", "OTEL_SDK_DISABLED": "true"})
    return env


def test_preflight_accepts_the_router_entrypoint_as_enabled():
    status, reason = _load_preflight().check_session_manager(_main_text())
    assert status == "enabled", reason


def test_router_entrypoint_hosts_the_workflow_natively_not_through_as_agent():
    text = _main_text()
    assert "workflow=" in text and "parse_response=" in text
    assert "as_agent" not in text and "WorkflowAgent" not in text


def test_router_entrypoint_has_no_custom_protocol_session_or_checkpoint_code():
    text = _main_text().lower()
    for marker in ("previous_response_id", "text/event-stream", "streamingresponse", "starlette", "fastapi", "flask",
                   "session_store", "checkpoint_store", "sqlite3", "pickle", "redis", "cosmos", "memorysaver",
                   "langgraph", "langchain"):
        assert marker not in text, f"main.py contains {marker!r}"


def test_router_entrypoint_builds_a_fresh_workflow_for_every_request():
    tree = ast.parse(_main_text())
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "make_workflow"]
    assert len(fns) == 1, "main.py must define exactly one make_workflow(request) factory"
    fn = fns[0]
    assert [a.arg for a in fn.args.args] == ["request"], "factory must take the request and build per call"
    assert any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "build_workflow" for n in ast.walk(fn))


def test_host_builds_without_credentials_or_network(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    sys.modules.pop("main", None)
    assert (HERE / "main.py").exists(), "router main.py is missing"
    spec = importlib.util.spec_from_file_location("router_main", HERE / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    server = mod.build_server()
    assert type(server).__name__ == "ResponsesHostServer"


def test_entrypoint_imports_outside_the_source_tree(tmp_path):
    for name in ("agent.py", "main.py", "specialists.json"):
        src = HERE / name
        assert src.exists(), f"{name} is missing from the router template"
        shutil.copy(src, tmp_path / name)
    r = subprocess.run([sys.executable, "-c", "import main; print(callable(main.build_server))"],
                       cwd=tmp_path, env=_clean_env(), capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().endswith("True"), r.stderr[-1500:]


def test_specialists_config_is_plain_data_with_no_secrets_or_endpoints():
    import json
    assert (HERE / "specialists.json").exists(), "specialists.json is missing from the router template"
    data = json.loads((HERE / "specialists.json").read_text(encoding="utf-8"))
    assert isinstance(data["router_instructions"], str)
    assert data["specialists"] and all(isinstance(v, str) and v.strip() for v in data["specialists"].values())
    blob = json.dumps(data).lower()
    for marker in ("http://", "https://", "key", "secret", "subscription"):
        assert marker not in blob


def test_router_requirements_are_maf_only_and_match_the_single_agent_pin():
    assert (HERE / "requirements.txt").exists(), "router requirements.txt is missing"
    router = (HERE / "requirements.txt").read_text(encoding="utf-8")
    single = (ASSETS / "requirements.txt").read_text(encoding="utf-8")
    assert router == single, "router and single-agent templates must pin the same tested dependency set"
    assert "langgraph" not in router.lower() and "langchain" not in router.lower()

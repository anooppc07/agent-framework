"""Offline tests for the MAF hosted baseline (plan Task 0, cases 65-70).

No Azure credentials. The only fake is a hand-written model client at the external
boundary; agent, runtime facade, host construction and preflight are the real code.
Run with an interpreter that has requirements.txt installed (PYTHONPATH unset):
    python -m pytest hosted/tests -v
These prove local wiring only. They do NOT prove hosted session persistence, restart
survival or tenant isolation; that needs an authorized Phase 2 run.
"""

import asyncio
import ast
import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parent.parent / "assets"
sys.path.insert(0, str(ASSETS))

SHIPPED = ["agent.py", "main.py", "requirements.txt", "instructions.md", "azure.yaml"]


def _text(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _clean_env() -> dict:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _fake_client(reply=None, error=None):
    """Hand-written model client: the external boundary. Records every call."""
    from agent_framework import BaseChatClient, ChatResponse, Message

    class FakeClient(BaseChatClient):
        def __init__(self):
            super().__init__()
            self.calls: list[list[tuple[str, str]]] = []

        def _inner_get_response(self, *, messages, stream, options, **kwargs):
            async def go():
                seen = [(m.role, m.text or "") for m in messages]
                self.calls.append(seen)
                await asyncio.sleep(0)  # let concurrent turns interleave
                if error is not None:
                    raise error
                users = [t for r, t in seen if r == "user"]
                text = reply(seen) if reply else "saw:" + "|".join(users)
                return ChatResponse(messages=Message("assistant", [text]))

            return go()

    return FakeClient()


def _import(name: str):
    """Import a shipped module; an ImportError means the template still has foreign deps."""
    try:
        mod = importlib.import_module(name)
        return importlib.reload(mod)
    except ImportError as exc:
        pytest.fail(f"{name}.py does not import with the MAF-only dependency set: {exc}")


def _agent(client):
    agent_mod = _import("agent")
    assert hasattr(agent_mod, "build_agent") and hasattr(agent_mod, "run_turn"),         "agent.py must expose build_agent(client=...) and async run_turn(...)"
    return agent_mod, agent_mod.build_agent(client=client)


# --- case 1: MAF, no LangGraph --------------------------------------------------------------


def test_agent_uses_maf_and_no_langgraph_anywhere():
    imports = {
        n.module.split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(ast.parse(_text("agent.py")))
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [None])
    }
    assert "agent_framework" in imports, f"agent.py does not use MAF: {sorted(imports)}"
    for name in SHIPPED:
        assert not re.search(r"lang(graph|chain)", _text(name), re.I), f"{name} still mentions LangGraph/LangChain"
    assert "agent-framework-foundry-hosting" in _text("requirements.txt")


# --- case 3: pinned set resolves ------------------------------------------------------------


def test_requirements_resolve_on_this_interpreter(tmp_path):
    reqs = _text("requirements.txt")
    for pin in ("agent-framework-foundry-hosting", "agent-framework-foundry"):
        assert re.search(rf"^{pin}[=<>~]", reqs, re.M), f"{pin} must be pinned in requirements.txt"
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--dry-run", "--pre", "-q", "-r", str(ASSETS / "requirements.txt")],
        cwd=tmp_path, env=_clean_env(), capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-1500:]


# --- case 4: standard host adapter only, no custom protocol layer ---------------------------

PROTOCOL_MARKERS = ["previous_response_id", "text/event-stream", "StreamingResponse", "EventSourceResponse",
                    "fastapi", "flask", "starlette", "aiohttp", "@app."]


def test_main_uses_standard_host_adapter_without_protocol_code():
    main = _text("main.py")
    assert re.search(r"from agent_framework_foundry_hosting import .*ResponsesHostServer", main)
    for marker in PROTOCOL_MARKERS:
        for name in ("agent.py", "main.py"):
            assert marker not in _text(name), f"{name} contains custom protocol marker {marker!r}"


# --- cases 5-8: session-manager preflight ---------------------------------------------------


def _preflight():
    import preflight

    return preflight


def test_preflight_ok_for_shipped_main():
    status, reason = _preflight().check_session_manager(_text("main.py"))
    assert status == "enabled", reason


def test_preflight_fails_when_custom_session_store_overrides_default():
    src = "from agent_framework_foundry_hosting import ResponsesHostServer\n" \
          "ResponsesHostServer(agent=a, agent_session_store_provider=MyStore())\n"
    status, reason = _preflight().check_session_manager(src)
    assert status == "disabled" and "agent_session_store_provider" in reason


@pytest.mark.parametrize("src", [
    "print('no host here')\n",
    "ResponsesHostServer(agent=a, **opts)\n",
    "def broken(:\n",
])
def test_preflight_unresolved_configuration_fails_closed_as_unknown(src):
    status, reason = _preflight().check_session_manager(src)
    assert status == "unknown" and reason


@pytest.mark.parametrize("src", ["", "   \n\t", None])
def test_preflight_empty_or_null_is_unknown_not_an_exception(src):
    status, reason = _preflight().check_session_manager(src)
    assert status == "unknown" and reason


def test_preflight_cli_exit_codes(tmp_path):
    def run(path):
        return subprocess.run([sys.executable, str(ASSETS / "preflight.py"), str(path)],
                              cwd=tmp_path, env=_clean_env(), capture_output=True, text=True)

    assert run(ASSETS / "main.py").returncode == 0
    bad = tmp_path / "main.py"
    bad.write_text("ResponsesHostServer(agent=a, agent_session_store_provider=S())\n", encoding="utf-8")
    assert run(bad).returncode == 2
    assert run(tmp_path / "missing.py").returncode == 2


# --- case 9: no parallel session/state implementation ---------------------------------------

STATE_MARKERS = ["checkpointer", "MemorySaver", "SqliteSaver", "session_store", "SessionStore", "sqlite3",
                 "shelve", "pickle", "redis", "cosmos", "InMemoryHistoryProvider", "history_provider"]


def test_no_custom_session_store_checkpointer_or_history_replay():
    assert "from agent_framework_foundry_hosting import" in _text("main.py"),         "scan is meaningless without the standard MAF host"
    for name in ("agent.py", "main.py"):
        text = _text(name)
        for marker in STATE_MARKERS:
            assert marker.lower() not in text.lower(), f"{name} contains {marker!r}"


# --- cases 10-14: runtime facade through the model boundary ---------------------------------


def test_follow_up_uses_supplied_prior_turns():
    def reply(seen):
        earlier = " ".join(t for _, t in seen[:-1])
        return "Ada" if "Ada" in earlier else "I do not know"

    mod, agent = _agent(_fake_client(reply=reply))
    history = [("user", "My name is Ada"), ("assistant", "Nice to meet you, Ada.")]
    result = asyncio.run(mod.run_turn(agent, "What is my name?", history))
    assert result.answer == "Ada" and result.outcome == "answered"
    alone = asyncio.run(mod.run_turn(agent, "What is my name?"))
    assert alone.answer == "I do not know"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_input_refuses_without_a_model_call(blank):
    client = _fake_client()
    mod, agent = _agent(client)
    result = asyncio.run(mod.run_turn(agent, blank))
    assert result.outcome == "refused" and result.answer == mod.REFUSAL
    assert client.calls == []


def test_concurrent_turns_do_not_leak_state():
    client = _fake_client()
    mod, agent = _agent(client)

    async def both():
        return await asyncio.gather(
            mod.run_turn(agent, "second-A", [("user", "first-A")]),
            mod.run_turn(agent, "second-B", [("user", "first-B")]),
        )

    a, b = asyncio.run(both())
    assert a.answer == "saw:first-A|second-A"
    assert b.answer == "saw:first-B|second-B"


def test_model_failure_propagates_without_fabricated_answer():
    mod, agent = _agent(_fake_client(error=RuntimeError("model down")))
    with pytest.raises(RuntimeError, match="model down"):
        asyncio.run(mod.run_turn(agent, "hello"))


def test_non_ascii_input_and_history_survive_unchanged():
    client = _fake_client()
    mod, agent = _agent(client)
    result = asyncio.run(mod.run_turn(agent, "你好 🙂 ñandú", [("user", "héllo wörld")]))
    assert result.answer == "saw:héllo wörld|你好 🙂 ñandú"


# --- case 15: host constructs without credentials; entrypoint imports outside the tree ------


def test_host_builds_without_credentials_or_network():
    _, agent = _agent(_fake_client())
    main_mod = _import("main")
    server = main_mod.build_server(agent)
    assert type(server).__name__ == "ResponsesHostServer"


def test_entrypoint_imports_outside_source_tree(tmp_path):
    for name in ("agent.py", "main.py", "instructions.md"):
        shutil.copy(ASSETS / name, tmp_path / name)
    r = subprocess.run([sys.executable, "-c", "import main; print(callable(main.build_server))"],
                       cwd=tmp_path, env=_clean_env(), capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "True", r.stderr[-1500:]


# --- extra (ledgered ruling): protocol version matches the pinned hosting package ----------


def test_azure_yaml_declares_responses_protocol_2_and_python_313():
    text = _text("azure.yaml")
    assert re.search(r"protocol: responses\s+version: 2\.0\.0", text), "protocol must be responses 2.0.0"
    assert "python_3_13" in text

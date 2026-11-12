"""Review-driven fix for Task 0 (finding 4): preflight must not report 'enabled' for shapes it
cannot prove. It accepts exactly the shipped entrypoint shape and returns 'unknown' otherwise.
It reads source only; effective hosted enablement stays unverified until Phase 2.
Run with an interpreter that has the template requirements installed:  PYTHONPATH= python -m pytest tests -q
"""

import subprocess
import sys
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parent.parent / "assets"
sys.path.insert(0, str(ASSETS))

import preflight  # noqa: E402

IMPORT = "from agent_framework_foundry_hosting import ResponsesHostServer\nfrom agent import build_agent\n"
MAIN = 'if __name__ == "__main__":\n    build_server(build_agent()).run()\n'


def shaped(body, imports=IMPORT, main=MAIN):
    return imports + "\n\ndef build_server(agent):\n" + body + "\n\n" + main


GOOD_BODY = '    return ResponsesHostServer(agent=agent, history_source="agent_server")'


def status(src):
    return preflight.check_session_manager(src)[0]


# --- case 19 (guard): the shipped shape still passes ------------------------------------------


def test_shipped_main_is_enabled():
    assert status((ASSETS / "main.py").read_text(encoding="utf-8")) == "enabled"
    assert status(shaped(GOOD_BODY)) == "enabled"


def test_cli_exit_codes_unchanged(tmp_path):
    def run(p):
        return subprocess.run([sys.executable, str(ASSETS / "preflight.py"), str(p)], cwd=tmp_path,
                              capture_output=True, text=True).returncode

    assert run(ASSETS / "main.py") == 0
    bad = tmp_path / "main.py"
    bad.write_text("x = 1\n", encoding="utf-8")
    assert run(bad) == 2 and run(tmp_path / "missing.py") == 2


# --- cases 13-16: shapes that merely look like the SDK host -----------------------------------


def test_rebound_host_name_is_unknown():
    src = IMPORT + "from fake import Other as ResponsesHostServer\n\ndef build_server(agent):\n" + GOOD_BODY + "\n\n" + MAIN
    assert status(src) == "unknown"
    assert status(shaped(GOOD_BODY, imports=IMPORT + "ResponsesHostServer = object\n")) == "unknown"


def test_host_built_only_in_a_function_the_entrypoint_never_reaches_is_unknown():
    src = IMPORT + "\ndef unused(agent):\n" + GOOD_BODY + "\n\ndef build_server(agent):\n    return None\n\n" + MAIN
    assert status(src) == "unknown"
    no_main = IMPORT + "\ndef build_server(agent):\n" + GOOD_BODY + "\n"
    assert status(no_main) == "unknown"


def test_host_from_another_module_is_unknown():
    src = "import other\n" + IMPORT + "\ndef build_server(agent):\n    return other.ResponsesHostServer(agent=agent)\n\n" + MAIN
    assert status(src) == "unknown"


def test_aliased_or_star_or_module_import_is_unknown():
    aliased = IMPORT.replace("import ResponsesHostServer", "import ResponsesHostServer as Host")
    assert status(shaped(GOOD_BODY, imports=aliased)) == "unknown"
    assert status(shaped(GOOD_BODY, imports="from agent_framework_foundry_hosting import *\n")) == "unknown"
    assert status(shaped("    return agent_framework_foundry_hosting.ResponsesHostServer(agent=agent)",
                         imports="import agent_framework_foundry_hosting\n")) == "unknown"


# --- case 17: host count; override still reported as disabled ---------------------------------


def test_more_than_one_host_construction_is_unknown():
    two = GOOD_BODY + "\n    ResponsesHostServer(agent=agent)"
    assert status(shaped(two)) == "unknown"


def test_storage_override_is_still_disabled():
    body = "    return ResponsesHostServer(agent=agent, agent_session_store_provider=Store())"
    st, reason = preflight.check_session_manager(shaped(body))
    assert st == "disabled" and "agent_session_store_provider" in reason
    # a None override is the documented default, not a bypass
    none_body = "    return ResponsesHostServer(agent=agent, agent_session_store_provider=None)"
    assert status(shaped(none_body)) == "enabled"


@pytest.mark.parametrize("kw", ["checkpoint_store_provider", "function_approval_store_provider", "response_store", "store"])
def test_other_storage_overrides_are_not_accepted_as_default_shape(kw):
    body = f"    return ResponsesHostServer(agent=agent, {kw}=Store())"
    assert status(shaped(body)) in ("disabled", "unknown")  # never 'enabled'


# --- case 18: positional arguments and argument shape -----------------------------------------


def test_extra_positional_arguments_are_unknown():
    assert status(shaped("    return ResponsesHostServer(agent, Store())")) == "unknown"
    assert status(shaped("    return ResponsesHostServer(agent)")) == "unknown"  # agent must be the keyword


def test_star_args_and_kwargs_are_unknown():
    assert status(shaped("    return ResponsesHostServer(*args)")) == "unknown"
    assert status(shaped("    return ResponsesHostServer(agent=agent, **opts)")) == "unknown"


def test_unexpected_keywords_are_unknown():
    assert status(shaped("    return ResponsesHostServer(agent=agent, surprise=1)")) == "unknown"


def test_entrypoint_must_call_build_server():
    other_main = 'if __name__ == "__main__":\n    something_else()\n'
    assert status(shaped(GOOD_BODY, main=other_main)) == "unknown"
    assert status(shaped(GOOD_BODY, main="")) == "unknown"


# --- unchanged fail-closed behaviour (empty, null, unparsable) --------------------------------


@pytest.mark.parametrize("src", ["", "  \n", None, "def broken(:\n", 5])
def test_empty_null_and_unparsable_stay_unknown(src):
    assert status(src) == "unknown"


# --- router: natively hosted workflow shape (user chose native workflow= over the deprecated agent= wrapper) ---

WF_IMPORT = ("from agent_framework_foundry_hosting import ResponsesHostServer, WorkflowTurn\n"
             "from agent import build_workflow, run_turn\n")
WF_MAIN = 'if __name__ == "__main__":\n    build_server().run()\n'


def wf_shaped(call, imports=WF_IMPORT, main=WF_MAIN):
    return imports + "\n\ndef build_server():\n    return " + call + "\n\n" + main


WF_GOOD = "ResponsesHostServer(workflow=make_workflow, parse_response=parse_response)"


def test_native_workflow_shape_is_enabled():
    st, reason = preflight.check_session_manager(wf_shaped(WF_GOOD))
    assert st == "enabled", reason


def test_native_workflow_with_a_storage_override_is_disabled():
    for kw in ("agent_session_store_provider=S()", "checkpoint_store_provider=S()"):
        call = f"ResponsesHostServer(workflow=make_workflow, parse_response=parse_response, {kw})"
        assert status(wf_shaped(call)) == "disabled", kw


def test_native_workflow_requires_workflow_and_parse_response_keywords():
    assert status(wf_shaped("ResponsesHostServer(workflow=make_workflow)")) == "unknown"
    assert status(wf_shaped("ResponsesHostServer(parse_response=parse_response)")) == "unknown"


def test_native_workflow_rejects_mixing_agent_and_workflow():
    call = "ResponsesHostServer(agent=a, workflow=make_workflow, parse_response=parse_response)"
    assert status(wf_shaped(call)) == "unknown"


def test_native_workflow_rejects_positional_star_and_unexpected_keywords():
    for call in ("ResponsesHostServer(make_workflow, parse_response=parse_response)",
                 "ResponsesHostServer(workflow=make_workflow, parse_response=p, **opts)",
                 "ResponsesHostServer(workflow=make_workflow, parse_response=p, surprise=1)"):
        assert status(wf_shaped(call)) == "unknown", call


def test_native_workflow_still_needs_the_entrypoint_to_reach_the_factory():
    assert status(wf_shaped(WF_GOOD, main="")) == "unknown"
    assert status(wf_shaped(WF_GOOD, main='if __name__ == "__main__":\n    other()\n')) == "unknown"


def test_native_workflow_rebound_or_aliased_host_is_unknown():
    assert status(wf_shaped(WF_GOOD, imports=WF_IMPORT + "from fake import X as ResponsesHostServer\n")) == "unknown"
    aliased = WF_IMPORT.replace("ResponsesHostServer, WorkflowTurn", "ResponsesHostServer as H, WorkflowTurn")
    assert status(wf_shaped(WF_GOOD, imports=aliased)) == "unknown"


def test_each_shape_keeps_its_own_required_keywords():
    assert status(shaped(GOOD_BODY)) == "enabled"
    # the agent shape may not borrow the workflow-only parser, and the workflow shape may not borrow history_source
    assert status(shaped("    return ResponsesHostServer(agent=agent, parse_response=parse_response)")) == "unknown"
    mixed = "ResponsesHostServer(workflow=make_workflow, parse_response=parse_response, history_source='agent_server')"
    assert status(wf_shaped(mixed)) == "unknown"

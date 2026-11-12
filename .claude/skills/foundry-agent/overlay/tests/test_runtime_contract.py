"""Task 2 tests (plan cases 12-21, 61, 63): runtime contract and responsibility boundaries.

Real agent_spec and runtime_contract modules; nothing is mocked. Run from overlay/:
    PYTHONPATH= python -m pytest tests -q
Local tests and platform documentation never establish deployed verification.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_spec as S  # noqa: E402
import runtime_contract as C  # noqa: E402

ORDER = ["identity-propagation", "approved-tool-access", "context-memory-isolation",
         "bounded-reasoning-retries", "handoff-controls", "safe-termination", "traceability", "audit-evidence"]
SINGLE = dict(zip(ORDER, ["declared", "declared", "declared", "not-implemented", "not-implemented",
                          "not-implemented", "declared", "not-implemented"]))
ROUTER = dict(SINGLE, **{"context-memory-isolation": "not-implemented"})
DESIGN = {c: "not-implemented" for c in ORDER}


def spec(**over):
    d = {
        "name": "card-helper", "description": "Dispute intake", "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf",
        "pattern": "single-agent", "pattern_rationale": "one agent is enough", "model": "gpt-5.4",
        "hosted_session_manager": "enabled", "tools": [], "evals_enabled": False, "pipeline_enabled": False,
    }
    d.update(over)
    return S.parse_spec(d)


def router_spec(**over):
    d = dict(name="card-router", pattern="router-specialists", specialists=[
        {"name": "billing", "instructions": "x", "examples": ["q1"]},
        {"name": "fraud", "instructions": "y", "examples": ["q2"]},
    ])
    d.update(over)
    return spec(**d)


def impl(contract):
    return {c.control_id: c.implementation for c in contract.controls}


def deployed(**over):
    d = dict(description="hosted multi-turn run", scope="deployed", environment="dev",
             runtime_version="agent v3", test="session-continuity", run="run-42")
    d.update(over)
    return C.Evidence(**d)


# --- case 1: the eight controls in pinned order -----------------------------------------------


@pytest.mark.parametrize("make,mode", [
    (spec, "overlay-existing"), (router_spec, "create-new"), (spec, "design-only"),
])
def test_exactly_eight_controls_in_pinned_order(make, mode):
    ids = [c.control_id for c in C.build_contract(make(), mode).controls]
    assert ids == ORDER and tuple(ORDER) == C.CONTROL_IDS


# --- case 3: implementation table per column --------------------------------------------------


def test_single_agent_column_matches_the_plan_table():
    assert impl(C.build_contract(spec(), "overlay-existing")) == SINGLE


def test_router_column_matches_the_plan_table():
    assert impl(C.build_contract(router_spec(), "create-new")) == ROUTER


@pytest.mark.parametrize("make", [spec, router_spec])
def test_design_only_mode_claims_no_implementation(make):
    c = C.build_contract(make(), "design-only")
    assert impl(c) == DESIGN and all(x.verification == "not-run" for x in c.controls)


@pytest.mark.parametrize("pattern", ["supervisor-worker", "agent-handoffs"])
def test_design_only_patterns_use_the_design_column(pattern):
    assert impl(C.build_contract(spec(pattern=pattern), "design-only")) == DESIGN


# --- case 2: starting verification ------------------------------------------------------------


@pytest.mark.parametrize("make,mode", [(spec, "overlay-existing"), (router_spec, "create-new")])
def test_applicable_rows_start_not_run_and_handoff_is_not_applicable_with_reason(make, mode):
    controls = C.build_contract(make(), mode).controls
    assert len(controls) == len(ORDER), "no controls to check"
    for c in controls:
        if c.control_id == "handoff-controls":
            assert c.verification == "not-applicable"
            assert "topology" in c.not_applicable_reason and c.not_applicable_reason.strip()
        else:
            assert c.verification == "not-run" and c.not_applicable_reason == ""


# --- case 4 and 12: deployed "passed" needs scoped provenance; local evidence cannot promote ---


def control(**over):
    d = dict(control_id="traceability", implementation="declared")
    d.update(over)
    return C.ControlStatus(**d)


def test_deployed_passed_with_full_provenance_is_accepted():
    c = control(verification="passed", responsibility="platform", deployed_evidence=deployed())
    assert c.verification == "passed" and c.deployed_evidence.environment == "dev"
    assert c.deployed_evidence.scope == "deployed" and c.responsibility == "platform"
    assert c.implementation == "declared"


@pytest.mark.parametrize("field", ["description", "environment", "runtime_version", "test", "run"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_deployed_passed_rejects_blank_evidence_or_provenance(field, blank):
    with pytest.raises(C.ContractError, match=field):
        control(verification="passed", deployed_evidence=deployed(**{field: blank}))


def test_passed_without_any_deployed_evidence_is_rejected():
    with pytest.raises(C.ContractError, match="deployed"):
        control(verification="passed")


def test_local_only_evidence_cannot_construct_passed():
    local = C.Evidence(description="pytest fake-client run", scope="local", test="test_route", run="local")
    with pytest.raises(C.ContractError, match="deployed"):
        control(verification="passed", local_evidence=(local,))
    with pytest.raises(C.ContractError, match="scope"):
        control(verification="passed", deployed_evidence=local)
    kept = control(local_evidence=(local,))
    assert kept.verification == "not-run"  # local evidence is recorded but never promotes


def test_local_evidence_must_be_local_scope():
    with pytest.raises(C.ContractError, match="scope"):
        control(local_evidence=(deployed(),))


# --- case 5: invalid values -------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("implementation", "done"), ("verification", "green"), ("responsibility", "vendor"),
    ("control_id", "made-up-control"),
])
def test_invalid_status_values_are_rejected(field, value):
    with pytest.raises(C.ContractError, match=field):
        control(**{field: value})


def test_invalid_evidence_scope_rejected():
    with pytest.raises(C.ContractError, match="scope"):
        C.Evidence(description="x", scope="prod")


def test_not_applicable_requires_a_reason():
    with pytest.raises(C.ContractError, match="reason"):
        control(verification="not-applicable")
    with pytest.raises(C.ContractError, match="reason"):
        control(verification="not-applicable", not_applicable_reason="  ")
    assert control(verification="not-applicable", not_applicable_reason="topology: no hand-off").verification


def test_reason_without_not_applicable_is_rejected():
    with pytest.raises(C.ContractError, match="reason"):
        control(not_applicable_reason="because")


# --- case 6: platform defaults do not mark broad controls implemented -------------------------


def test_platform_capabilities_are_listed_separately_and_do_not_promote_controls():
    c = C.build_contract(router_spec(evals_enabled=True), "create-new")
    caps = {x.name: x for x in c.capabilities}
    assert {"session-history", "invocation-protocol", "context-propagation", "bounded-routing",
            "refusal", "release-evidence"} <= set(caps)
    assert caps["session-history"].responsibility == "platform"
    assert caps["invocation-protocol"].responsibility == "platform"
    assert "retries" in caps["bounded-routing"].limits and "cancellation" in caps["bounded-routing"].limits
    assert "timeout" in caps["refusal"].limits
    state = impl(c)
    for broad in ("context-memory-isolation", "bounded-reasoning-retries", "safe-termination", "audit-evidence"):
        assert state[broad] == "not-implemented"
    assert not any(x.verification == "passed" for x in c.controls)


def test_single_agent_has_no_router_only_capabilities():
    names = {x.name for x in C.build_contract(spec(), "overlay-existing").capabilities}
    assert "bounded-routing" not in names and "refusal" not in names and "session-history" in names


# --- cases 7-8: tool declarations are requirements, not enforcement ---------------------------


def test_tool_identity_and_approval_declarations_are_requirements_not_enforced():
    tools = [{"name": "case-lookup", "auth": "user-token", "requires_approval": True}]
    c = C.build_contract(spec(tools=tools), "overlay-existing")
    t = c.tools[0]
    assert (t.name, t.auth, t.requires_approval, t.enforced) == ("case-lookup", "user-token", True, False)
    gaps = " ".join(next(x for x in c.controls if x.control_id == "approved-tool-access").gaps)
    assert "approval" in gaps and "enforcement" in gaps
    assert impl(c)["identity-propagation"] != "implemented" and impl(c)["approved-tool-access"] != "implemented"


def test_user_token_note_requires_real_catalog_and_auth_compatibility():
    c = C.build_contract(spec(tools=[{"name": "lookup", "auth": "user-token", "requires_approval": False}]),
                         "overlay-existing")
    note = c.tools[0].note
    assert "catalog" in note and "not assumed" in note
    other = C.build_contract(spec(tools=[{"name": "lookup", "auth": "none", "requires_approval": False}]),
                             "overlay-existing")
    assert other.tools[0].note == ""


# --- case 9: markdown and JSON preserve the same data -----------------------------------------


def test_json_and_markdown_preserve_kind_framework_mode_rationale_and_scope():
    c = C.build_contract(router_spec(evals_enabled=True), "create-new")
    data = json.loads(C.render_json(c))
    md = C.render_markdown(c)
    assert (data["agent_kind"], data["framework"], data["mode"], data["pattern"]) == \
           ("hosted", "maf", "create-new", "router-specialists")
    assert data["rationale"] == "one agent is enough"
    assert [x["control_id"] for x in data["controls"]] == ORDER
    assert {x["name"] for x in data["capabilities"]} == {x.name for x in c.capabilities}
    for needle in ("hosted", "maf", "create-new", "router-specialists", "one agent is enough", "platform"):
        assert needle in md
    assert "Evidence scope" in md and "do not establish deployed verification" in md
    assert not re.search(r"\|\s*passed\s*\|", md)
    assert all(x["verification"] != "passed" for x in data["controls"])


def test_json_keeps_raw_user_text_unescaped():
    text = "Use `x` | y\nz <b> *star* \\"
    data = json.loads(C.render_json(C.build_contract(spec(pattern_rationale=text), "overlay-existing")))
    assert data["rationale"] == text.strip()


# --- case 10: markdown escaping ---------------------------------------------------------------


def _unescaped_pipes(line):
    return len(re.findall(r"(?<!\\)\|", line))


def test_markdown_escapes_user_text_so_tables_cannot_break():
    hostile = "evil | col `code` *b*\nnew line <script> [x](y)"
    tools = [{"name": "tool | one", "auth": "none", "requires_approval": False}]
    c = C.build_contract(spec(pattern_rationale=hostile, tools=tools, name="card-helper"), "overlay-existing")
    md = C.render_markdown(c)
    table_lines = [ln for ln in md.splitlines() if ln.startswith("|")]
    blocks, current = [], []
    for ln in md.splitlines():
        if ln.startswith("|"):
            current.append(ln)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    assert table_lines and blocks
    for block in blocks:
        counts = {_unescaped_pipes(ln) for ln in block}
        assert len(counts) == 1, f"ragged table: {block}"
    assert "<script>" not in md and "\nnew line" not in md


# --- case 11: overlay and design honesty ------------------------------------------------------


def test_overlay_never_infers_maf_or_session_from_labels_or_filenames():
    s = spec(name="maf-session-agent", framework="legacy-langgraph", hosted_session_manager="unknown",
             pattern_rationale="maf foundry session manager enabled")
    c = C.build_contract(s, "overlay-existing")
    assert c.framework == "legacy-langgraph" and "not inspected" in c.framework_source
    assert c.session.required == "reported-only" and c.session.declared == "unknown"
    assert c.session.observed == "not-observed" and c.session.blocks_readiness
    assert "session-history" not in {x.name for x in c.capabilities}
    assert "invocation-protocol" not in {x.name for x in c.capabilities}


def test_design_output_never_claims_implementation_or_declaration():
    c = C.build_contract(router_spec(), "design-only")
    md = C.render_markdown(c)
    assert "Design only" in md and not c.claims_implementation
    assert all(x.implementation == "not-implemented" for x in c.controls)


# --- case 13: session record ------------------------------------------------------------------


def test_overlay_reports_declared_session_state_and_never_requires_or_infers_it():
    for declared in ("enabled", "disabled", "unknown"):
        s = C.build_contract(spec(hosted_session_manager=declared), "overlay-existing").session
        assert (s.required, s.declared, s.observed) == ("reported-only", declared, "not-observed")
        assert s.blocks_readiness


def test_new_hosted_session_requirement_is_separate_from_observed_state():
    s = C.build_contract(router_spec(), "create-new").session
    assert (s.required, s.declared, s.observed) == ("enabled", "enabled", "not-observed")
    assert s.observed_evidence is None and s.blocks_readiness
    assert s.owner.startswith("platform") and "preflight" in s.provenance and "README" in s.provenance
    assert any("authorization" in x for x in s.separate_concerns)


def test_session_observed_enabled_needs_evidence_and_disabled_or_unknown_block():
    base = C.build_contract(router_spec(), "create-new").session
    with pytest.raises(C.ContractError, match="evidence"):
        C.SessionRecord(**{**base.__dict__, "observed": "enabled", "observed_evidence": None})
    ok = C.SessionRecord(**{**base.__dict__, "observed": "enabled", "observed_evidence": deployed()})
    assert not ok.blocks_readiness
    for state in ("disabled", "unknown", "not-observed"):
        assert C.SessionRecord(**{**base.__dict__, "observed": state}).blocks_readiness
    with pytest.raises(C.ContractError, match="observed"):
        C.SessionRecord(**{**base.__dict__, "observed": "maybe"})


def test_prompt_agent_session_is_not_applicable_and_never_blocks():
    s = C.build_contract(spec(agent_kind="prompt", framework="not-applicable",
                              hosted_session_manager="not-applicable"), "overlay-existing").session
    assert s.required == "not-applicable" and not s.blocks_readiness


# --- case 14: eval/pipeline status and router coverage gaps -----------------------------------


def test_eval_and_pipeline_status_are_explicit():
    off = C.build_contract(spec(), "overlay-existing")
    assert (off.evaluation_status, off.pipeline_status) == ("disabled", "disabled")
    unconfigured = C.build_contract(spec(evals_enabled=True, pipeline_enabled=True), "overlay-existing")
    assert unconfigured.evaluation_status == "unconfigured"
    assert unconfigured.pipeline_status == "selected-not-executed"
    cfg = {"judge_model": "j", "min_pass_rate": 0.8, "evaluators": ["relevance"], "dataset_status": "proposed",
           "cases": [{"query": "a"}]}
    proposed = C.build_contract(spec(evals_enabled=True, evaluation=cfg), "overlay-existing")
    assert proposed.evaluation_status == "configured-proposed"
    approved = C.build_contract(spec(evals_enabled=True, evaluation=dict(cfg, dataset_status="approved")),
                                "overlay-existing")
    assert approved.evaluation_status == "configured-approved"


def test_router_coverage_gaps_are_reported_and_not_invented():
    s = router_spec(evals_enabled=True, specialists=[
        {"name": "billing", "instructions": "x", "examples": ["q1", "q2"]},
        {"name": "fraud", "instructions": "y", "examples": []},
    ])
    gaps = " ".join(C.build_contract(s, "create-new").gaps)
    assert "fraud" in gaps and "out-of-scope" in gaps
    with_oos = router_spec(evals_enabled=True, out_of_scope_examples=["Tell me a joke"])
    assert "out-of-scope" not in " ".join(C.build_contract(with_oos, "create-new").gaps)
    assert C.build_contract(spec(evals_enabled=True), "overlay-existing").gaps == () or \
        "fraud" not in " ".join(C.build_contract(spec(evals_enabled=True), "overlay-existing").gaps)


def test_unknown_mode_is_rejected():
    with pytest.raises(S.SpecError, match="mode"):
        C.build_contract(spec(), "overwrite")

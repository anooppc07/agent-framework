"""Review-driven fixes for Task 2 (findings 1-4): no overclaiming, session applicability from kind
and mode, Markdown/JSON equivalence with evidence, honest evaluation status.
Real modules only. Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import dataclasses
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_spec as S  # noqa: E402
import runtime_contract as C  # noqa: E402


def spec(**over):
    d = {
        "name": "card-helper", "description": "Dispute intake", "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf",
        "pattern": "single-agent", "pattern_rationale": "one agent is enough", "model": "gpt-5.4",
        "hosted_session_manager": "enabled", "tools": [], "evals_enabled": False, "pipeline_enabled": False,
    }
    d.update(over)
    return S.parse_spec(d)


SPECIALISTS = [{"name": "billing", "instructions": "x", "examples": ["q1"]},
               {"name": "fraud", "instructions": "y", "examples": ["q2"]}]


def router(**over):
    d = dict(name="card-router", pattern="router-specialists", specialists=SPECIALISTS)
    d.update(over)
    return spec(**d)


def forged(s, **over):
    """Bypass parse_spec so the contract itself is tested against invalid metadata."""
    return dataclasses.replace(s, **over)


def deployed(**over):
    d = dict(description="desc-hosted-run", scope="deployed", environment="env-dev-9",
             runtime_version="ver-7.3", test="test-continuity", run="run-4242")
    d.update(over)
    return C.Evidence(**d)


def local(**over):
    d = dict(description="local-fake-pytest", scope="local", test="test-route", run="run-local-1")
    d.update(over)
    return C.Evidence(**d)


def with_control(contract, index, **fields):
    """Replace one control. A missing field is an assertion failure, not an AttributeError."""
    old = contract.controls[index]
    try:
        new = dataclasses.replace(old, **fields)
    except TypeError as exc:
        pytest.fail(f"ControlStatus does not support {sorted(fields)}: {exc}")
    return dataclasses.replace(contract, controls=contract.controls[:index] + (new,) + contract.controls[index + 1:])


# --- finding 1: no overclaiming (cases 1-5) ---------------------------------------------------


@pytest.mark.parametrize("make,mode", [
    (spec, "overlay-existing"), (router, "overlay-existing"), (router, "create-new"),
    (spec, "design-only"), (router, "design-only"),
])
def test_declaration_only_contracts_never_claim_implementation(make, mode):
    c = C.build_contract(make(), mode)
    assert any(x.implementation == "declared" for x in c.controls) or mode == "design-only"
    assert c.claims_implementation is False


def test_claim_is_true_only_when_a_control_is_marked_implemented():
    c = C.build_contract(spec(), "overlay-existing")
    assert c.claims_implementation is False
    flipped = with_control(c, 0, implementation="implemented")
    assert flipped.claims_implementation is True


def test_overlay_capabilities_are_declared_requirements_never_observed():
    for make in (spec, router):
        caps = C.build_contract(make(), "overlay-existing").capabilities
        assert caps, "overlay of a maf spec should list required mechanisms"
        assert {getattr(k, "basis", None) for k in caps} == {"declared-requirement"}


def test_capability_basis_cannot_claim_observation():
    with pytest.raises(C.ContractError, match="basis"):
        C.Capability("x", "platform", "s", basis="observed")


def test_legacy_router_with_an_action_tool_gets_no_tool_less_or_finite_claim():
    s = router(framework="legacy-langgraph", hosted_session_manager="unknown",
               tools=[{"name": "refund", "auth": "none", "requires_approval": False}])
    c = C.build_contract(s, "overlay-existing")
    text = " ".join(f"{k.summary} {k.limits}" for k in c.capabilities).lower()
    assert "tool-less" not in text and "finite" not in text
    assert any("not inspected" in g for g in c.gaps)
    assert [t.name for t in c.tools] == ["refund"]


def test_create_new_router_still_lists_template_provided_mechanisms():
    c = C.build_contract(router(evals_enabled=True), "create-new")
    caps = {k.name: k for k in c.capabilities}
    assert {"context-propagation", "bounded-routing", "refusal"} <= set(caps)
    assert "tool-less" in caps["bounded-routing"].summary.lower()
    assert {getattr(k, "basis", None) for k in caps.values() if k.name != "release-evidence"} == {"provided-by-template"}


@pytest.mark.parametrize("make", [spec, router])
def test_design_only_lists_no_capabilities(make):
    c = C.build_contract(make(), "design-only")
    assert c.capabilities == () and c.claims_implementation is False


# --- finding 2: session applicability from kind and mode (cases 6-9) --------------------------


@pytest.mark.parametrize("mode", ["create-new", "design-only"])
def test_new_hosted_contract_requires_enabled_even_if_declared_not_applicable(mode):
    s = C.build_contract(forged(router(), hosted_session_manager="not-applicable"), mode).session
    assert s.required == "enabled" and s.declared == "not-applicable"
    assert s.blocks_readiness is True


@pytest.mark.parametrize("declared", ["not-applicable", "enabled", "disabled", "unknown"])
def test_hosted_overlay_reports_and_still_blocks_whatever_is_declared(declared):
    s = C.build_contract(forged(spec(), hosted_session_manager=declared), "overlay-existing").session
    assert s.required == "reported-only" and s.declared == declared
    assert s.blocks_readiness is True


@pytest.mark.parametrize("mode", ["overlay-existing", "create-new", "design-only"])
@pytest.mark.parametrize("declared", ["not-applicable", "enabled"])
def test_prompt_agent_is_not_applicable_in_every_mode_and_never_blocks(mode, declared):
    s = forged(spec(), agent_kind="prompt", framework="not-applicable", hosted_session_manager=declared)
    rec = C.build_contract(s, mode).session
    assert rec.required == "not-applicable" and rec.blocks_readiness is False


def test_unknown_agent_kind_or_session_value_raises_contract_error():
    with pytest.raises(C.ContractError, match="agent_kind"):
        C.build_contract(forged(spec(), agent_kind="container"), "overlay-existing")
    with pytest.raises(C.ContractError, match="hosted_session_manager"):
        C.build_contract(forged(spec(), hosted_session_manager="maybe"), "create-new")


# --- finding 3: Markdown preserves what JSON holds (cases 10-15) ------------------------------

TOKENS = ("env-dev-9", "ver-7.3", "test-continuity", "run-4242", "desc-hosted-run", "local-fake-pytest")


def rich_contract():
    c = C.build_contract(router(evals_enabled=True, pipeline_enabled=True,
                                tools=[]), "create-new")
    c = with_control(c, 6, verification="passed", responsibility="platform", deployed_evidence=deployed(),
                     local_evidence=(local(),), mechanisms=("mech-otel-span",), notes="note-audit-xyz",
                     gaps=("gap-token-abc",), component_refs=("comp-host",), source_refs=("src/main.py:12",),
                     test_refs=("tests/t.py::test_a",))
    base = c.session
    session = C.SessionRecord(base.required, base.declared, "enabled", deployed(description="desc-session-proof"),
                              base.owner, base.provenance, base.separate_concerns)
    return dataclasses.replace(c, session=session)


def _unescape(md):
    return re.sub(r"\\([\\`*_{}\[\]<>#|~])", r"\1", md)


def test_markdown_shows_deployed_and_local_evidence_apart_with_full_provenance():
    md = C.render_markdown(rich_contract())
    for token in TOKENS:
        assert token in md, f"{token!r} missing from Markdown"
    deployed_line = next(ln for ln in md.splitlines() if "run-4242" in ln and "desc-hosted-run" in ln)
    local_line = next(ln for ln in md.splitlines() if "local-fake-pytest" in ln)
    assert "deployed" in deployed_line.lower() and "local" not in deployed_line.lower()
    assert "local" in local_line.lower() and "deployed" not in local_line.lower()
    assert "desc-session-proof" in md


def test_markdown_shows_control_notes_mechanisms_and_gaps():
    md = C.render_markdown(rich_contract())
    for token in ("mech-otel-span", "note-audit-xyz", "gap-token-abc"):
        assert token in md


def test_every_string_in_the_json_contract_appears_in_the_markdown():
    c = rich_contract()
    data = json.loads(C.render_json(c))
    md = " ".join(_unescape(C.render_markdown(c)).split())

    def leaves(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                yield from leaves(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from leaves(v)
        elif isinstance(obj, str) and obj.strip():
            yield obj

    missing = sorted({v for v in leaves(data) if " ".join(v.split()) not in md})
    assert not missing, f"in JSON but not Markdown: {missing}"


def _ragged_tables(md):
    blocks, current = [], []
    for ln in md.splitlines():
        if ln.startswith("|"):
            current.append(ln)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return [b for b in blocks if len({len(re.findall(r"(?<!\\)\|", ln)) for ln in b}) != 1]


def test_hostile_evidence_text_cannot_break_markdown():
    hostile = "evil | col `code` *b*\nnew line <script> [x](y)"
    c = C.build_contract(spec(), "overlay-existing")
    c = with_control(c, 6, deployed_evidence=deployed(description=hostile, test=hostile),
                     local_evidence=(local(description=hostile),), notes=hostile, mechanisms=(hostile,),
                     component_refs=(hostile,))
    md = C.render_markdown(c)
    assert not _ragged_tables(md)
    assert "<script>" not in md and "\nnew line" not in md


def test_control_reference_fields_exist_default_empty_and_round_trip():
    c = rich_contract()
    ctrl = c.controls[6]
    assert (ctrl.component_refs, ctrl.source_refs, ctrl.test_refs) == \
           (("comp-host",), ("src/main.py:12",), ("tests/t.py::test_a",))
    data = json.loads(C.render_json(c))["controls"][6]
    assert data["component_refs"] == ["comp-host"] and data["test_refs"] == ["tests/t.py::test_a"]
    md = _unescape(C.render_markdown(c))  # Markdown escapes `_` etc.; compare the text a reader sees
    assert "comp-host" in md and "src/main.py:12" in md and "tests/t.py::test_a" in md


@pytest.mark.parametrize("field", ["component_refs", "source_refs", "test_refs"])
@pytest.mark.parametrize("bad", [(1,), ("",), ("  ",), "abc", [None]], ids=repr)
def test_reference_fields_reject_non_string_blank_or_bare_values(field, bad):
    with pytest.raises(C.ContractError, match=field):
        C.ControlStatus("traceability", "declared", **{field: bad})


def test_generated_contracts_never_invent_references():
    for make, mode in ((spec, "overlay-existing"), (router, "create-new"), (router, "design-only")):
        for ctrl in C.build_contract(make(), mode).controls:
            assert (getattr(ctrl, "component_refs", None), getattr(ctrl, "source_refs", None),
                    getattr(ctrl, "test_refs", None)) == ((), (), ())


# --- finding 4: evaluation status reflects completeness (cases 16-20) -------------------------

FULL = {"judge_model": "judge", "min_pass_rate": 0.8, "evaluators": ["relevance"],
        "dataset_status": "proposed", "cases": [{"query": "a"}]}


def status(evaluation, **over):
    return C.build_contract(spec(evals_enabled=True, evaluation=evaluation, **over), "overlay-existing").evaluation_status


def test_empty_evaluation_block_is_unconfigured():
    assert status({}) == "unconfigured"


@pytest.mark.parametrize("partial", [
    {"dataset_status": "approved"},
    {"dataset_status": "approved", "judge_model": "judge"},
    {"judge_model": "judge"},
])
def test_partial_evaluation_is_incomplete_and_approval_does_not_imply_configured(partial):
    assert status(partial) == "incomplete"


def test_complete_configuration_reports_dataset_approval():
    assert status(FULL) == "configured-proposed"
    assert status(dict(FULL, dataset_status="approved")) == "configured-approved"


@pytest.mark.parametrize("missing", ["judge_model", "min_pass_rate", "evaluators", "cases"])
def test_each_missing_piece_alone_makes_it_incomplete(missing):
    partial = {k: v for k, v in FULL.items() if k != missing}
    assert status(partial) == "incomplete"
    assert status(dict(partial, dataset_status="approved")) == "incomplete"


def test_disabled_and_absent_block_statuses_are_unchanged():
    assert C.build_contract(spec(), "overlay-existing").evaluation_status == "disabled"
    assert C.build_contract(spec(evals_enabled=True), "overlay-existing").evaluation_status == "unconfigured"


def test_json_carries_the_claims_implementation_flag_markdown_shows():
    for make, mode in ((spec, "overlay-existing"), (router, "create-new"), (router, "design-only")):
        c = C.build_contract(make(), mode)
        data = json.loads(C.render_json(c))
        assert data.get("claims_implementation") is False
        assert "Claims implementation: no" in C.render_markdown(c)
    flipped = with_control(C.build_contract(spec(), "overlay-existing"), 0, implementation="implemented")
    assert json.loads(C.render_json(flipped))["claims_implementation"] is True
    assert "Claims implementation: yes" in C.render_markdown(flipped)

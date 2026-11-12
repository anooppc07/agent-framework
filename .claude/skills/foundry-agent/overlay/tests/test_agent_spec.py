"""Task 1 tests (plan cases 1-11, 39-42, 54-56): agent spec and pattern registry.

Real modules and real files only; nothing is mocked. Run from overlay/:
    PYTHONPATH= python -m pytest tests -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_spec as A  # noqa: E402


def base(**over):
    d = {
        "name": "card-helper", "description": 'Helps: "dispute" intake', "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf",
        "pattern": "single-agent", "pattern_rationale": "one agent is enough",
        "model": "gpt-5.4", "hosted_session_manager": "enabled",
        "tools": [], "evals_enabled": False, "pipeline_enabled": False,
    }
    d.update(over)
    return d


def router(**over):
    d = base(
        name="card-router", pattern="router-specialists", evals_enabled=False,
        specialists=[
            {"name": "billing", "instructions": "Answer billing questions.", "examples": ["Why was I charged twice?"]},
            {"name": "fraud", "instructions": "Handle fraud reports.", "examples": ["I did not make this purchase"]},
        ],
    )
    d.update(over)
    return d


# --- cases 1-2: valid specs -------------------------------------------------------------------


def test_single_agent_specs_parse_without_specialists():
    for kind, fw, sm in (("prompt", "not-applicable", "not-applicable"), ("hosted", "maf", "enabled")):
        s = A.parse_spec(base(agent_kind=kind, framework=fw, hosted_session_manager=sm))
        assert s.specialists == () and s.pattern == "single-agent" and s.agent_kind == kind


def test_hosted_router_with_two_specialists_parses():
    s = A.parse_spec(router())
    assert [x.name for x in s.specialists] == ["billing", "fraud"]


# --- case 3: empty / null ---------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, {}, [], "text", 5])
def test_non_mapping_or_empty_input_raises_spec_error(bad):
    with pytest.raises(A.SpecError):
        A.parse_spec(bad)


@pytest.mark.parametrize("field", ["name", "description", "owner", "model", "pattern_rationale"])
@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_required_metadata_raises(field, blank):
    with pytest.raises(A.SpecError, match=field):
        A.parse_spec(base(**{field: blank}))


# --- cases 4-5: topology ----------------------------------------------------------------------


def test_runnable_router_needs_a_specialist_but_design_only_may_omit():
    with pytest.raises(A.SpecError, match="specialist"):
        A.validate_for_mode(A.parse_spec(router(specialists=[])), "create-new")
    s = A.parse_spec(router(specialists=[]))
    A.validate_for_mode(s, "design-only")  # must not raise


def test_one_specialist_is_valid():
    one = router(specialists=[{"name": "billing", "instructions": "x", "examples": []}])
    A.validate_for_mode(A.parse_spec(one), "create-new")


@pytest.mark.parametrize("second", ["Billing", " billing ", "BILLING"])
def test_normalized_duplicate_specialist_ids_rejected(second):
    sp = [{"name": "billing", "instructions": "x"}, {"name": second, "instructions": "y"}]
    with pytest.raises(A.SpecError, match="duplicate"):
        A.parse_spec(router(specialists=sp))


@pytest.mark.parametrize("reserved", ["refusal", "router", "unknown"])
def test_reserved_specialist_ids_rejected(reserved):
    with pytest.raises(A.SpecError, match="reserved"):
        A.parse_spec(router(specialists=[{"name": reserved, "instructions": "x"}]))


# --- cases 6-7: pattern registry --------------------------------------------------------------


def test_pattern_registry_has_exactly_the_four_pinned_ids():
    assert list(A.PATTERNS) == ["single-agent", "router-specialists", "supervisor-worker", "agent-handoffs"]
    assert {k: v.status for k, v in A.PATTERNS.items()} == {
        "single-agent": "runnable-unvalidated", "router-specialists": "runnable-unvalidated",
        "supervisor-worker": "design-only", "agent-handoffs": "design-only",
    }


def test_unknown_pattern_lists_supported_candidates_without_fallback():
    with pytest.raises(A.UnsupportedPattern) as e:
        A.parse_spec(base(pattern="swarm"))
    for pid in A.PATTERNS:
        assert pid in str(e.value)


@pytest.mark.parametrize("pattern", ["supervisor-worker", "agent-handoffs"])
@pytest.mark.parametrize("mode", ["create-new", "overlay-existing"])
def test_design_only_patterns_reject_executable_and_overlay_modes(pattern, mode):
    s = A.parse_spec(base(pattern=pattern))
    with pytest.raises(A.UnsupportedPattern, match="design-only"):
        A.validate_for_mode(s, mode)
    A.validate_for_mode(s, "design-only")


# --- cases 8-9: enums and tools ---------------------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("classification", "secret"), ("agent_kind", "container"), ("evals_enabled", "yes"),
    ("pipeline_enabled", 1), ("framework", "langgraph"),
])
def test_invalid_enum_or_type_values_rejected(field, value):
    with pytest.raises(A.SpecError, match=field):
        A.parse_spec(base(**{field: value}))


def test_tool_auth_mode_and_approval_flag_validated():
    ok = A.parse_spec(base(tools=[{"name": "lookup", "auth": "user-token", "requires_approval": True}]))
    assert ok.tools[0].auth == "user-token" and ok.tools[0].requires_approval is True
    with pytest.raises(A.SpecError, match="auth"):
        A.parse_spec(base(tools=[{"name": "t", "auth": "magic", "requires_approval": False}]))
    with pytest.raises(A.SpecError, match="requires_approval"):
        A.parse_spec(base(tools=[{"name": "t", "auth": "none", "requires_approval": "yes"}]))


def test_duplicate_tool_names_rejected():
    t = {"name": "Lookup", "auth": "none", "requires_approval": False}
    with pytest.raises(A.SpecError, match="duplicate"):
        A.parse_spec(base(tools=[t, dict(t, name="lookup")]))


# --- case 10: load_spec -----------------------------------------------------------------------


def test_load_spec_missing_and_malformed_files_raise_contextual_spec_error(tmp_path):
    with pytest.raises(A.SpecError, match="missing.json"):
        A.load_spec(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(A.SpecError, match="bad.json"):
        A.load_spec(bad)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(base()), encoding="utf-8")
    assert A.load_spec(good).name == "card-helper"


# --- case 11 and 17: verbatim text, ordering --------------------------------------------------


def test_queries_verbatim_and_ordering_preserved():
    q = ["  Why  was I charged\ttwice? ", "Zażółć gęślą 🙂", "b", "a"]
    s = A.parse_spec(router(specialists=[
        {"name": "billing", "instructions": "x", "examples": q},
        {"name": "fraud", "instructions": "y", "examples": ["z"]},
    ]))
    assert list(s.specialists[0].examples) == q
    assert [x.name for x in s.specialists] == ["billing", "fraud"]


def test_same_content_different_key_order_gives_equal_specs():
    a = base()
    b = dict(reversed(list(a.items())))
    sa, sb = A.parse_spec(a), A.parse_spec(b)
    assert sa == sb and sa.name == "card-helper" and sa.owner == "payments-team"


# --- cases 39-40: text and ids ----------------------------------------------------------------


def test_description_with_quotes_and_colons_round_trips_through_yaml_scalar():
    yaml = pytest.importorskip("yaml")
    text = 'He said: "hi" # not a comment'
    s = A.parse_spec(base(description=text))
    assert yaml.safe_load("k: " + A.yaml_scalar(s.description)) == {"k": text}


@pytest.mark.parametrize("bad", ["Card_Helper", "card helper", "-x", "x-", "a--b", "é", "x" * 64])
def test_agent_and_specialist_ids_must_be_kebab_case(bad):
    with pytest.raises(A.SpecError, match="kebab"):
        A.parse_spec(base(name=bad))
    with pytest.raises(A.SpecError, match="kebab"):
        A.parse_spec(router(specialists=[{"name": bad, "instructions": "x"}]))


# --- case 41: eval-enabled router requirements and release preflight --------------------------


def test_eval_enabled_router_needs_examples_per_specialist_and_two_total():
    no_ex = router(evals_enabled=True, specialists=[{"name": "billing", "instructions": "x", "examples": []}])
    with pytest.raises(A.SpecError, match="example"):
        A.validate_for_mode(A.parse_spec(no_ex), "create-new")
    one_total = router(evals_enabled=True, specialists=[{"name": "billing", "instructions": "x", "examples": ["q"]}])
    with pytest.raises(A.SpecError, match="two"):
        A.validate_for_mode(A.parse_spec(one_total), "create-new")
    two_total = router(evals_enabled=True, specialists=[{"name": "billing", "instructions": "x", "examples": ["q1", "q2"]}])
    A.validate_for_mode(A.parse_spec(two_total), "create-new")


def test_no_router_only_requirements_for_single_agent_design_overlay_or_evals_off():
    A.validate_for_mode(A.parse_spec(base(evals_enabled=True)), "overlay-existing")
    A.validate_for_mode(A.parse_spec(router(evals_enabled=True, specialists=[])), "design-only")
    A.validate_for_mode(A.parse_spec(router(evals_enabled=False)), "create-new")


def _eval_cfg(**over):
    cfg = {"judge_model": "judge-deploy", "min_pass_rate": 0.8, "evaluators": ["relevance"],
           "dataset_status": "approved", "cases": [{"query": f"q{i}"} for i in range(3)]}
    cfg.update(over)
    return cfg


def test_release_config_requires_three_approved_queries_and_configured_judge():
    A.validate_release_config(A.parse_spec(base(evals_enabled=True, evaluation=_eval_cfg())))
    for bad in (_eval_cfg(cases=[{"query": "a"}, {"query": "b"}]), _eval_cfg(dataset_status="proposed"),
                _eval_cfg(judge_model=""), _eval_cfg(min_pass_rate=None), _eval_cfg(evaluators=[])):
        with pytest.raises(A.SpecError):
            A.validate_release_config(A.parse_spec(base(evals_enabled=True, evaluation=bad)))
    with pytest.raises(A.SpecError, match="evals"):
        A.validate_release_config(A.parse_spec(base(evals_enabled=False)))


def test_unconfigured_evaluation_values_parse_but_block_release():
    s = A.parse_spec(base(evals_enabled=True))  # no evaluation block at all
    with pytest.raises(A.SpecError, match="unconfigured"):
        A.validate_release_config(s)


# --- case 42 and 54-56, 15-16: framework/session/mode rules -----------------------------------


def test_runtime_selections_recorded_not_verified():
    s = A.parse_spec(base(runtime="python_3_13", resource={"cpu": "0.25", "memory": "0.5Gi"}))
    assert s.runtime == "python_3_13" and s.resource == {"cpu": "0.25", "memory": "0.5Gi"}
    assert A.parse_spec(base()).runtime == "unspecified"  # inherited default, not claimed verified


def test_new_hosted_requires_maf_and_enabled_session_manager():
    A.validate_new_hosted_spec(A.parse_spec(base()))
    with pytest.raises(A.SpecError, match="maf"):
        A.validate_new_hosted_spec(A.parse_spec(base(framework="legacy-langgraph")))
    with pytest.raises(A.SpecError, match="session"):
        A.validate_new_hosted_spec(A.parse_spec(base(hosted_session_manager="disabled")))
    with pytest.raises(A.SpecError, match="session"):
        A.validate_new_hosted_spec(A.parse_spec(base(hosted_session_manager="unknown")))


def test_existing_overlay_may_name_legacy_framework_and_any_session_state():
    for sm in ("enabled", "disabled", "unknown"):
        s = A.parse_spec(base(framework="legacy-langgraph", hosted_session_manager=sm))
        A.validate_for_mode(s, "overlay-existing")


def test_prompt_agent_must_mark_framework_and_session_not_applicable():
    with pytest.raises(A.SpecError, match="framework"):
        A.parse_spec(base(agent_kind="prompt", framework="maf"))


def test_runnable_router_rejects_prompt_kind_tools_and_non_maf():
    prompt = router(agent_kind="prompt", framework="not-applicable", hosted_session_manager="not-applicable")
    with pytest.raises(A.SpecError, match="prompt"):
        A.validate_for_mode(A.parse_spec(prompt), "create-new")
    tool = {"name": "lookup", "auth": "none", "requires_approval": False}
    with pytest.raises(A.SpecError, match="tool"):
        A.validate_for_mode(A.parse_spec(router(tools=[tool])), "create-new")
    with pytest.raises(A.SpecError, match="maf"):
        A.validate_for_mode(A.parse_spec(router(framework="legacy-langgraph")), "create-new")


def test_create_new_rejects_single_agent_which_belongs_to_the_base_flow():
    with pytest.raises(A.SpecError, match="base flow"):
        A.validate_for_mode(A.parse_spec(base()), "create-new")


def test_unknown_mode_rejected():
    with pytest.raises(A.SpecError, match="mode"):
        A.validate_for_mode(A.parse_spec(base()), "overwrite")


def test_pipeline_requires_evals_enabled():
    with pytest.raises(A.SpecError, match="pipeline"):
        A.parse_spec(base(pipeline_enabled=True, evals_enabled=False))
    A.parse_spec(base(pipeline_enabled=True, evals_enabled=True))


# --- review-added: edge cases found after GREEN -----------------------------------------------


def test_yaml_scalar_preserves_unicode_newlines_and_backslashes():
    yaml = pytest.importorskip("yaml")
    for text in ("Zażółć 🙂", "line1\nline2", "back\slash 'q' \"d\"", "- dash: colon #hash"):
        assert yaml.safe_load("k: " + A.yaml_scalar(text)) == {"k": text}


def test_load_spec_prefixes_validation_errors_with_the_file_path(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(base(owner="")), encoding="utf-8")
    with pytest.raises(A.SpecError, match=r"s\.json.*owner"):
        A.load_spec(f)


def test_min_pass_rate_must_be_a_number_between_zero_and_one():
    for bad in (1.5, -0.1, "0.8", True):
        with pytest.raises(A.SpecError, match="min_pass_rate"):
            A.parse_spec(base(evals_enabled=True, evaluation=_eval_cfg(min_pass_rate=bad)))

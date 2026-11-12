"""Review-driven fixes for Task 1 (findings 1-3): type validation, release-case validation,
router minimum-query rule. Real modules only. Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_spec as A  # noqa: E402


def base(**over):
    d = {
        "name": "card-helper", "description": "Dispute intake", "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf",
        "pattern": "single-agent", "pattern_rationale": "one agent is enough", "model": "gpt-5.4",
        "hosted_session_manager": "enabled", "tools": [], "evals_enabled": True, "pipeline_enabled": False,
    }
    d.update(over)
    return d


def cfg(**over):
    c = {"judge_model": "judge", "min_pass_rate": 0.8, "evaluators": ["relevance"],
         "dataset_status": "approved", "cases": [{"query": f"q{i}"} for i in range(3)]}
    c.update(over)
    return c


def router(**over):
    d = base(pattern="router-specialists", name="card-router", specialists=[
        {"name": "billing", "instructions": "x", "examples": ["q1"]},
        {"name": "fraud", "instructions": "y", "examples": ["q2"]},
    ])
    d.update(over)
    return d


# --- cases 1-3: evaluation collections and entries --------------------------------------------


@pytest.mark.parametrize("bad", ["relevance", [1], [None], [""], ["  "], {"a": 1}, 3, True],
                         ids=repr)
def test_evaluators_must_be_a_list_of_nonblank_strings(bad):
    with pytest.raises(A.SpecError, match="evaluators"):
        A.parse_spec(base(evaluation=cfg(evaluators=bad)))


@pytest.mark.parametrize("bad", ["abc", 3, {"query": "a"}, True], ids=repr)
def test_evaluation_cases_must_be_a_list(bad):
    with pytest.raises(A.SpecError, match="cases"):
        A.parse_spec(base(evaluation=cfg(cases=bad)))


@pytest.mark.parametrize("entry", [{}, {"query": " "}, {"query": ""}, {"query": 3}, {"query": None}, "abc", None, 7],
                         ids=repr)
def test_each_case_needs_a_mapping_with_a_nonblank_query(entry):
    with pytest.raises(A.SpecError, match="cases"):
        A.parse_spec(base(evaluation=cfg(cases=[{"query": "ok"}, entry, {"query": "ok2"}])))


def test_release_config_rejects_what_used_to_slip_through():
    for bad in (cfg(evaluators="relevance"), cfg(cases="abc"), cfg(cases=[{}, {}, {}]),
                cfg(cases=[{"query": " "}] * 3), cfg(evaluators=[1, None])):
        with pytest.raises(A.SpecError):
            A.validate_release_config(A.parse_spec(base(evaluation=bad)))
    A.validate_release_config(A.parse_spec(base(evaluation=cfg())))  # a valid one still passes


def test_case_query_text_is_kept_verbatim():
    q = "  Zażółć\tgęślą 🙂 "
    s = A.parse_spec(base(evaluation=cfg(cases=[{"query": q}, {"query": "b"}, {"query": "c"}])))
    assert s.evaluation.cases[0]["query"] == q


# --- case 4: router expected routes -----------------------------------------------------------

ROUTED = [{"query": "q1", "expected_route": "billing"}, {"query": "q2", "expected_route": "fraud"},
          {"query": "q3", "expected_route": "refusal"}]


def test_router_release_cases_need_a_valid_expected_route():
    A.validate_release_config(A.parse_spec(router(evaluation=cfg(cases=ROUTED))))
    missing = [dict(c) for c in ROUTED]
    del missing[0]["expected_route"]
    parsed = A.parse_spec(router(evaluation=cfg(cases=missing)))  # parsing allows it; release does not
    with pytest.raises(A.SpecError, match="expected_route"):
        A.validate_release_config(parsed)


@pytest.mark.parametrize("route", ["payments", "", 5, None, "Billing"], ids=repr)
def test_unknown_or_malformed_expected_route_is_rejected(route):
    cases = [dict(ROUTED[0], expected_route=route)] + ROUTED[1:]
    with pytest.raises(A.SpecError, match="expected_route"):
        A.parse_spec(router(evaluation=cfg(cases=cases)))


def test_single_agent_cases_need_no_route_and_reject_one():
    A.parse_spec(base(evaluation=cfg()))
    with pytest.raises(A.SpecError, match="expected_route"):
        A.parse_spec(base(evaluation=cfg(cases=[{"query": "a", "expected_route": "billing"}] * 3)))


# --- case 5: pattern type ---------------------------------------------------------------------


@pytest.mark.parametrize("bad", [[], {}, 3, None, True, ["single-agent"]], ids=repr)
def test_non_string_pattern_raises_unsupported_pattern_listing_candidates(bad):
    with pytest.raises(A.UnsupportedPattern) as e:
        A.parse_spec(base(pattern=bad))
    assert all(pid in str(e.value) for pid in A.PATTERNS)


# --- case 6: runtime and resource types -------------------------------------------------------


@pytest.mark.parametrize("bad", [{}, {"a": 1}, ["python_3_13"], 3, True, "", "   "], ids=repr)
def test_runtime_must_be_a_nonblank_string_when_given(bad):
    with pytest.raises(A.SpecError, match="runtime"):
        A.parse_spec(base(runtime=bad))


def test_runtime_absent_or_none_means_unspecified():
    assert A.parse_spec(base()).runtime == "unspecified"
    assert A.parse_spec(base(runtime=None)).runtime == "unspecified"


@pytest.mark.parametrize("bad", [{"cpu": 1}, {"cpu": ""}, {"cpu": None}, {1: "x"}, ["cpu"], "cpu"], ids=repr)
def test_resource_must_map_strings_to_nonblank_strings(bad):
    with pytest.raises(A.SpecError, match="resource"):
        A.parse_spec(base(resource=bad))


# --- case 7: out-of-scope examples ------------------------------------------------------------


@pytest.mark.parametrize("bad", [[1], [""], ["  "], [None], "joke", {"a": "b"}, 3], ids=repr)
def test_out_of_scope_examples_must_be_nonblank_strings(bad):
    with pytest.raises(A.SpecError, match="out_of_scope"):
        A.parse_spec(base(out_of_scope_examples=bad))


def test_out_of_scope_examples_verbatim_and_ordered():
    q = [" Tell me a joke ", "Zażółć 🙂", "b"]
    assert list(A.parse_spec(base(out_of_scope_examples=q)).out_of_scope_examples) == q


# --- case 8: session-manager state by kind ----------------------------------------------------


@pytest.mark.parametrize("state", ["enabled", "disabled", "unknown"])
def test_prompt_agent_must_declare_session_not_applicable(state):
    with pytest.raises(A.SpecError, match="session"):
        A.parse_spec(base(agent_kind="prompt", framework="not-applicable", hosted_session_manager=state))
    A.parse_spec(base(agent_kind="prompt", framework="not-applicable", hosted_session_manager="not-applicable"))


def test_hosted_agent_cannot_declare_session_not_applicable():
    with pytest.raises(A.SpecError, match="session"):
        A.parse_spec(base(hosted_session_manager="not-applicable"))


# --- case 9: nothing escapes as a non-SpecError -----------------------------------------------

BAD_VALUES = [[], {}, 3, 1.5, True, None, ""]
TOP_FIELDS = ["name", "description", "owner", "classification", "agent_kind", "framework", "pattern",
              "pattern_rationale", "model", "hosted_session_manager", "tools", "specialists",
              "out_of_scope_examples", "evals_enabled", "pipeline_enabled", "evaluation", "runtime", "resource"]


def _only_spec_error(data):
    try:
        A.parse_spec(data)
    except A.SpecError:
        pass  # actionable; any other exception type propagates and fails the test


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
@pytest.mark.parametrize("field", TOP_FIELDS)
def test_no_top_level_field_can_escape_as_a_non_spec_error(field, bad):
    _only_spec_error(base(**{field: bad}))


TOOL = {"name": "lookup", "auth": "none", "requires_approval": False}
SPEC = {"name": "billing", "instructions": "x", "examples": ["q"]}
NESTED = (
    [("tool." + k, lambda v, k=k: base(tools=[dict(TOOL, **{k: v})])) for k in TOOL]
    + [("tool-entry", lambda v: base(tools=[v]))]
    + [("specialist." + k, lambda v, k=k: router(specialists=[dict(SPEC, **{k: v})])) for k in SPEC]
    + [("specialist-entry", lambda v: router(specialists=[v]))]
    + [("evaluation." + k, lambda v, k=k: base(evaluation=cfg(**{k: v}))) for k in cfg()]
    + [("case-entry", lambda v: base(evaluation=cfg(cases=[v]))),
       ("case.query", lambda v: base(evaluation=cfg(cases=[{"query": v}]))),
       ("case.expected_route", lambda v: router(evaluation=cfg(cases=[{"query": "q", "expected_route": v}])))]
)


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
@pytest.mark.parametrize("label,make", NESTED, ids=[n[0] for n in NESTED])
def test_no_nested_field_can_escape_as_a_non_spec_error(label, make, bad):
    _only_spec_error(make(bad))


# --- cases 10-12: router minimum counts specialist plus out-of-scope examples -----------------


def test_one_specialist_example_plus_one_out_of_scope_example_is_valid():
    s = A.parse_spec(router(specialists=[{"name": "billing", "instructions": "x", "examples": ["q"]}],
                            out_of_scope_examples=["Tell me a joke"]))
    A.validate_for_mode(s, "create-new")


def test_one_specialist_example_alone_is_invalid():
    s = A.parse_spec(router(specialists=[{"name": "billing", "instructions": "x", "examples": ["q"]}],
                            out_of_scope_examples=[]))
    with pytest.raises(A.SpecError, match="two"):
        A.validate_for_mode(s, "create-new")


def test_out_of_scope_example_cannot_replace_a_missing_specialist_example():
    s = A.parse_spec(router(specialists=[
        {"name": "billing", "instructions": "x", "examples": ["q1", "q2"]},
        {"name": "fraud", "instructions": "y", "examples": []},
    ], out_of_scope_examples=["Tell me a joke"]))
    with pytest.raises(A.SpecError, match="every specialist"):
        A.validate_for_mode(s, "create-new")


# --- found while building the Task 3 router: specialist ids must not collide with internal executors ---


@pytest.mark.parametrize("name", ["classifier", "normalize", "refuse"])
def test_specialist_ids_that_collide_with_router_internal_executors_are_reserved(name):
    with pytest.raises(A.SpecError, match="reserved"):
        A.parse_spec(router(specialists=[{"name": name, "instructions": "x"}]))


def test_dispatch_prefixed_ids_that_would_collide_are_reserved():
    with pytest.raises(A.SpecError, match="reserved"):
        A.parse_spec(router(specialists=[{"name": "dispatch-billing", "instructions": "x"},
                                         {"name": "billing", "instructions": "y"}]))
    A.parse_spec(router(specialists=[{"name": "dispatcher", "instructions": "x"}]))  # a similar name is fine


# --- reported by the Task 3b agent: yaml_scalar left some control characters raw -----------------


@pytest.mark.parametrize("text", ["a\x7fb", "a\x85b", "a\x9fb", "tab\there", "bell\x07x", "nul\x00x", "a b",
                                  "a b", "back\slash", 'q"uote', "emoji \U0001F642 ok"])
def test_yaml_scalar_round_trips_control_and_separator_characters(text):
    yaml = pytest.importorskip("yaml")
    out = A.yaml_scalar(text)
    assert yaml.safe_load("k: " + out) == {"k": text}


def test_yaml_scalar_output_has_no_raw_control_characters():
    out = A.yaml_scalar("a\x7fb\x85c\x00d e")
    assert all(ord(c) >= 0x20 and not 0x7F <= ord(c) <= 0x9F and c not in "  " for c in out), repr(out)

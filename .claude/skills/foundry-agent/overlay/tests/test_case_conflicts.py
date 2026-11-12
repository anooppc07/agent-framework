"""Duplicate and conflicting evaluation cases (plan case 64 and Task 9): the same query must not appear
twice, and must never carry two different expected routes, whether the repeats are in evaluation.cases
or against specialist / out-of-scope examples. Query text is compared normalized (whitespace) but kept
verbatim. Real modules only. Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_spec as A  # noqa: E402


def cfg(cases, **over):
    c = {"judge_model": "judge", "min_pass_rate": 0.8, "evaluators": ["relevance"],
         "dataset_status": "approved", "cases": cases}
    c.update(over)
    return c


def base(**over):
    d = {
        "name": "card-helper", "description": "d", "owner": "o", "classification": "internal",
        "agent_kind": "hosted", "framework": "maf", "pattern": "single-agent", "pattern_rationale": "r",
        "model": "m", "hosted_session_manager": "enabled", "evals_enabled": True,
    }
    d.update(over)
    return d


def router(cases, specialists=None, **over):
    d = base(pattern="router-specialists", name="card-router", specialists=specialists or [
        {"name": "billing", "instructions": "x", "examples": ["Why was I charged twice?"]},
        {"name": "fraud", "instructions": "y", "examples": ["I did not make this purchase"]},
    ], evaluation=cfg(cases))
    d.update(over)
    return d


# --- cases 1-2: duplicate and conflicting routes within evaluation.cases ----------------------


def test_same_query_with_the_same_route_is_a_duplicate():
    cases = [{"query": "refund please", "expected_route": "billing"},
             {"query": "refund please", "expected_route": "billing"}]
    with pytest.raises(A.SpecError, match="duplicate"):
        A.parse_spec(router(cases))


def test_same_query_with_different_routes_is_a_conflict():
    cases = [{"query": "refund please", "expected_route": "billing"},
             {"query": "refund please", "expected_route": "fraud"}]
    with pytest.raises(A.SpecError, match="conflict"):
        A.parse_spec(router(cases))


def test_a_repeated_query_where_only_one_has_a_route_is_still_rejected():
    cases = [{"query": "refund please", "expected_route": "billing"}, {"query": "refund please"}]
    with pytest.raises(A.SpecError, match="conflict"):
        A.parse_spec(router(cases))
    with pytest.raises(A.SpecError, match="duplicate"):
        A.parse_spec(router([{"query": "refund please"}, {"query": "refund please"}]))


# --- case 3: whitespace normalization, verbatim storage ---------------------------------------


@pytest.mark.parametrize("second", ["  refund please ", "refund\tplease", "refund   please", "\nrefund please\n"])
def test_queries_differing_only_in_whitespace_are_the_same_query(second):
    cases = [{"query": "refund please", "expected_route": "billing"}, {"query": second, "expected_route": "billing"}]
    with pytest.raises(A.SpecError, match="duplicate"):
        A.parse_spec(router(cases))


def test_case_text_is_stored_verbatim_when_valid():
    q = "  Zażółć   gęślą 🙂 "
    s = A.parse_spec(router([{"query": q, "expected_route": "billing"}, {"query": "other", "expected_route": "fraud"}]))
    assert s.evaluation.cases[0]["query"] == q


def test_different_case_letters_are_different_queries():
    cases = [{"query": "Refund", "expected_route": "billing"}, {"query": "refund", "expected_route": "billing"}]
    A.parse_spec(router(cases))


# --- case 4: distinct queries may share a route ------------------------------------------------


def test_different_queries_may_share_a_route():
    cases = [{"query": "q1", "expected_route": "billing"}, {"query": "q2", "expected_route": "billing"},
             {"query": "q3", "expected_route": "refusal"}, {"query": "q4", "expected_route": "refusal"}]
    assert len(A.parse_spec(router(cases)).evaluation.cases) == 4


# --- case 5: single-agent cases ----------------------------------------------------------------


def test_single_agent_duplicate_queries_are_rejected():
    with pytest.raises(A.SpecError, match="duplicate"):
        A.parse_spec(base(evaluation=cfg([{"query": "a"}, {"query": "b"}, {"query": " a "}])))
    A.parse_spec(base(evaluation=cfg([{"query": "a"}, {"query": "b"}, {"query": "c"}])))


# --- case 6: cross-check against specialist and out-of-scope examples -------------------------


def test_case_whose_route_disagrees_with_a_specialist_example_is_a_conflict():
    cases = [{"query": "Why was I charged twice?", "expected_route": "fraud"}]
    with pytest.raises(A.SpecError, match="conflict") as e:
        A.parse_spec(router(cases))
    assert "billing" in str(e.value)


def test_case_that_agrees_with_a_specialist_example_is_accepted():
    A.parse_spec(router([{"query": "Why was I charged twice?", "expected_route": "billing"}]))


def test_case_whose_route_disagrees_with_an_out_of_scope_example_is_a_conflict():
    cases = [{"query": "Tell me a joke", "expected_route": "billing"}]
    with pytest.raises(A.SpecError, match="conflict") as e:
        A.parse_spec(router(cases, out_of_scope_examples=["Tell me a joke"]))
    assert "out_of_scope" in str(e.value)
    A.parse_spec(router([{"query": "Tell me a joke", "expected_route": "refusal"}],
                        out_of_scope_examples=["Tell me a joke"]))


def test_the_same_example_under_two_specialists_is_a_conflict():
    specs = [{"name": "billing", "instructions": "x", "examples": ["shared question"]},
             {"name": "fraud", "instructions": "y", "examples": ["  shared question "]}]
    with pytest.raises(A.SpecError, match="conflict"):
        A.parse_spec(router([], specialists=specs))


def test_an_out_of_scope_example_that_is_also_a_specialist_example_is_a_conflict():
    with pytest.raises(A.SpecError, match="conflict"):
        A.parse_spec(router([], out_of_scope_examples=["Why was I charged twice?"]))


def test_single_agent_has_no_route_to_cross_check():
    A.parse_spec(base(out_of_scope_examples=["same text"], evaluation=cfg([{"query": "same text"}])))


# --- case 7: messages name the positions -------------------------------------------------------


def test_duplicate_message_names_both_case_positions():
    cases = [{"query": "a", "expected_route": "billing"}, {"query": "b", "expected_route": "billing"},
             {"query": "c", "expected_route": "fraud"}, {"query": "a", "expected_route": "billing"}]
    with pytest.raises(A.SpecError) as e:
        A.parse_spec(router(cases))
    assert "cases[0]" in str(e.value) and "cases[3]" in str(e.value)


def test_conflict_message_names_both_positions_and_both_routes():
    cases = [{"query": "x", "expected_route": "billing"}, {"query": "y", "expected_route": "fraud"},
             {"query": "x", "expected_route": "fraud"}]
    with pytest.raises(A.SpecError) as e:
        A.parse_spec(router(cases))
    msg = str(e.value)
    assert "cases[0]" in msg and "cases[2]" in msg and "billing" in msg and "fraud" in msg


# --- case 8: order preserved, earliest occurrence reported -------------------------------------


def test_valid_case_order_is_preserved():
    queries = ["z", "a", "m", "b"]
    cases = [{"query": q, "expected_route": "billing"} for q in queries]
    assert [c["query"] for c in A.parse_spec(router(cases)).evaluation.cases] == queries


def test_the_earliest_occurrence_is_the_one_reported_as_first():
    cases = [{"query": "dup", "expected_route": "billing"}, {"query": "dup", "expected_route": "billing"},
             {"query": "dup", "expected_route": "billing"}]
    with pytest.raises(A.SpecError) as e:
        A.parse_spec(router(cases))
    assert "cases[0]" in str(e.value) and "cases[1]" in str(e.value) and "cases[2]" not in str(e.value)


def test_release_validation_still_passes_for_a_consistent_set():
    cases = [{"query": "q1", "expected_route": "billing"}, {"query": "q2", "expected_route": "fraud"},
             {"query": "q3", "expected_route": "refusal"}]
    A.validate_release_config(A.parse_spec(router(cases)))

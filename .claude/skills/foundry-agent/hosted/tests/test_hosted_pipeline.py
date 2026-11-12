"""Hosted lifecycle adapter and promotion gate (plan Task 7, cases 5-12 of the approved list).

Real `hosted_pipeline` code on real files. The only fake is `FakeAdapter` in pipeline_fakes.py, standing
in for the external Foundry/CLI boundary. These prove the gate and ordering logic locally; they do not
show that any hosted pipeline has run.
Run (PYTHONPATH unset):  python -m pytest hosted/tests/test_hosted_pipeline.py -q
"""

import dataclasses
import json

import pytest

import pipeline_fakes as F
from pipeline_fakes import FakeAdapter, hp, identity

# --- C5: the adapter is an explicit interface --------------------------------------------------

ADAPTER_METHODS = {
    "capabilities", "serving_version", "deploy_candidate", "resolve_candidate_version",
    "run_evaluation", "check_session", "promote", "smoke", "rollback",
}


def test_adapter_interface_is_explicit_and_abstract():
    assert ADAPTER_METHODS <= set(hp.LifecycleAdapter.__abstractmethods__)
    with pytest.raises(TypeError):
        hp.LifecycleAdapter()


def test_incomplete_adapter_cannot_be_instantiated():
    class Partial(hp.LifecycleAdapter):
        def capabilities(self):
            return hp.Capabilities()

    with pytest.raises(TypeError):
        Partial()


def test_hand_written_fake_drives_the_whole_flow():
    adapter = FakeAdapter()
    report = hp.run_release(adapter, identity())
    assert report.status == hp.PROMOTED
    assert adapter.log == [
        "capabilities", "serving_version", "deploy_candidate", "serving_version",
        "resolve_candidate_version", "run_evaluation", "check_session", "promote", "serving_version", "smoke",
    ]


def test_default_adapter_is_registered_unconfigured_and_blocks_every_operation():
    assert "unconfigured" in hp.ADAPTERS
    adapter = hp.ADAPTERS["unconfigured"]()
    caps = adapter.capabilities()
    assert (caps.non_serving_candidate, caps.promotion_boundary, caps.route_evidence) == (False, False, False)
    for call in (lambda: adapter.deploy_candidate(identity()), lambda: adapter.serving_version("a"),
                 lambda: adapter.promote(hp.Candidate("a", "1", "x")), lambda: adapter.rollback("a", "1")):
        with pytest.raises(hp.AdapterUnavailable):
            call()


# --- C6: the candidate is non-serving, otherwise the template is BLOCKED ------------------------


def test_adapter_without_non_serving_candidate_blocks_before_any_deploy():
    adapter = FakeAdapter(caps=hp.Capabilities(non_serving_candidate=False, promotion_boundary=True))
    report = hp.run_release(adapter, identity())
    assert report.status == hp.BLOCKED
    assert "deploy_candidate" not in adapter.log and adapter.serving == "2"
    assert any("non-serving" in r for r in report.reasons)


def test_adapter_without_promotion_boundary_blocks_before_any_deploy():
    adapter = FakeAdapter(caps=hp.Capabilities(non_serving_candidate=True, promotion_boundary=False))
    report = hp.run_release(adapter, identity())
    assert report.status == hp.BLOCKED
    assert "deploy_candidate" not in adapter.log
    assert any("promotion boundary" in r for r in report.reasons)


def test_unconfigured_default_adapter_blocks_the_release():
    adapter = hp.ADAPTERS["unconfigured"]()
    report = hp.run_release(adapter, identity())
    assert report.status == hp.BLOCKED and report.serving_after is None


def test_capability_probe_failure_blocks():
    adapter = FakeAdapter()
    adapter.caps_error = RuntimeError("cli missing")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.BLOCKED and "deploy_candidate" not in adapter.log


def test_configuration_blockers_stop_the_release_before_any_adapter_call():
    adapter = FakeAdapter()
    report = hp.run_release(adapter, identity(blockers=("judge_model is unconfigured",)))
    assert report.status == hp.BLOCKED
    assert adapter.log == []
    assert "judge_model is unconfigured" in report.reasons


def test_candidate_that_is_serving_on_creation_is_a_remediation_not_a_promotion():
    adapter = FakeAdapter()
    adapter.deploy_makes_serving = True
    report = hp.run_release(adapter, identity())
    assert report.status == hp.REMEDIATION_REQUIRED
    assert "promote" not in adapter.log and "run_evaluation" not in adapter.log
    assert report.serving_before == "2" and report.serving_after == "3"


def test_deploy_failure_leaves_serving_unchanged():
    adapter = FakeAdapter()
    adapter.deploy_error = RuntimeError("build failed")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.NOT_PROMOTED and adapter.serving == "2"
    assert "promote" not in adapter.log


# --- C7: evaluated version == promoted version --------------------------------------------------


def test_promote_is_called_with_exactly_the_evaluated_version():
    adapter = FakeAdapter(new_version="9")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.PROMOTED and report.candidate_version == "9"
    assert [c.version for c in adapter.promoted] == ["9"]
    assert report.serving_before == "2" and report.serving_after == "9"


def test_resolved_candidate_version_mismatch_blocks():
    adapter = FakeAdapter()
    adapter.resolved_version = "4"
    report = hp.run_release(adapter, identity())
    assert report.status == hp.NOT_PROMOTED and "promote" not in adapter.log and adapter.serving == "2"
    assert any("version" in r for r in report.reasons)


def test_evaluation_of_a_different_version_blocks():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(ident, cand, version="2")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.NOT_PROMOTED and adapter.serving == "2" and "promote" not in adapter.log


def test_previously_approved_version_is_still_evaluated():
    adapter = FakeAdapter(serving="3", new_version="3")  # the serving version equals the new candidate
    hp.run_release(adapter, identity())
    assert "run_evaluation" in adapter.log


def test_blank_candidate_version_blocks():
    adapter = FakeAdapter(new_version="")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.NOT_PROMOTED and "promote" not in adapter.log


# --- C8: failed/missing/canceled/timed-out evaluation leaves serving unchanged ------------------


def _blocks(adapter, **kw):
    report = hp.run_release(adapter, identity(**kw))
    assert report.status == hp.NOT_PROMOTED, report
    assert adapter.serving == "2" and adapter.promoted == [] and "promote" not in adapter.log
    assert report.serving_after == "2" and report.reasons
    return report


@pytest.mark.parametrize("status", ["failed", "canceled", "cancelled", "timed_out", "timeout", "in_progress",
                                    "queued", "", "weird"])
def test_non_completed_evaluation_blocks(status):
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(ident, cand, status=status)
    _blocks(adapter)


def test_missing_evaluation_result_blocks():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: None
    _blocks(adapter)


def test_evaluation_exception_blocks_and_keeps_serving():
    adapter = FakeAdapter()
    adapter.eval_error = TimeoutError("poll deadline")
    _blocks(adapter)


def test_empty_pass_counts_block():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(ident, cand, pass_counts={})
    _blocks(adapter)


def test_zero_over_zero_counts_block():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, pass_counts={e: (0, 0) for e in F.EVALUATORS})
    _blocks(adapter)


def test_one_configured_evaluator_missing_from_the_results_blocks():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(ident, cand, pass_counts={"relevance": (10, 10)})
    report = _blocks(adapter)
    assert any("task_adherence" in r for r in report.reasons)


def test_one_evaluator_below_its_threshold_blocks_even_when_the_others_are_perfect():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, pass_counts={"relevance": (10, 10), "task_adherence": (7, 10)})
    report = _blocks(adapter)
    assert any("task_adherence" in r for r in report.reasons)


def test_pass_rate_exactly_at_the_threshold_is_accepted():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, pass_counts={"relevance": (8, 10), "task_adherence": (10, 10)})
    assert hp.run_release(adapter, identity()).status == hp.PROMOTED


def test_pass_rate_just_below_the_threshold_blocks():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, pass_counts={"relevance": (79, 100), "task_adherence": (100, 100)})
    _blocks(adapter, case_count=100)


def test_threshold_comparison_is_exact_where_float_arithmetic_is_not():
    # 7/50 is exactly 0.14; `7 >= 0.14 * 50` is False in binary floating point.
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, pass_counts={"relevance": (7, 50), "task_adherence": (7, 50)})
    assert hp.run_release(adapter, identity(min_pass_rate=0.14, case_count=50)).status == hp.PROMOTED


def test_threshold_of_one_requires_every_case_to_pass():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, pass_counts={"relevance": (9, 10), "task_adherence": (10, 10)})
    _blocks(adapter, min_pass_rate=1.0)


@pytest.mark.parametrize("bad", [(11, 10), (-1, 10), (5, 5), "10/10", None, (True, True), (8.0, 10)])
def test_malformed_or_inconsistent_counts_block(bad):
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, pass_counts={"relevance": bad, "task_adherence": (10, 10)})
    _blocks(adapter)


def test_totals_that_disagree_with_the_dataset_case_count_block():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, pass_counts={e: (3, 3) for e in F.EVALUATORS})
    report = _blocks(adapter)
    assert any("case" in r for r in report.reasons)


def test_extra_unconfigured_evaluators_in_the_result_do_not_hide_a_missing_one():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, pass_counts={"relevance": (10, 10), "fluency": (10, 10)},
        evaluator_set=("relevance", "fluency"))
    _blocks(adapter)


def test_report_carries_per_evaluator_counts_and_is_json_serializable():
    adapter = FakeAdapter()
    report = hp.run_release(adapter, identity())
    data = report.to_dict()
    json.dumps(data)
    assert data.get("status") == hp.PROMOTED and (data.get("evaluator_results") or {}).get("relevance") == [10, 10]
    assert data.get("agent") == "card-helper" and data.get("artifact_id") == "sha256:art"


# --- identity completeness and the rollback target ---------------------------------------------


@pytest.mark.parametrize("over", [
    {"agent": ""}, {"environment": ""}, {"run_id": " "}, {"source_id": ""}, {"artifact_id": ""},
    {"dataset_hash": ""}, {"criteria_hash": ""}, {"evaluators": ()}, {"case_count": 0},
    {"min_pass_rate": 0}, {"min_pass_rate": 1.5}, {"min_pass_rate": True}, {"min_pass_rate": None},
])
def test_incomplete_identity_blocks_before_any_adapter_call(over):
    adapter = FakeAdapter()
    report = hp.run_release(adapter, identity(**over))
    assert report.status == hp.BLOCKED and adapter.log == [] and report.reasons


def test_rolling_back_to_the_candidate_itself_is_not_a_rollback():
    # the serving version already equals the candidate (platform reused a version): there is nothing
    # distinct to restore, so a smoke failure needs a person.
    adapter = FakeAdapter(serving="3", new_version="3")
    adapter.smoke_override = lambda cand: hp.SmokeResult(False, 1, "3")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.REMEDIATION_REQUIRED and adapter.rolled_back == []


def test_unreadable_serving_version_blocks_before_deploy():
    adapter = FakeAdapter()
    adapter.serving_error = RuntimeError("cannot read")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.BLOCKED and "deploy_candidate" not in adapter.log

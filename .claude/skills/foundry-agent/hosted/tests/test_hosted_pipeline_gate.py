"""Evidence correlation, session, router and post-promotion tests (plan Task 7, cases 9-12 of the
approved list) plus the identity/digest helpers. Real code, real files; only `FakeAdapter` is a fake.
Run (PYTHONPATH unset):  python -m pytest hosted/tests/test_hosted_pipeline_gate.py -q
"""

import json

import pytest

import pipeline_fakes as F
from pipeline_fakes import FakeAdapter, hp, identity


def _blocks(adapter, ident=None):
    report = hp.run_release(adapter, ident or identity())
    assert report.status == hp.NOT_PROMOTED, report
    assert adapter.serving == "2" and adapter.promoted == [] and "promote" not in adapter.log
    return report


# --- C9: stale or mismatched evidence blocks ----------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("dataset_hash", "sha256:other"),
    ("artifact_id", "sha256:other"),
    ("source_id", "c" * 40),
    ("run_id", "1000-1"),
    ("criteria_hash", "sha256:stale"),
    ("candidate_name", "someone-else"),
])
def test_mismatched_identity_in_the_evidence_blocks(field, value):
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(ident, cand, **{field: value})
    report = _blocks(adapter)
    assert any(field.split("_")[0] in r for r in report.reasons), report.reasons


def test_evaluator_set_mismatch_blocks_even_if_counts_look_complete():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(ident, cand, evaluator_set=("relevance",))
    report = _blocks(adapter)
    assert any("evaluator" in r for r in report.reasons)


@pytest.mark.parametrize("field", ["dataset_hash", "artifact_id", "source_id", "run_id", "criteria_hash"])
def test_blank_identity_in_the_evidence_blocks(field):
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(ident, cand, **{field: ""})
    _blocks(adapter)


def test_candidate_built_from_a_different_artifact_blocks_even_if_the_evidence_claims_the_right_one():
    # the evaluation evidence names the released artifact, but the platform created the candidate from
    # something else: only the candidate-vs-release comparison can catch this.
    adapter = FakeAdapter(artifact="sha256:not-what-was-built")
    report = _blocks(adapter)
    assert any("candidate artifact" in r for r in report.reasons)


def test_matching_identities_promote():
    adapter = FakeAdapter()
    assert hp.run_release(adapter, identity()).status == hp.PROMOTED


# --- identity helpers: what must invalidate evidence ---------------------------------------------


def _crit(**over):
    values = dict(dataset_hash="sha256:d", evaluators=("a", "b"), min_pass_rate=0.8,
                  judge_model="j", tool_schema_hash="sha256:t", pipeline_digest="sha256:p")
    values.update(over)
    return hp.criteria_hash(**values)


def test_criteria_hash_is_stable_and_order_insensitive_for_evaluators():
    assert _crit() == _crit() == _crit(evaluators=("b", "a"))
    assert _crit().startswith("sha256:") and len(_crit()) > len("sha256:") + 10


@pytest.mark.parametrize("over", [
    {"dataset_hash": "sha256:e"}, {"evaluators": ("a",)}, {"evaluators": ("a", "b", "c")},
    {"min_pass_rate": 0.9}, {"judge_model": "k"}, {"tool_schema_hash": "sha256:u"},
    {"pipeline_digest": "sha256:q"},
])
def test_each_criterion_change_invalidates_the_criteria_hash(over):
    assert _crit(**over) != _crit()


def test_dataset_hash_tracks_content_not_path(tmp_path):
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("q1\nq2\n", encoding="utf-8")
    b.write_text("q1\nq2\n", encoding="utf-8")
    assert hp.dataset_hash(a) == hp.dataset_hash(b) and hp.dataset_hash(a).startswith("sha256:")
    b.write_text("q1\nq3\n", encoding="utf-8")
    assert hp.dataset_hash(a) != hp.dataset_hash(b)


def test_dataset_hash_of_a_missing_or_empty_file_is_an_error(tmp_path):
    with pytest.raises(hp.ConfigError):
        hp.dataset_hash(tmp_path / "nope.yaml")
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    with pytest.raises(hp.ConfigError):
        hp.dataset_hash(tmp_path / "empty.yaml")


def test_artifact_digest_changes_with_source_dependencies_and_instructions(tmp_path):
    root = F.make_agent_root(tmp_path)
    base = hp.artifact_digest(root)
    assert base == hp.artifact_digest(root) and base.startswith("sha256:")
    for name, text in (("agent.py", "x = 1\n"), ("requirements.txt", "other==1\n"),
                       ("instructions.md", "different\n"), ("main.py", "y = 2\n")):
        old = (root / name).read_text(encoding="utf-8")
        (root / name).write_text(text, encoding="utf-8")
        assert hp.artifact_digest(root) != base, name
        (root / name).write_text(old, encoding="utf-8")
    assert hp.artifact_digest(root) == base


def test_artifact_digest_ignores_evals_pipeline_docs_and_caches(tmp_path):
    root = F.make_agent_root(tmp_path)
    (root / "evals" / "run_evaluation.py").write_text("x = 1", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "helper.py").write_text("x = 1", encoding="utf-8")
    base = hp.artifact_digest(root)
    assert base.startswith("sha256:")
    (root / "evals" / "run_evaluation.py").write_text("x = 2", encoding="utf-8")
    (root / "docs" / "helper.py").write_text("x = 2", encoding="utf-8")
    (root / "evals" / "evals.yaml").write_text("changed\n", encoding="utf-8")
    (root / "pipeline" / "agent-deploy.yml").write_text("changed\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "agent.cpython-313.pyc").write_bytes(b"\x00\x01")
    assert hp.artifact_digest(root) == base


def test_artifact_digest_of_an_empty_project_is_an_error(tmp_path):
    with pytest.raises(hp.ConfigError):
        hp.artifact_digest(tmp_path)


def test_pipeline_digest_tracks_every_pipeline_file(tmp_path):
    root = F.make_agent_root(tmp_path)
    base = hp.pipeline_digest(root / "pipeline")
    (root / "pipeline" / "agent-deploy.yml").write_text("# changed\n", encoding="utf-8")
    assert hp.pipeline_digest(root / "pipeline") != base


# --- C10: session manager enablement and continuity are required --------------------------------


@pytest.mark.parametrize("session", [
    lambda c: F.good_session(c, manager_enabled=False),
    lambda c: F.good_session(c, manager_enabled=None),
    lambda c: F.good_session(c, continuity_passed=False),
    lambda c: F.good_session(c, continuity_passed=None),
    lambda c: F.good_session(c, manager_enabled=False, continuity_passed=False),
    lambda c: F.good_session(c, version="2"),
    lambda c: F.good_session(c, candidate_name="someone-else"),
    lambda c: None,
], ids=["disabled", "enablement-unknown", "continuity-failed", "continuity-unknown", "both-bad",
        "wrong-version", "wrong-agent", "missing"])
def test_session_evidence_must_be_enabled_and_continuous_for_this_candidate(session):
    adapter = FakeAdapter()
    adapter.session_override = session
    report = _blocks(adapter)
    assert any("session" in r for r in report.reasons)


def test_session_check_exception_blocks():
    adapter = FakeAdapter()
    adapter.session_error = RuntimeError("probe failed")
    _blocks(adapter)


def test_session_check_runs_before_promotion_and_good_evidence_promotes():
    adapter = FakeAdapter()
    assert hp.run_release(adapter, identity()).status == hp.PROMOTED
    assert "check_session" in adapter.log
    assert adapter.log.index("check_session") < adapter.log.index("promote")


# --- C11: a router release needs candidate-scoped route evidence --------------------------------


def test_router_release_is_blocked_when_the_adapter_has_no_route_evidence_capability():
    adapter = FakeAdapter(caps=hp.Capabilities(True, True, route_evidence=False))
    report = hp.run_release(adapter, identity(router=True))
    assert report.status == hp.BLOCKED and "deploy_candidate" not in adapter.log
    assert any("route evidence" in r for r in report.reasons)


def test_the_same_adapter_without_route_evidence_can_release_a_single_agent():
    adapter = FakeAdapter(caps=hp.Capabilities(True, True, route_evidence=False))
    assert hp.run_release(adapter, identity(router=False)).status == hp.PROMOTED


@pytest.mark.parametrize("route", [
    None,
    lambda i, c: F.good_route(i, c, scope="local"),
    lambda i, c: F.good_route(i, c, scope=""),
    lambda i, c: F.good_route(i, c, version="2"),
    lambda i, c: F.good_route(i, c, candidate_name="someone-else"),
    lambda i, c: F.good_route(i, c, cases_correct=9),
    lambda i, c: F.good_route(i, c, cases_total=9, cases_correct=9),
    lambda i, c: F.good_route(i, c, cases_total=0, cases_correct=0),
], ids=["absent", "local-scope", "blank-scope", "wrong-version", "wrong-agent", "a-route-wrong",
        "wrong-case-count", "zero-cases"])
def test_router_release_blocks_on_missing_local_or_mismatched_route_evidence(route):
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(
        ident, cand, route_evidence=route(ident, cand) if route else None)
    report = _blocks(adapter, identity(router=True))
    assert any("route" in r for r in report.reasons)


def test_router_release_with_complete_candidate_scoped_evidence_promotes():
    adapter = FakeAdapter()
    assert hp.run_release(adapter, identity(router=True)).status == hp.PROMOTED


def test_good_quality_scores_alone_cannot_pass_a_router():
    adapter = FakeAdapter()
    adapter.eval_override = lambda ident, cand: F.good_eval(ident, cand, route_evidence=None)
    assert hp.run_release(adapter, identity(router=True)).status == hp.NOT_PROMOTED


# --- C12: post-promotion smoke failure fails the run and reports rollback status ----------------


def test_smoke_failure_rolls_back_to_the_previous_version_and_never_reports_success():
    adapter = FakeAdapter()
    adapter.smoke_override = lambda cand: hp.SmokeResult(False, 2, "3", "check 2 failed")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.ROLLED_BACK and report.rollback == "rolled-back"
    assert adapter.rolled_back == [("card-helper", "2")] and adapter.serving == "2"
    assert report.serving_before == "2" and report.serving_after == "2"
    assert hp.EXIT_CODES[report.status] != 0


def test_smoke_exception_is_a_smoke_failure():
    adapter = FakeAdapter()
    adapter.smoke_error = RuntimeError("endpoint down")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.ROLLED_BACK and adapter.serving == "2"


def test_smoke_failure_with_no_previous_version_requires_remediation():
    adapter = FakeAdapter(serving=None)
    adapter.smoke_override = lambda cand: hp.SmokeResult(False, 1, "3")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.REMEDIATION_REQUIRED and report.rollback == "remediation-required"
    assert adapter.rolled_back == [] and hp.EXIT_CODES[report.status] != 0


def test_a_rollback_that_raises_requires_remediation():
    adapter = FakeAdapter()
    adapter.smoke_override = lambda cand: hp.SmokeResult(False, 1, "3")
    adapter.rollback_error = RuntimeError("cannot revert")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.REMEDIATION_REQUIRED and hp.EXIT_CODES[report.status] != 0


def test_a_rollback_that_does_not_restore_the_previous_version_requires_remediation():
    adapter = FakeAdapter()
    adapter.smoke_override = lambda cand: hp.SmokeResult(False, 1, "3")
    adapter.rollback_noop = True
    report = hp.run_release(adapter, identity())
    assert report.status == hp.REMEDIATION_REQUIRED and report.serving_after == "3"


def test_smoke_of_a_different_version_than_promoted_is_a_failure():
    adapter = FakeAdapter()
    adapter.smoke_override = lambda cand: hp.SmokeResult(True, 2, "2")
    assert hp.run_release(adapter, identity()).status == hp.ROLLED_BACK


def test_smoke_with_zero_checks_is_a_failure():
    adapter = FakeAdapter()
    adapter.smoke_override = lambda cand: hp.SmokeResult(True, 0, "3")
    assert hp.run_release(adapter, identity()).status == hp.ROLLED_BACK


def test_promote_that_raises_after_moving_serving_is_rolled_back():
    adapter = FakeAdapter()
    adapter.promote_error = RuntimeError("timeout after switch")
    adapter.promote_moves_then_raises = True
    report = hp.run_release(adapter, identity())
    assert report.status == hp.ROLLED_BACK and adapter.serving == "2"


def test_promote_that_raises_without_moving_serving_leaves_it_unchanged():
    adapter = FakeAdapter()
    adapter.promote_error = RuntimeError("denied")
    report = hp.run_release(adapter, identity())
    assert report.status == hp.NOT_PROMOTED and adapter.serving == "2" and adapter.rolled_back == []


def test_promote_that_reports_success_but_does_not_move_serving_is_not_a_success():
    adapter = FakeAdapter()
    adapter.promote_noop = True
    report = hp.run_release(adapter, identity())
    assert report.status == hp.NOT_PROMOTED and report.serving_after == "2"
    assert "smoke" not in adapter.log, "a smoke test must never run against a serving target that did not move"
    assert any("serving" in r for r in report.reasons)


def test_smoke_runs_only_after_promotion():
    adapter = FakeAdapter()
    hp.run_release(adapter, identity())
    assert "smoke" in adapter.log
    assert adapter.log.index("promote") < adapter.log.index("smoke")


def test_exit_codes_are_distinct_and_only_promoted_is_zero():
    codes = hp.EXIT_CODES
    assert set(codes) == {hp.PROMOTED, hp.BLOCKED, hp.NOT_PROMOTED, hp.ROLLED_BACK, hp.REMEDIATION_REQUIRED}
    assert codes[hp.PROMOTED] == 0
    assert len({v for k, v in codes.items()}) == 5 and all(v != 0 for k, v in codes.items() if k != hp.PROMOTED)


def test_report_contains_identities_but_no_raw_text():
    adapter = FakeAdapter()
    adapter.smoke_override = lambda cand: hp.SmokeResult(False, 1, "3", "user said: my card number is 4111")
    text = json.dumps(hp.run_release(adapter, identity()).to_dict())
    assert "4111" not in text and "sha256:art" in text and "1001-1" in text

"""Tests for run_release_evaluation.py: the fail-closed hosted release gate (plan Task 3b, cases 7-9, 51-52).

Real config loading, request building and scoring over real files. The only fakes are hand-written
stand-ins for the external Foundry evaluation service (an EvalClient, and an OpenAI-style client under
FoundryEvalClient). Nothing here contacts Azure; a pass here is evidence about the gate logic, not about
any deployed agent.
Run from the evals/ folder with the declared requirements installed, PYTHONPATH unset:
    python -m pytest tests/test_release_evaluation.py -q
"""

import copy
import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

EVALS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EVALS_DIR))

import run_release_evaluation as gate  # noqa: E402

ROWS = [{"case_id": f"c{i}", "query": f"Question number {i}?"} for i in range(1, 6)]
BASE = {
    "agent": "card-helper", "pattern": "single-agent", "judge_model": "judge-deploy", "min_pass_rate": 0.8,
    "evaluators": ["relevance", "coherence"], "dataset_status": "approved",
    "dataset": ROWS, "smoke": ["c1", "c2"],
}


def write_config(tmp_path, mutate=None, **over):
    data = copy.deepcopy(BASE)
    data.update(over)
    if mutate:
        mutate(data)
    path = tmp_path / "evals.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeClient:
    """Hand-written stand-in for the evaluation service. `respond(request)` returns the snapshots to serve."""

    def __init__(self, respond, start_error=None, poll_error=None):
        self.respond, self.start_error, self.poll_error = respond, start_error, poll_error
        self.started, self.cancelled, self._snaps, self.polls = [], [], [], 0

    def start(self, request):
        if self.start_error:
            raise self.start_error
        self.started.append(request)
        self._snaps = list(self.respond(request))
        return gate.RunRef("eval-1", f"run-{len(self.started)}")

    def poll(self, ref):
        self.polls += 1
        if self.poll_error:
            raise self.poll_error
        return self._snaps.pop(0) if len(self._snaps) > 1 else self._snaps[0]

    def cancel(self, ref):
        self.cancelled.append(ref)


def good(request, **over):
    n = len(request.items)
    snap = gate.RunSnapshot(
        status="completed", candidate_name=request.agent_name, candidate_version=request.agent_version,
        dataset_sha256=request.dataset_sha256, evaluators=tuple(request.evaluators),
        criteria=tuple(gate.CriterionResult(e, n, 0) for e in request.evaluators), errored=0,
        report_url="https://example.invalid/report", error=None,
    )
    return dataclasses.replace(snap, **over)


def run(tmp_path, respond, *, config=None, version="3", client=None, deadline=100, poll=10, **cfg_over):
    config = config or gate.load_config(write_config(tmp_path, **cfg_over))
    client = client or FakeClient(respond)
    clock = Clock()
    result = gate.run_release_gate(client, config, agent_name="card-helper", agent_version=version,
                                   deadline_seconds=deadline, poll_seconds=poll, clock=clock, sleep=clock.sleep)
    return result, client


def serving(*overs):
    """Respond with good snapshots that are overridden one after another."""
    return lambda request: [good(request, **o) for o in overs] or [good(request)]


# --- case 7: empty / missing / 0-0 / failed / canceled / timed out all fail closed ------------------


def test_a_clean_run_passes_and_records_candidate_and_dataset_identity(tmp_path):
    result, client = run(tmp_path, serving({}))
    assert (result.exit_code, result.verdict) == (0, "pass"), result.reasons
    ident = result.identity
    assert set(ident) >= {"agent_name", "agent_version", "dataset_sha256", "dataset_source", "evaluators",
                          "judge_model", "run_id"}, ident
    assert ident["agent_name"] == "card-helper" and ident["agent_version"] == "3"
    assert ident["dataset_sha256"] == client.started[0].dataset_sha256 and len(ident["dataset_sha256"]) == 64
    assert ident["dataset_source"].endswith("#baseline") and ident["evaluators"] == ["coherence", "relevance"]
    assert ident["judge_model"] == "judge-deploy" and ident["run_id"] == "run-1"


def test_empty_results_block_instead_of_passing_vacuously(tmp_path):
    result, _ = run(tmp_path, lambda r: [good(r, criteria=())])
    assert (result.exit_code, result.verdict) == (2, "blocked")
    assert any("relevance" in why and "no result" in why for why in result.reasons)


def test_a_configured_evaluator_missing_from_results_blocks(tmp_path):
    result, _ = run(tmp_path, lambda r: [good(r, criteria=(gate.CriterionResult("relevance", 5, 0),))])
    assert result.exit_code == 2 and any("coherence" in why for why in result.reasons)


def test_an_evaluator_reporting_zero_of_zero_is_no_result_not_zero_percent(tmp_path):
    zero = (gate.CriterionResult("relevance", 5, 0), gate.CriterionResult("coherence", 0, 0))
    result, _ = run(tmp_path, lambda r: [good(r, criteria=zero)])
    assert result.exit_code == 2, "0/0 must be missing evidence (exit 2), not a 0% behavioural failure (exit 1)"
    assert any("coherence" in why and "no result" in why for why in result.reasons)


@pytest.mark.parametrize("status", ["failed", "canceled", "cancelled", "queued", "in_progress", "", "weird"])
def test_a_run_that_did_not_complete_blocks_even_with_passing_scores(tmp_path, status):
    result, _ = run(tmp_path, serving({"status": status}))
    assert result.exit_code == 2 and any("status" in why for why in result.reasons)


def test_a_service_error_on_a_completed_run_blocks(tmp_path):
    result, _ = run(tmp_path, serving({"error": "judge deployment not found"}))
    assert result.exit_code == 2 and any("judge deployment not found" in why for why in result.reasons)


def test_a_run_that_never_finishes_times_out_blocks_and_is_cancelled(tmp_path):
    result, client = run(tmp_path, serving({"status": "in_progress"}), deadline=100, poll=10)
    assert result.exit_code == 2 and any("timed out" in why for why in result.reasons)
    assert len(client.cancelled) == 1, "a timed-out run must not be left running"


def test_polling_continues_until_completion_within_the_deadline(tmp_path):
    result, client = run(tmp_path, serving({"status": "queued"}, {"status": "in_progress"}, {}), deadline=100)
    assert result.exit_code == 0 and client.cancelled == []
    assert client.polls == 3, "must keep polling until the terminal state, not stop early"


def test_items_that_errored_in_the_service_block(tmp_path):
    result, _ = run(tmp_path, serving({"errored": 1}))
    assert result.exit_code == 2 and any("errored" in why for why in result.reasons)


def test_partially_scored_evaluator_blocks_even_if_its_scored_part_passes(tmp_path):
    partial = (gate.CriterionResult("relevance", 5, 0), gate.CriterionResult("coherence", 3, 0))
    result, _ = run(tmp_path, lambda r: [good(r, criteria=partial)])
    assert result.exit_code == 2 and any("coherence" in why and "3 of 5" in why for why in result.reasons)


def test_negative_or_duplicate_result_entries_block(tmp_path):
    neg = (gate.CriterionResult("relevance", -1, 0), gate.CriterionResult("coherence", 5, 0))
    dup = (gate.CriterionResult("relevance", 5, 0), gate.CriterionResult("relevance", 5, 0),
           gate.CriterionResult("coherence", 5, 0))
    for crit in (neg, dup):
        result, _ = run(tmp_path, lambda r, c=crit: [good(r, criteria=c)])
        assert result.exit_code == 2


def test_start_failure_is_invalid_not_a_pass_or_a_behavioural_failure(tmp_path):
    result, _ = run(tmp_path, serving({}), client=FakeClient(serving({}), start_error=RuntimeError("503 from service")))
    assert result.exit_code == 2 and any("RuntimeError" in why and "503" in why for why in result.reasons)


def test_poll_failure_blocks_and_attempts_to_cancel(tmp_path):
    client = FakeClient(serving({}), poll_error=ConnectionError("reset"))
    result, client = run(tmp_path, None, client=client)
    assert result.exit_code == 2 and len(client.cancelled) == 1


# --- case 8: each configured evaluator meets its own threshold; extras never count -------------------


def test_every_configured_evaluator_must_meet_its_own_threshold_not_the_average(tmp_path):
    mixed = (gate.CriterionResult("relevance", 5, 0), gate.CriterionResult("coherence", 3, 2))  # overall 8/10
    result, _ = run(tmp_path, lambda r: [good(r, criteria=mixed)])
    assert (result.exit_code, result.verdict) == (1, "fail")
    assert any("coherence" in why for why in result.reasons) and not any("relevance" in why for why in result.reasons)
    assert result.scores["relevance"]["passed"] == 5 and result.scores["coherence"]["rate"] == 0.6


@pytest.mark.parametrize("passed,failed,code", [(4, 1, 0), (3, 2, 1), (5, 0, 0), (0, 5, 1)])
def test_threshold_boundary_is_inclusive_and_exact(tmp_path, passed, failed, code):
    crit = (gate.CriterionResult("relevance", 5, 0), gate.CriterionResult("coherence", passed, failed))
    result, _ = run(tmp_path, lambda r: [good(r, criteria=crit)])
    assert result.exit_code == code  # 0.8 threshold: 4/5 passes exactly, 3/5 does not
    assert "coherence" in result.scores, "scores must be reported per configured evaluator"
    assert result.scores["coherence"] == {"passed": passed, "total": 5, "rate": passed / 5, "threshold": 0.8}


def test_decimal_thresholds_are_compared_exactly(tmp_path):
    rows = [{"case_id": f"c{i}", "query": f"q{i}"} for i in range(10)]
    crit = (gate.CriterionResult("relevance", 7, 3), gate.CriterionResult("coherence", 10, 0))
    result, _ = run(tmp_path, lambda r: [good(r, criteria=crit)], dataset=rows, min_pass_rate=0.7)
    assert result.exit_code == 0, result.reasons
    assert "relevance" in result.scores, "scores must be reported per configured evaluator"
    assert result.scores["relevance"]["passed"] == 7 and result.scores["relevance"]["threshold"] == 0.7


def test_an_extra_unconfigured_evaluator_cannot_rescue_a_failing_configured_one(tmp_path):
    crit = (gate.CriterionResult("relevance", 1, 4), gate.CriterionResult("coherence", 5, 0),
            gate.CriterionResult("fluency", 5, 0))
    result, _ = run(tmp_path, lambda r: [good(r, criteria=crit)])
    assert result.exit_code == 1 and any("relevance" in why for why in result.reasons)
    assert "fluency" not in result.scores, "an unconfigured evaluator must not appear among the gated scores"


def test_an_extra_evaluator_cannot_stand_in_for_a_missing_configured_one(tmp_path):
    crit = (gate.CriterionResult("relevance", 5, 0), gate.CriterionResult("fluency", 5, 0))
    result, _ = run(tmp_path, lambda r: [good(r, criteria=crit)])
    assert result.exit_code == 2 and any("coherence" in why for why in result.reasons)


def test_a_failing_extra_evaluator_does_not_fail_a_run_that_meets_every_configured_threshold(tmp_path):
    crit = (gate.CriterionResult("relevance", 5, 0), gate.CriterionResult("coherence", 5, 0),
            gate.CriterionResult("fluency", 0, 5))
    result, _ = run(tmp_path, lambda r: [good(r, criteria=crit)])
    assert result.exit_code == 0, result.reasons
    assert set(result.scores) == {"relevance", "coherence"}, "only configured evaluators are gated"


# --- case 9: stale or wrong evidence is rejected -------------------------------------------------------


@pytest.mark.parametrize("field,value,word", [
    ("candidate_name", "other-agent", "name"), ("candidate_version", "2", "version"),
    ("candidate_version", None, "version"), ("candidate_name", None, "name"),
    ("dataset_sha256", "0" * 64, "dataset"), ("dataset_sha256", None, "dataset"),
])
def test_evidence_for_a_different_candidate_or_dataset_is_rejected(tmp_path, field, value, word):
    result, _ = run(tmp_path, serving({field: value}))
    assert (result.exit_code, result.verdict) == (2, "blocked")
    assert any(word in why for why in result.reasons), result.reasons


@pytest.mark.parametrize("reported", [("relevance",), ("relevance", "coherence", "fluency"), ("relevance", "fluency"), ()])
def test_evidence_from_a_different_evaluator_set_is_rejected(tmp_path, reported):
    result, _ = run(tmp_path, serving({"evaluators": reported}))
    assert result.exit_code == 2 and any("evaluator set" in why for why in result.reasons)


def test_a_version_that_was_evaluated_before_is_evaluated_again(tmp_path):
    config = gate.load_config(write_config(tmp_path))
    client = FakeClient(serving({}))
    for _ in range(2):
        clock = Clock()
        res = gate.run_release_gate(client, config, agent_name="card-helper", agent_version="3",
                                    deadline_seconds=50, poll_seconds=5, clock=clock, sleep=clock.sleep)
        assert res.exit_code == 0
    assert [r.agent_version for r in client.started] == ["3", "3"], "nothing may be skipped as already approved"


@pytest.mark.parametrize("version", ["", "  ", "latest", "Latest", "current", "1 2", None])
def test_candidate_version_must_be_explicit(tmp_path, version):
    client = FakeClient(serving({}))
    result, _ = run(tmp_path, None, version=version, client=client)
    assert result.exit_code == 2 and client.started == []


def test_candidate_name_must_be_explicit(tmp_path):
    client = FakeClient(serving({}))
    clock = Clock()
    res = gate.run_release_gate(client, gate.load_config(write_config(tmp_path)), agent_name=" ", agent_version="1",
                                clock=clock, sleep=clock.sleep)
    assert res.exit_code == 2 and client.started == []


# --- release evidence for routers --------------------------------------------------------------------


def test_router_quality_scores_alone_never_release_a_router(tmp_path):
    result, _ = run(tmp_path, serving({}), pattern="router-specialists")
    assert result.exit_code == 2 and any("route evidence" in why for why in result.reasons)


def test_router_with_failing_quality_is_still_a_behavioural_failure(tmp_path):
    crit = (gate.CriterionResult("relevance", 1, 4), gate.CriterionResult("coherence", 5, 0))
    result, _ = run(tmp_path, lambda r: [good(r, criteria=crit)], pattern="router-specialists")
    assert result.exit_code == 1


# --- config: unconfigured / unsupported / inconsistent values block ----------------------------------


def judge_null(d): d["judge_model"] = None
def judge_blank(d): d["judge_model"] = "  "
def rate_null(d): d["min_pass_rate"] = None
def rate_zero(d): d["min_pass_rate"] = 0
def rate_high(d): d["min_pass_rate"] = 1.5
def rate_bool(d): d["min_pass_rate"] = True
def rate_text(d): d["min_pass_rate"] = "0.8"
def evaluators_empty(d): d["evaluators"] = []
def evaluator_unknown(d): d["evaluators"] = ["relevance", "tool_call_accuracy"]
def evaluator_dup(d): d["evaluators"] = ["relevance", "relevance"]
def status_proposed(d): d["dataset_status"] = "proposed"
def dataset_empty(d): d["dataset"] = []
def dataset_small(d): d["dataset"] = d["dataset"][:2]
def dataset_dup_id(d): d["dataset"][1]["case_id"] = "c1"
def dataset_blank_query(d): d["dataset"][0]["query"] = "  "
def pattern_unknown(d): d["pattern"] = "supervisor-worker"
def smoke_unknown_id(d): d["smoke"] = ["c1", "nope"]
def groundedness_without_context(d): d["evaluators"] = ["relevance", "groundedness"]


@pytest.mark.parametrize("fn", [
    judge_null, judge_blank, rate_null, rate_zero, rate_high, rate_bool, rate_text, evaluators_empty,
    evaluator_unknown, evaluator_dup, status_proposed, dataset_empty, dataset_small, dataset_dup_id,
    dataset_blank_query, pattern_unknown, smoke_unknown_id, groundedness_without_context], ids=lambda f: f.__name__)
def test_unconfigured_or_inconsistent_config_blocks(tmp_path, fn):
    with pytest.raises(gate.ConfigError):
        gate.load_config(write_config(tmp_path, mutate=fn))


def test_groundedness_is_allowed_only_when_every_item_carries_approved_context(tmp_path):
    def with_context(d):
        d["evaluators"] = ["groundedness"]
        for row in d["dataset"]:
            row["context"] = "approved policy excerpt"
    config = gate.load_config(write_config(tmp_path, mutate=with_context))
    request = gate.build_request(config, "card-helper", "1")
    assert [c["name"] for c in request.criteria] == ["groundedness"]
    assert request.criteria[0]["data_mapping"].get("context") == "{{item.context}}"
    assert request.items and all(item.get("context") == "approved policy excerpt" for item in request.items)


@pytest.mark.parametrize("text", ["", "- just\n- a list\n", "key: [unclosed", ": :"])
def test_unreadable_or_malformed_config_blocks(tmp_path, text):
    path = tmp_path / "evals.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(gate.ConfigError):
        gate.load_config(path)


def test_missing_config_file_blocks(tmp_path):
    with pytest.raises(gate.ConfigError):
        gate.load_config(tmp_path / "absent.yaml")


def test_smoke_dataset_selects_only_the_smoke_cases_and_records_its_source(tmp_path):
    config = gate.load_config(write_config(tmp_path), dataset="smoke")
    request = gate.build_request(config, "card-helper", "1")
    assert [i["case_id"] for i in request.items] == ["c1", "c2"] and request.dataset_source.endswith("#smoke")
    full = gate.build_request(gate.load_config(write_config(tmp_path)), "card-helper", "1")
    assert full.dataset_sha256 != request.dataset_sha256 and len(full.items) == 5


def test_unknown_dataset_name_blocks(tmp_path):
    with pytest.raises(gate.ConfigError):
        gate.load_config(write_config(tmp_path), dataset="nightly")


def test_dataset_hash_tracks_content_and_ignores_key_order(tmp_path):
    a = gate.build_request(gate.load_config(write_config(tmp_path)), "card-helper", "1")
    b = gate.build_request(gate.load_config(write_config(tmp_path)), "card-helper", "2")
    assert a.dataset_sha256 == b.dataset_sha256, "same dataset, different candidate"

    def edit(d):
        d["dataset"][4]["query"] = "Question number 5!"
    c = gate.build_request(gate.load_config(write_config(tmp_path, mutate=edit)), "card-helper", "1")
    assert c.dataset_sha256 != a.dataset_sha256
    assert gate.dataset_sha256([{"b": 1, "a": 2}]) == gate.dataset_sha256([{"a": 2, "b": 1}])


def test_request_carries_a_judge_criterion_per_evaluator_and_hides_unrelated_fields(tmp_path):
    def more(d):
        d["dataset"][0]["expected_route"] = "billing"
        d["dataset"][0]["category"] = "x"
    request = gate.build_request(gate.load_config(write_config(tmp_path, mutate=more)), "card-helper", "7")
    assert [c["name"] for c in request.criteria] == ["relevance", "coherence"]
    assert all(c["initialization_parameters"] == {gate.JUDGE_PARAM: "judge-deploy"} for c in request.criteria)
    assert all(set(item) <= {"case_id", "query", "context"} for item in request.items)
    assert request.metadata["agent_version"] == "7" and request.metadata["dataset_sha256"] == request.dataset_sha256


# --- FoundryEvalClient against a hand-written OpenAI-style fake -----------------------------------


class FakeRuns:
    def __init__(self, owner):
        self.owner = owner

    def create(self, eval_id, *, data_source, metadata=None, name=None):
        self.owner.run_calls.append({"eval_id": eval_id, "data_source": data_source, "metadata": metadata, "name": name})
        return SimpleNamespace(id="run-9")

    def retrieve(self, run_id, *, eval_id):
        self.owner.retrieved.append((run_id, eval_id))
        return self.owner.run

    def cancel(self, run_id, *, eval_id):
        self.owner.cancelled.append((run_id, eval_id))


class FakeEvals:
    def __init__(self):
        self.created, self.run_calls, self.retrieved, self.cancelled = [], [], [], []
        self.criteria_names = ["relevance", "coherence"]
        self.run = None
        self.runs = FakeRuns(self)

    def create(self, *, name, data_source_config, testing_criteria, metadata=None):
        self.created.append({"name": name, "config": data_source_config, "criteria": list(testing_criteria),
                             "metadata": metadata})
        return SimpleNamespace(id="eval-9")

    def retrieve(self, eval_id):
        return SimpleNamespace(id=eval_id, testing_criteria=[SimpleNamespace(name=n) for n in self.criteria_names])


class FakeOpenAI:
    def __init__(self):
        self.evals = FakeEvals()


def fake_run(request, *, version=None, name=None, counts=((5, 0), (5, 0)), status="completed", error=None):
    return SimpleNamespace(
        status=status, error=error, report_url="https://example.invalid/r/9",
        data_source=SimpleNamespace(target=SimpleNamespace(name=name or request.agent_name,
                                                          version=version or request.agent_version)),
        metadata={"dataset_sha256": request.dataset_sha256},
        per_testing_criteria_results=[SimpleNamespace(testing_criteria=n, passed=p, failed=f)
                                      for n, (p, f) in zip(request.evaluators, counts)],
        result_counts=SimpleNamespace(errored=0),
    )


def test_foundry_client_creates_the_evaluation_for_the_exact_candidate(tmp_path):
    request = gate.build_request(gate.load_config(write_config(tmp_path)), "card-helper", "3")
    oai = FakeOpenAI()
    ref = gate.FoundryEvalClient(oai).start(request)
    assert (ref.eval_id, ref.run_id) == ("eval-9", "run-9")
    created, call = oai.evals.created[0], oai.evals.run_calls[0]
    assert created["config"]["type"] == "custom" and created["config"]["item_schema"]["required"] == ["query"]
    assert call["data_source"]["type"] == "azure_ai_target_completions"
    assert call["data_source"]["target"] == {"type": "azure_ai_agent", "name": "card-helper", "version": "3"}
    content = call["data_source"]["source"]["content"]
    assert [c["item"]["case_id"] for c in content] == ["c1", "c2", "c3", "c4", "c5"]
    assert call["metadata"]["dataset_sha256"] == request.dataset_sha256 and call["metadata"]["agent_version"] == "3"
    assert all(len(v) <= 512 for v in call["metadata"].values()) and len(call["metadata"]) <= 16


def test_every_criterion_has_the_keys_the_installed_sdk_declares():
    models = pytest.importorskip("azure.ai.projects.models")
    td = models.TestingCriterionAzureAIEvaluator
    for name, entry in gate.SUPPORTED_EVALUATORS.items():
        crit = gate.build_criteria([name], "judge-deploy")[0]
        assert td.__required_keys__ <= set(crit) <= set(td.__annotations__), name
        assert crit["type"] == "azure_ai_evaluator" and crit["evaluator_name"] == f"builtin.{name}"
    assert set(gate.SUPPORTED_EVALUATORS) == {"intent_resolution", "task_adherence", "relevance", "coherence",
                                                "fluency", "groundedness"}


def test_foundry_client_reads_identity_scores_and_report_from_the_service_run(tmp_path):
    request = gate.build_request(gate.load_config(write_config(tmp_path)), "card-helper", "3")
    oai = FakeOpenAI()
    oai.evals.run = fake_run(request)
    client = gate.FoundryEvalClient(oai)
    snap = client.poll(client.start(request))
    assert (snap.status, snap.candidate_name, snap.candidate_version) == ("completed", "card-helper", "3")
    assert snap.dataset_sha256 == request.dataset_sha256 and set(snap.evaluators) == {"relevance", "coherence"}
    assert {c.name: (c.passed, c.failed) for c in snap.criteria} == {"relevance": (5, 0), "coherence": (5, 0)}
    assert snap.report_url == "https://example.invalid/r/9" and snap.errored == 0


def test_end_to_end_through_the_foundry_client_passes_and_rejects_a_version_mismatch(tmp_path):
    config = gate.load_config(write_config(tmp_path))
    request = gate.build_request(config, "card-helper", "3")
    for served_version, code in (("3", 0), ("2", 2)):
        oai = FakeOpenAI()
        oai.evals.run = fake_run(request, version=served_version)
        clock = Clock()
        res = gate.run_release_gate(gate.FoundryEvalClient(oai), config, agent_name="card-helper",
                                    agent_version="3", clock=clock, sleep=clock.sleep)
        assert res.exit_code == code, res.reasons


def test_foundry_client_cancel_targets_the_started_run(tmp_path):
    request = gate.build_request(gate.load_config(write_config(tmp_path)), "card-helper", "3")
    oai = FakeOpenAI()
    client = gate.FoundryEvalClient(oai)
    client.cancel(client.start(request))
    assert oai.evals.cancelled == [("run-9", "eval-9")]


# --- main(): exit codes, evidence file, no raw prompts ----------------------------------------------


def cli(tmp_path, args, client=None, environ=None):
    cfg = write_config(tmp_path)
    clock = Clock()
    code = gate.main(["--config", str(cfg), "--agent-name", "card-helper", *args], client=client,
                     clock=clock, sleep=clock.sleep, environ=environ or {})
    return code


def test_main_exit_codes_zero_one_two(tmp_path, capsys):
    assert cli(tmp_path, ["--agent-version", "3"], FakeClient(serving({}))) == 0
    printed = capsys.readouterr().out
    assert printed.strip(), "main must print the verdict as JSON"
    out = json.loads(printed)
    assert out["verdict"] == "pass" and out["scope"] == "hosted-evaluation-service"
    crit = (gate.CriterionResult("relevance", 1, 4), gate.CriterionResult("coherence", 5, 0))
    assert cli(tmp_path, ["--agent-version", "3"], FakeClient(lambda r: [good(r, criteria=crit)])) == 1
    assert cli(tmp_path, ["--agent-version", "3"], FakeClient(serving({"status": "failed"}))) == 2
    assert cli(tmp_path, ["--agent-version", "latest"], FakeClient(serving({}))) == 2


def test_main_missing_args_or_endpoint_is_invalid_not_a_crash(tmp_path, capsys):
    assert cli(tmp_path, []) == 2, "agent version is required"
    assert cli(tmp_path, ["--agent-version", "3"], client=None, environ={}) == 2
    assert "FOUNDRY_PROJECT_ENDPOINT" in capsys.readouterr().err


def test_main_unconfigured_config_blocks_before_any_service_call(tmp_path):
    cfg = write_config(tmp_path, mutate=judge_null)
    client = FakeClient(serving({}))
    clock = Clock()
    code = gate.main(["--config", str(cfg), "--agent-name", "card-helper", "--agent-version", "3"], client=client,
                     clock=clock, sleep=clock.sleep, environ={})
    assert code == 2 and client.started == []


def test_evidence_output_has_no_prompts_and_is_written_when_asked(tmp_path, capsys):
    target = tmp_path / "out" / "evidence.json"
    code = cli(tmp_path, ["--agent-version", "3", "--output", str(target)], FakeClient(serving({})))
    printed = capsys.readouterr().out
    assert code == 0 and target.exists() and printed.strip(), (code, target.exists())
    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(printed)
    assert "Question number" not in printed and "Question number" not in target.read_text(encoding="utf-8")


def test_unwritable_evidence_path_is_invalid(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    assert cli(tmp_path, ["--agent-version", "3", "--output", str(blocker / "evidence.json")],
               FakeClient(serving({}))) == 2


def test_candidate_name_must_match_the_agent_the_config_was_generated_for(tmp_path):
    client = FakeClient(serving({}))
    clock = Clock()
    res = gate.run_release_gate(client, gate.load_config(write_config(tmp_path)), agent_name="some-other-agent",
                                agent_version="1", clock=clock, sleep=clock.sleep)
    assert res.exit_code == 2 and client.started == []
    assert any("card-helper" in why for why in res.reasons), res.reasons

"""Tests for run_routing_evaluation.py: the deterministic LOCAL routing gate (plan Task 3b, cases 10-12, 48, 57-59).

Real router (build_workflow / run_turn) and real scoring over real files. The only fake is a hand-written
model client at the external boundary. Evidence shapes the real router cannot produce (duplicate or unknown
observations, an outcome that contradicts the route) are fed straight to the pure `judge`.
A pass here is LOCAL evidence about this router with these fake models. It does not certify a deployed router.
Run with the template requirements installed, PYTHONPATH unset:  python -m pytest tests -q
"""

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import run_routing_evaluation as gate  # noqa: E402

SPECIALISTS = {"billing": "BILLING-INSTRUCTIONS", "fraud": "FRAUD-INSTRUCTIONS"}
KNOWN = ("billing", "fraud", "refusal")
CASES = [
    {"case_id": "c1", "query": "Why was I charged twice?", "expected_route": "billing"},
    {"case_id": "c2", "query": "I did not make this purchase", "expected_route": "fraud"},
    {"case_id": "c3", "query": "What is the weather on Mars?", "expected_route": "refusal"},
]
SECRET_QUERY = "Why was I charged twice?"
ANSWER_TEXT = "Your billing statement shows a duplicate charge on the card ending 4242."


def router():
    return importlib.import_module("agent")


class Models:
    """Hand-written fake for the model boundary; `route_for(user_text)` decides what the classifier says."""

    def __init__(self, route_for, answers=None, errors=None, delay=None):
        self.route_for, self.answers, self.errors, self.delay = route_for, answers or {}, errors or {}, delay
        self.calls = []

    def factory(self):
        from agent_framework import BaseChatClient, ChatResponse, Message
        outer = self

        def make(role):
            class Client(BaseChatClient):
                def _inner_get_response(self, *, messages, stream, options, **kw):
                    async def go():
                        seen = [(m.role, m.text or "") for m in messages]
                        outer.calls.append(role)
                        await asyncio.sleep(outer.delay or 0)
                        if role in outer.errors and outer.errors[role](seen):
                            raise outer.errors[role](seen)
                        if role == "classifier":
                            return ChatResponse(messages=Message("assistant", [outer.route_for(seen[-1][1])]))
                        return ChatResponse(messages=Message("assistant", [outer.answers.get(role, f"{role} answer")]))
                    return go()

            return Client()

        return make


def by_keyword(text):
    if "charged" in text:
        return "billing"
    if "purchase" in text:
        return "fraud"
    return "none"


def factory_for(models):
    mod = router()
    return lambda: mod.build_workflow(SPECIALISTS, models.factory(), router_instructions="route it")


def write_dataset(tmp_path, cases=CASES, name="baseline.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    return path


def write_specialists(tmp_path):
    path = tmp_path / "specialists.json"
    path.write_text(json.dumps({"router_instructions": "route", "specialists": SPECIALISTS}), encoding="utf-8")
    return path


def cli(tmp_path, models, cases=CASES, extra=(), results=None):
    dataset = write_dataset(tmp_path, cases)
    results = results or tmp_path / "out" / "routing.jsonl"
    argv = ["--dataset", str(dataset), "--specialists", str(write_specialists(tmp_path)),
            "--results", str(results), *extra]
    return gate.main(argv, workflow_factory=factory_for(models), router=router()), results


def rows_of(path):
    assert Path(path).is_file(), f"the gate did not write its results file {path}"
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def text_of(path):
    assert Path(path).is_file(), f"the gate did not write its results file {path}"
    return Path(path).read_text(encoding="utf-8")


def obs(case_id, route, outcome=None, **kw):
    outcome = outcome or ("refused" if route == "refusal" else "answered")
    return gate.Observation(case_id, route=route, outcome=outcome, **kw)


def cases():
    return [gate.Case(c["case_id"], c["query"], c["expected_route"]) for c in CASES]


# --- case 10 / 57: a plausible answer from the wrong specialist fails -------------------------------


def test_a_plausible_answer_from_the_wrong_specialist_fails_whatever_the_answer_says(tmp_path):
    models = Models(lambda q: "fraud", answers={"fraud": ANSWER_TEXT})  # billing question sent to fraud
    code, results = cli(tmp_path, models, cases=CASES[:1])
    assert code == 1
    (row,) = rows_of(results)
    assert (row["expected_route"], row["observed_route"], row["passed"]) == ("billing", "fraud", False)
    assert "billing" in ANSWER_TEXT.lower(), "the wrong specialist's answer reads as a billing answer"


def test_judge_ignores_answer_text_and_any_quality_signal():
    good_text = obs("c1", "fraud", answer="A perfect, polite and correct billing explanation.")
    verdict = gate.judge(cases()[:1], [good_text], KNOWN)
    assert (verdict.exit_code, verdict.verdict) == (1, "fail") and verdict.rows[0].passed is False
    right_route_bad_text = obs("c1", "billing", answer="lorem ipsum nonsense")
    assert gate.judge(cases()[:1], [right_route_bad_text], KNOWN).exit_code == 0


def test_route_comes_from_the_executor_that_ran_not_from_words_in_the_answer(tmp_path):
    models = Models(by_keyword, answers={"billing": "FRAUD fraud refusal I am the fraud specialist"})
    code, results = cli(tmp_path, models, cases=CASES[:1])
    assert code == 0 and rows_of(results)[0]["observed_route"] == "billing"


# --- case 18: everything correct passes ------------------------------------------------------------


def test_correct_specialists_and_a_correct_refusal_pass(tmp_path, capsys):
    models = Models(by_keyword)
    code, results = cli(tmp_path, models)
    assert code == 0
    rows = rows_of(results)
    assert [(r["case_id"], r["observed_route"], r["passed"]) for r in rows] == [
        ("c1", "billing", True), ("c2", "fraud", True), ("c3", "refusal", True)]
    assert "RESULT: pass" in capsys.readouterr().out


def test_the_refusal_route_is_the_routers_reserved_refusal_id():
    assert gate.REFUSAL_ROUTE == router().REFUSAL_ROUTE == "refusal"


# --- case 19: behavioural mismatches fail (exit 1) ---------------------------------------------------


@pytest.mark.parametrize("route_for,case_index,detail", [
    (lambda q: "fraud", 0, "wrong specialist"),
    (lambda q: "none", 0, "refusal where a specialist was expected"),
    (lambda q: "billing", 2, "answer where a refusal was expected"),
])
def test_wrong_route_or_refusal_mismatch_fails_with_exit_one(tmp_path, route_for, case_index, detail):
    code, results = cli(tmp_path, Models(route_for), cases=[CASES[case_index]])
    assert code == 1, detail
    assert rows_of(results)[0]["passed"] is False


def test_one_failing_case_among_passing_ones_fails_the_whole_gate(tmp_path):
    flaky = Models(lambda q: "billing" if "purchase" in q else by_keyword(q))  # c2 misrouted
    code, results = cli(tmp_path, flaky)
    assert code == 1 and [r["passed"] for r in rows_of(results)] == [True, False, True]


# --- case 20 / 58: missing, duplicate and unknown evidence fails closed (exit 2) ----------------------


def test_a_case_with_no_observation_blocks():
    verdict = gate.judge(cases(), [obs("c1", "billing"), obs("c2", "fraud")], KNOWN)
    assert (verdict.exit_code, verdict.verdict) == (2, "blocked")
    assert any("c3" in p and "no observation" in p for p in verdict.problems)
    assert [r.passed for r in verdict.rows] == [True, True, False]


def test_two_observations_for_one_case_block_even_if_both_are_correct():
    verdict = gate.judge(cases()[:1], [obs("c1", "billing"), obs("c1", "billing")], KNOWN)
    assert verdict.exit_code == 2 and any("c1" in p and "duplicate" in p for p in verdict.problems)


def test_an_observation_for_an_unknown_case_blocks():
    verdict = gate.judge(cases()[:1], [obs("c1", "billing"), obs("ghost", "billing")], KNOWN)
    assert verdict.exit_code == 2 and any("ghost" in p and "unknown case" in p for p in verdict.problems)


@pytest.mark.parametrize("route", ["payments", "", None, "Billing", "dispatch-billing", "classifier"])
def test_an_unknown_or_missing_observed_route_blocks(route):
    verdict = gate.judge(cases()[:1], [gate.Observation("c1", route=route, outcome="answered")], KNOWN)
    assert verdict.exit_code == 2 and any("route" in p for p in verdict.problems)


@pytest.mark.parametrize("route,outcome", [("refusal", "answered"), ("billing", "refused"), ("billing", None),
                                           ("billing", "maybe")])
def test_an_outcome_that_contradicts_the_route_blocks(route, outcome):
    verdict = gate.judge(cases()[:1], [gate.Observation("c1", route=route, outcome=outcome)], KNOWN)
    assert verdict.exit_code == 2 and any("outcome" in p for p in verdict.problems)


def test_no_cases_and_no_observations_is_not_a_pass():
    verdict = gate.judge([], [], KNOWN)
    assert (verdict.exit_code, verdict.verdict) == (2, "blocked") and any("no cases" in p for p in verdict.problems)


def test_a_case_expecting_an_unknown_route_blocks():
    bad = [gate.Case("c1", "q", "payments")]
    verdict = gate.judge(bad, [obs("c1", "billing")], KNOWN)
    assert verdict.exit_code == 2 and any("expected route" in p for p in verdict.problems)


def test_invalid_evidence_outranks_a_behavioural_failure():
    verdict = gate.judge(cases()[:2], [obs("c1", "fraud")], KNOWN)  # c1 wrong AND c2 missing
    assert verdict.exit_code == 2 and verdict.rows[0].passed is False


def test_an_invocation_error_blocks_and_records_only_the_error_type(tmp_path):
    boom = Models(by_keyword, errors={"billing": lambda seen: RuntimeError(f"model down for {SECRET_QUERY}")})
    code, results = cli(tmp_path, boom, cases=CASES[:2])
    assert code == 2
    text = text_of(results)
    assert "RuntimeError" in text and SECRET_QUERY not in text and "model down" not in text
    assert [r["passed"] for r in rows_of(results)] == [False, True]


def test_a_classifier_failure_blocks_that_case_and_the_rest_still_run(tmp_path):
    models = Models(by_keyword, errors={"classifier": lambda seen: (
        ConnectionError("reset") if "purchase" in seen[-1][1] else None)})
    code, results = cli(tmp_path, models)
    assert code == 2
    assert [r["passed"] for r in rows_of(results)] == [True, False, True]


def test_a_hung_model_call_times_out_and_blocks(tmp_path):
    slow = Models(by_keyword, delay=5)
    code, results = cli(tmp_path, slow, cases=CASES[:1], extra=["--timeout-seconds", "0.05"])
    assert code == 2 and "TimeoutError" in text_of(results)


# --- case 12 / 59: JSONL, scoped, no raw prompts or responses by default -----------------------------


def test_results_are_jsonl_with_exactly_the_scoped_fields_and_no_raw_text(tmp_path):
    models = Models(by_keyword, answers={"billing": ANSWER_TEXT})
    code, results = cli(tmp_path, models)
    text = text_of(results)
    assert code == 0 and text.endswith("\n") and len(text.splitlines()) == 3
    for row in rows_of(results):
        assert set(row) == {"scope", "case_id", "expected_route", "observed_route", "outcome", "passed", "reason"}
        assert row["scope"] == "local-routing-check"
    for raw in (SECRET_QUERY, ANSWER_TEXT, "weather on Mars", "4242"):
        assert raw not in text


def test_include_raw_is_an_explicit_opt_in_that_adds_query_and_answer(tmp_path):
    models = Models(by_keyword, answers={"billing": ANSWER_TEXT})
    code, results = cli(tmp_path, models, cases=CASES[:1], extra=["--include-raw"])
    (row,) = rows_of(results)
    assert code == 0 and row["query"] == SECRET_QUERY and row["answer"] == ANSWER_TEXT


def test_unicode_queries_survive_when_raw_is_requested(tmp_path):
    case = {"case_id": "u1", "query": "¿Por qué me cobraron dos veces? charged 你好 🙂", "expected_route": "billing"}
    code, results = cli(tmp_path, Models(by_keyword), cases=[case], extra=["--include-raw"])
    assert code == 0 and rows_of(results)[0]["query"] == case["query"]
    assert "你好" in text_of(results), "written as UTF-8, not \\u escapes"


def test_output_states_that_this_is_local_evidence_and_not_a_deployed_certification(tmp_path, capsys):
    cli(tmp_path, Models(by_keyword))
    out = capsys.readouterr().out
    assert "LOCAL" in out and "does not certify a deployed router" in out
    assert "does not certify a deployed router" in gate.__doc__ and gate.NOT_CERTIFIED in out


def test_failed_run_still_writes_its_results_file_and_the_directory_is_created(tmp_path):
    code, results = cli(tmp_path, Models(lambda q: "fraud"), cases=CASES[:1])
    assert code == 1 and results.parent.is_dir() and results.stat().st_size > 0


def test_unwritable_results_path_blocks(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    code, _ = cli(tmp_path, Models(by_keyword), results=blocker / "routing.jsonl")
    assert code == 2


# --- case 23: the dataset loader ----------------------------------------------------------------------


def dataset_text(tmp_path, text):
    path = tmp_path / "d.jsonl"
    path.write_text(text, encoding="utf-8")
    return path


def line(**over):
    row = {"case_id": "c1", "query": "Why was I charged twice?", "expected_route": "billing"}
    row.update(over)
    return json.dumps(row)


def test_load_cases_reads_ids_queries_and_routes_in_order(tmp_path):
    loaded = gate.load_cases(write_dataset(tmp_path), KNOWN)
    assert [(c.case_id, c.expected_route) for c in loaded] == [("c1", "billing"), ("c2", "fraud"), ("c3", "refusal")]
    assert loaded[0].query == SECRET_QUERY


@pytest.mark.parametrize("text", [
    "", "\n\n", "not json\n", "[1, 2]\n", line(case_id="") + "\n", line(query="  ") + "\n",
    line(expected_route="payments") + "\n", line(expected_route=None) + "\n",
    line() + "\n" + line() + "\n", line() + "\n" + line(case_id="c2", expected_route="fraud") + "\n",
    json.dumps({"case_id": "c1", "expected_route": "billing"}) + "\n",
    json.dumps({"case_id": 7, "query": "q", "expected_route": "billing"}) + "\n",
], ids=["empty", "blank-lines", "bad-json", "non-object", "blank-id", "blank-query", "unknown-route",
        "null-route", "duplicate-id", "same-query-two-routes", "missing-query", "non-string-id"])
def test_load_cases_rejects_unusable_datasets(tmp_path, text):
    with pytest.raises(gate.EvidenceError):
        gate.load_cases(dataset_text(tmp_path, text), KNOWN)


def test_load_cases_missing_file_is_an_evidence_error(tmp_path):
    with pytest.raises(gate.EvidenceError):
        gate.load_cases(tmp_path / "absent.jsonl", KNOWN)


def test_load_cases_ignores_surrounding_blank_lines_and_extra_fields(tmp_path):
    path = dataset_text(tmp_path, "\n" + line(category="x", extra={"a": 1}) + "\n\n")
    assert [c.case_id for c in gate.load_cases(path, KNOWN)] == ["c1"]


# --- case 24: ordering and isolation -------------------------------------------------------------------


def test_a_fresh_workflow_is_built_for_every_case_in_dataset_order(tmp_path):
    built = []
    models = Models(by_keyword)
    mod = router()

    def counting_factory():
        built.append(1)
        return mod.build_workflow(SPECIALISTS, models.factory(), router_instructions="r")

    async def go():
        return await gate.collect(gate.load_cases(write_dataset(tmp_path), KNOWN), counting_factory, mod.run_turn)

    observed = asyncio.run(go())
    assert len(built) == 3 and [o.case_id for o in observed] == ["c1", "c2", "c3"]
    assert [o.route for o in observed] == ["billing", "fraud", "refusal"]


def test_each_case_is_run_with_no_history_from_another_case(tmp_path):
    mod = router()
    models = Models(by_keyword)
    asyncio.run(gate.collect(gate.load_cases(write_dataset(tmp_path), KNOWN), factory_for(models), mod.run_turn))
    assert models.calls.count("classifier") == 3 and models.calls[:2] == ["classifier", "billing"]


# --- CLI: configuration problems block before any model call -------------------------------------------


def test_cli_without_a_model_endpoint_blocks_before_any_call(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    dataset = write_dataset(tmp_path)
    code = gate.main(["--dataset", str(dataset), "--specialists", str(write_specialists(tmp_path)),
                      "--results", str(tmp_path / "r.jsonl")], router=router())
    assert code == 2 and "FOUNDRY_PROJECT_ENDPOINT" in capsys.readouterr().err
    assert not (tmp_path / "r.jsonl").exists()


def test_cli_with_a_bad_dataset_blocks_and_calls_no_model(tmp_path):
    models = Models(by_keyword)
    code, _ = cli(tmp_path, models, cases=[dict(CASES[0], expected_route="payments")])
    assert code == 2 and models.calls == []


def test_cli_with_missing_specialists_file_blocks(tmp_path):
    code = gate.main(["--dataset", str(write_dataset(tmp_path)), "--specialists", str(tmp_path / "nope.json"),
                      "--results", str(tmp_path / "r.jsonl")], workflow_factory=factory_for(Models(by_keyword)),
                     router=router())
    assert code == 2


def test_the_default_factory_is_the_hosted_entrypoints_own_workflow_builder(monkeypatch):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    from agent_framework import Workflow
    factory = gate.default_factory(HERE)
    first, second = factory(), factory()
    assert isinstance(first, Workflow) and first is not second


# --- JSONL records are split on newlines only -----------------------------------------------------------


def test_load_cases_keeps_a_query_that_contains_unicode_line_separators_in_one_record(tmp_path):
    query = "charged twice and\x85again ok \x0b\x0c\x1c"
    path = tmp_path / "d.jsonl"
    path.write_text(json.dumps({"case_id": "c1", "query": query, "expected_route": "billing"}, ensure_ascii=False)
                    + "\n", encoding="utf-8")
    try:
        loaded = gate.load_cases(path, KNOWN)
    except gate.EvidenceError as exc:
        pytest.fail(f"a valid record was split at a Unicode line separator: {exc}")
    assert [c.query for c in loaded] == [query]


# --- runs from a copy outside the source tree, PYTHONPATH unset ----------------------------------------


def test_the_runner_works_from_a_copied_router_tree_outside_the_source_tree(tmp_path):
    import os
    import shutil
    import subprocess
    tree = tmp_path / "router"
    (tree / "evals" / "datasets").mkdir(parents=True)
    for name in ("agent.py", "main.py", "specialists.json", "run_routing_evaluation.py"):
        shutil.copy(HERE / name, tree / name)
    (tree / "specialists.json").write_text(json.dumps({"router_instructions": "route", "specialists": SPECIALISTS}),
                                           encoding="utf-8")
    write_dataset(tree / "evals" / "datasets", CASES)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.pop("FOUNDRY_PROJECT_ENDPOINT", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # no endpoint: the gate must refuse to run (exit 2) from the copy, proving it found its own files
    run = subprocess.run([sys.executable, "run_routing_evaluation.py"], cwd=tree, env=env, capture_output=True,
                         text=True)
    assert run.returncode == 2, (run.returncode, run.stdout[-500:], run.stderr[-800:])
    assert "FOUNDRY_PROJECT_ENDPOINT" in run.stderr and "Traceback" not in run.stderr
    assert not (tree / "evals" / "results").exists(), "a blocked run must not leave results behind"

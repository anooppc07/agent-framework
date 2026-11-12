#!/usr/bin/env python3
"""Deterministic LOCAL routing gate for the router/specialists template.

Adapted in spirit from github.com/DeepuMM/agent-deployment @ 2220a3c2e3964728aef4444048944db0ebf41ecc
(the fail-closed release-gate idea in scripts/agent_pipeline.py); the routing logic here is new.

For each approved case it runs one turn through the router's own `run_turn` on a fresh workflow and reads the
observed route and outcome from the returned `RouteResult` (which comes from the executor that actually ran),
never from the answer text. A plausible answer from the wrong specialist therefore fails, whatever any quality
score says. Exactly one known observation per case is required.

Exit codes: 0 every case routed as expected; 1 an observed behavioural mismatch (wrong route, a refusal where
a specialist was expected, or the reverse); 2 invalid configuration, invocation or evidence (missing, duplicate
or unknown observation, an unknown route, a model error, an unusable dataset). Invalid evidence outranks a
behavioural failure because it means the run cannot be trusted.

SCOPE: this is LOCAL evidence about this router code with whatever models the process is given.
It does not certify a deployed router.
A hosted candidate needs a validated route-evidence adapter that correlates actual observations to that
candidate, and generic Foundry quality scores are not routing accuracy.

Results are scoped JSONL. Raw prompts and answers are NOT written unless --include-raw is passed, and an
invocation error records only the exception type (a message could echo the prompt).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

EXIT_PASS, EXIT_FAIL, EXIT_INVALID = 0, 1, 2
SCOPE = "local-routing-check"
REFUSAL_ROUTE = "refusal"  # the router's reserved refusal route; asserted equal to agent.REFUSAL_ROUTE in tests
OUTCOMES = {"answered", "refused"}
NOT_CERTIFIED = "This is LOCAL routing evidence and does not certify a deployed router."
DEFAULT_TIMEOUT = 120.0


class EvidenceError(ValueError):
    """The dataset, configuration or observations cannot support a routing verdict."""


@dataclass(frozen=True)
class Case:
    case_id: str
    query: str
    expected_route: str


@dataclass(frozen=True)
class Observation:
    case_id: str
    route: str | None = None
    outcome: str | None = None
    error: str | None = None  # exception type name only
    answer: str | None = None  # kept only for --include-raw; never used for scoring


@dataclass(frozen=True)
class Row:
    case_id: str
    expected_route: str
    observed_route: str | None
    outcome: str | None
    passed: bool
    reason: str


@dataclass(frozen=True)
class Verdict:
    exit_code: int
    verdict: str  # pass | fail | blocked
    rows: tuple = ()
    problems: tuple = ()


# --- dataset ------------------------------------------------------------------------------------


def _is_text(value):
    return isinstance(value, str) and bool(value.strip())


def load_cases(path, known_routes):
    """Read the JSONL dataset. Every line needs case_id, query and a known expected_route."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceError(f"{Path(path).name}: cannot read the dataset ({exc.strerror or exc})") from exc
    cases, ids, queries = [], set(), {}
    for number, text in enumerate(raw.split("\n"), start=1):  # newline-delimited; splitlines() also splits U+2028/NEL
        if not text.strip():
            continue
        where = f"{Path(path).name} line {number}"
        try:
            row = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"{where}: invalid JSON ({exc.msg})") from exc
        if not isinstance(row, dict):
            raise EvidenceError(f"{where}: must be a JSON object")
        if not _is_text(row.get("case_id")):
            raise EvidenceError(f"{where}: case_id must be a non-blank string")
        if not _is_text(row.get("query")):
            raise EvidenceError(f"{where}: query must be a non-blank string")
        if row.get("expected_route") not in known_routes:
            raise EvidenceError(f"{where}: expected_route {row.get('expected_route')!r} is not one of "
                                f"{', '.join(known_routes)}")
        if row["case_id"] in ids:
            raise EvidenceError(f"{where}: duplicate case_id {row['case_id']!r}")
        key = " ".join(row["query"].split())
        if key in queries and queries[key] != row["expected_route"]:
            raise EvidenceError(f"{where}: the same query has conflicting expected routes "
                                f"{queries[key]!r} and {row['expected_route']!r}")
        ids.add(row["case_id"])
        queries[key] = row["expected_route"]
        cases.append(Case(row["case_id"], row["query"], row["expected_route"]))
    if not cases:
        raise EvidenceError(f"{Path(path).name}: the dataset has no cases")
    return cases


# --- scoring ------------------------------------------------------------------------------------


def judge(cases, observations, known_routes):
    """Score observations against cases. Exit 2 beats 1 beats 0; nothing is ever inferred."""
    problems = []
    if not cases:
        return Verdict(EXIT_INVALID, "blocked", (), ("no cases: nothing was evaluated",))
    known_ids = {c.case_id for c in cases}
    by_case = {}
    for ob in observations:
        if ob.case_id not in known_ids:
            problems.append(f"{ob.case_id}: observation for an unknown case")
            continue
        by_case.setdefault(ob.case_id, []).append(ob)
    rows = []
    for case in cases:
        found = by_case.get(case.case_id, [])
        if case.expected_route not in known_routes:
            problems.append(f"{case.case_id}: expected route {case.expected_route!r} is not a known route")
        if not found:
            problems.append(f"{case.case_id}: no observation")
            rows.append(Row(case.case_id, case.expected_route, None, None, False, "no observation"))
            continue
        if len(found) > 1:
            problems.append(f"{case.case_id}: duplicate observations ({len(found)})")
            rows.append(Row(case.case_id, case.expected_route, None, None, False, "duplicate observations"))
            continue
        rows.append(_judge_one(case, found[0], known_routes, problems))
    if problems:
        return Verdict(EXIT_INVALID, "blocked", tuple(rows), tuple(problems))
    if any(not r.passed for r in rows):
        return Verdict(EXIT_FAIL, "fail", tuple(rows), ())
    return Verdict(EXIT_PASS, "pass", tuple(rows), ())


def _judge_one(case, ob, known_routes, problems):
    if ob.error:
        problems.append(f"{case.case_id}: invocation error {ob.error}")
        return Row(case.case_id, case.expected_route, None, None, False, f"invocation error {ob.error}")
    if ob.route not in known_routes:
        problems.append(f"{case.case_id}: observed route {ob.route!r} is not a known route")
        return Row(case.case_id, case.expected_route, ob.route, ob.outcome, False, "unknown observed route")
    expected_outcome = "refused" if ob.route == REFUSAL_ROUTE else "answered"
    if ob.outcome != expected_outcome:
        problems.append(f"{case.case_id}: outcome {ob.outcome!r} contradicts route {ob.route!r}")
        return Row(case.case_id, case.expected_route, ob.route, ob.outcome, False, "outcome contradicts route")
    if ob.route != case.expected_route:
        return Row(case.case_id, case.expected_route, ob.route, ob.outcome, False,
                   f"routed to {ob.route!r}, expected {case.expected_route!r}")
    return Row(case.case_id, case.expected_route, ob.route, ob.outcome, True, "routed as expected")


# --- execution ----------------------------------------------------------------------------------


async def _observe(case, workflow_factory, run_turn, timeout):
    try:
        result = await asyncio.wait_for(run_turn(workflow_factory, case.query, ()), timeout)
    except Exception as exc:  # an invocation failure is evidence of a broken run, never a pass or a mismatch
        return Observation(case.case_id, error=type(exc).__name__)
    return Observation(case.case_id, route=getattr(result, "route", None),
                       outcome=getattr(result, "outcome", None), answer=getattr(result, "answer", None))


async def collect(cases, workflow_factory, run_turn, refusal_route=REFUSAL_ROUTE, timeout=DEFAULT_TIMEOUT):
    """Run every case in dataset order, each on a fresh workflow and with no history from another case."""
    return [await _observe(case, workflow_factory, run_turn, timeout) for case in cases]


# --- results ------------------------------------------------------------------------------------


def write_results(path, verdict, cases, observations, include_raw=False):
    """Scoped JSONL: no query or answer text unless include_raw. Written even when the gate fails."""
    queries = {c.case_id: c.query for c in cases}
    answers = {o.case_id: o.answer for o in observations}
    lines = []
    for row in verdict.rows:
        record = {"scope": SCOPE, "case_id": row.case_id, "expected_route": row.expected_route,
                  "observed_route": row.observed_route, "outcome": row.outcome, "passed": row.passed,
                  "reason": row.reason}
        if include_raw:
            record["query"] = queries.get(row.case_id)
            record["answer"] = answers.get(row.case_id)
        lines.append(json.dumps(record, ensure_ascii=False))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


# --- command line -------------------------------------------------------------------------------


def _load_router(agent_dir):
    sys.path.insert(0, str(agent_dir))
    try:
        return importlib.import_module("agent")
    finally:
        sys.path.remove(str(agent_dir))


def default_factory(agent_dir):
    """The hosted entrypoint's own workflow builder: real models via the Foundry client; needs an endpoint."""
    agent_dir = Path(agent_dir)
    if not os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip():
        raise EvidenceError("FOUNDRY_PROJECT_ENDPOINT is not set; there is no model to route with")
    sys.path.insert(0, str(agent_dir))
    try:
        main = importlib.import_module("main")
    finally:
        sys.path.remove(str(agent_dir))
    return lambda: main.make_workflow(None)


def _specialists(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        specialists = data["specialists"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvidenceError(f"{Path(path).name}: cannot read the specialists configuration ({type(exc).__name__})") from exc
    if not isinstance(specialists, dict) or not specialists:
        raise EvidenceError(f"{Path(path).name}: no specialists are configured")
    return tuple(specialists)


def _parser(here):
    parser = argparse.ArgumentParser(description="Deterministic LOCAL routing gate. " + NOT_CERTIFIED)
    parser.add_argument("--dataset", default=str(here / "evals" / "datasets" / "baseline.jsonl"))
    parser.add_argument("--specialists", default=str(here / "specialists.json"))
    parser.add_argument("--results", default=str(here / "evals" / "results" / "routing.jsonl"))
    parser.add_argument("--include-raw", action="store_true", help="also write queries and answers (off by default)")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT)
    return parser


def _print_summary(verdict, args, wrote):
    print(f"{NOT_CERTIFIED}")
    print(f"scope: {SCOPE}; cases: {len(verdict.rows)}; passed: {sum(r.passed for r in verdict.rows)}")
    for row in verdict.rows:
        print(f"  {'PASS' if row.passed else 'FAIL'}  {row.case_id}: expected {row.expected_route}, "
              f"observed {row.observed_route}  ({row.reason})")
    for problem in verdict.problems:
        print(f"  BLOCKED: {problem}", file=sys.stderr)
    if wrote:
        print(f"results: {args.results}")
    print(f"RESULT: {verdict.verdict} (exit {verdict.exit_code})")


def main(argv=None, *, workflow_factory=None, router=None):
    here = Path(__file__).resolve().parent
    try:
        args = _parser(here).parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        router = router or _load_router(here)
        known = (*_specialists(args.specialists), router.REFUSAL_ROUTE)
        cases = load_cases(args.dataset, known)
        factory = workflow_factory or default_factory(here)
    except EvidenceError as exc:
        print(f"BLOCKED (exit 2): {exc}", file=sys.stderr)
        return EXIT_INVALID
    observations = asyncio.run(collect(cases, factory, router.run_turn, router.REFUSAL_ROUTE, args.timeout_seconds))
    verdict = judge(cases, observations, known)
    try:
        write_results(args.results, verdict, cases, observations, args.include_raw)
    except OSError as exc:
        print(f"BLOCKED (exit 2): cannot write the results file: {exc.strerror or exc}", file=sys.stderr)
        return EXIT_INVALID
    _print_summary(verdict, args, wrote=True)
    return verdict.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

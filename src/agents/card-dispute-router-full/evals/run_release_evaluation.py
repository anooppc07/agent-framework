#!/usr/bin/env python3
"""Fail-closed release gate: evaluate ONE explicit hosted-agent candidate (name + version) in Foundry.

Adapted from github.com/DeepuMM/agent-deployment @ 2220a3c2e3964728aef4444048944db0ebf41ecc
(scripts/agent_pipeline.py: gate_criteria, gate_evaluation, run_gate), reused with the owner's permission.
What changed from the reference, deliberately:
  * the candidate is an explicit name AND version; "latest"/blank is refused, and a version that was
    approved before is evaluated again, never skipped;
  * the run status failed / canceled / timed out blocks explicitly (the reference only stopped polling);
  * an evaluator that reports 0 of 0 has produced NO RESULT (exit 2); the reference scored it as 0%;
  * a configured evaluator that is missing, partial or duplicated blocks;
  * every configured evaluator must meet its own threshold; an unconfigured extra evaluator is ignored
    and can never stand in for, or rescue, a configured one;
  * the candidate name, version, dataset hash and evaluator set recorded in the result must equal what
    was requested, otherwise the evidence is rejected as stale or wrong;
  * no APIM/gateway, prompt-definition or deploy code is carried over (the reference is prompt-agent/APIM
    oriented); a hosted promotion adapter is a separate, explicit step.

Exit codes: 0 every configured evaluator passed; 1 behavioural failure (a threshold was missed);
2 invalid configuration, invocation or evidence (blocked: nothing may be promoted on it).

Scope: this reads evals/evals.yaml and talks to the Foundry evaluation service through a thin client
(`FoundryEvalClient`); tests substitute a hand-written fake. A router is never released on these quality
scores alone: its route evidence must come from a validated adapter for the deployed candidate, so a
router run always blocks with exit 2 unless a behavioural failure is reported first.
The evaluator mappings are adapted from the reference and the legacy runner; they are unverified against
a live service by this skill's tests. The judge model and thresholds come only from evals.yaml; nothing
here supplies a default for them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import yaml

EXIT_PASS, EXIT_FAIL, EXIT_INVALID = 0, 1, 2
SCOPE = "hosted-evaluation-service"
JUDGE_PARAM = "deployment_name"  # initialization_parameters key used by the Foundry built-in evaluators
PATTERNS = ("single-agent", "router-specialists")
DATASETS = ("baseline", "smoke")
MIN_ITEMS = 3  # the reference refuses a dataset under three queries
TERMINAL = ("completed", "failed", "canceled", "cancelled")
IN_PROGRESS = ("queued", "in_progress", "running")
FORBIDDEN_VERSIONS = ("latest", "current", "default", "newest")
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TEXT_RESPONSE = "{{sample.output_text}}"

# evaluator -> response mapping and whether every dataset item must carry approved `context`.
SUPPORTED_EVALUATORS = {
    "relevance": {"response": _TEXT_RESPONSE, "needs_context": False},
    "coherence": {"response": _TEXT_RESPONSE, "needs_context": False},
    "fluency": {"response": _TEXT_RESPONSE, "needs_context": False},
    "intent_resolution": {"response": _TEXT_RESPONSE, "needs_context": False},
    "task_adherence": {"response": "{{sample.output_items}}", "needs_context": False},
    "groundedness": {"response": _TEXT_RESPONSE, "needs_context": True},
}
ITEM_SCHEMA = {
    "type": "object",
    "required": ["query"],
    "properties": {"case_id": {"type": "string"}, "query": {"type": "string"}, "context": {"type": "string"}},
}


class ConfigError(ValueError):
    """The evaluation configuration or invocation is invalid or unconfigured."""


@dataclass(frozen=True)
class GateConfig:
    agent: str
    pattern: str
    judge_model: str
    min_pass_rate: float
    evaluators: tuple
    dataset_status: str
    rows: tuple  # the selected dataset's cases, in order
    source: str  # file name plus #dataset; never an absolute local path
    dataset_name: str = "baseline"


@dataclass(frozen=True)
class GateRequest:
    agent_name: str
    agent_version: str
    judge_model: str
    evaluators: tuple
    min_pass_rate: float
    items: tuple
    dataset_sha256: str
    dataset_source: str
    criteria: tuple
    item_schema: dict
    metadata: dict
    pattern: str = "single-agent"


@dataclass(frozen=True)
class RunRef:
    eval_id: str
    run_id: str


@dataclass(frozen=True)
class CriterionResult:
    name: str
    passed: int
    failed: int


@dataclass(frozen=True)
class RunSnapshot:
    status: str
    candidate_name: str | None
    candidate_version: str | None
    dataset_sha256: str | None
    evaluators: tuple
    criteria: tuple
    errored: int = 0
    report_url: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class GateResult:
    exit_code: int
    verdict: str  # pass | fail | blocked
    reasons: tuple = ()
    scores: dict | None = None
    identity: dict | None = None
    report_url: str | None = None


# --- configuration ------------------------------------------------------------------------------


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _need(condition, message):
    if not condition:
        raise ConfigError(message)


def load_config(path, dataset="baseline"):
    """Read evals.yaml. Anything unconfigured, unsupported or inconsistent raises ConfigError."""
    _need(dataset in DATASETS, f"dataset {dataset!r} is not one of {', '.join(DATASETS)}")
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"{path.name}: cannot read ({exc.strerror or exc})") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML ({exc})") from exc
    _need(isinstance(raw, dict), f"{path.name}: must be a mapping")
    _need(isinstance(raw.get("agent"), str) and _NAME.match(raw["agent"]), "agent: a kebab-case name is required")
    _need(raw.get("pattern") in PATTERNS, f"pattern: must be one of {', '.join(PATTERNS)}")
    _need(_text(raw.get("judge_model")), "judge_model is unconfigured: set the judge deployment explicitly")
    rate = raw.get("min_pass_rate")
    _need(rate is not None, "min_pass_rate is unconfigured: set the approved threshold explicitly")
    _need(isinstance(rate, (int, float)) and not isinstance(rate, bool) and 0 < rate <= 1,
          "min_pass_rate: must be a number greater than 0 and at most 1")
    evaluators = raw.get("evaluators")
    _need(isinstance(evaluators, list) and evaluators, "evaluators is unconfigured: list the evaluators to gate on")
    _need(all(isinstance(e, str) for e in evaluators) and len(set(evaluators)) == len(evaluators),
          "evaluators: must be unique strings")
    unknown = [e for e in evaluators if e not in SUPPORTED_EVALUATORS]
    _need(not unknown, f"evaluators {unknown} are not supported; choose from {', '.join(sorted(SUPPORTED_EVALUATORS))}")
    _need(raw.get("dataset_status") == "approved", "dataset is not approved (dataset_status must be 'approved')")
    cases = raw.get("dataset")
    _need(isinstance(cases, list) and all(isinstance(c, dict) for c in cases), "dataset: must be a list of cases")
    _need(len(cases) >= MIN_ITEMS, f"dataset: needs at least {MIN_ITEMS} approved cases, has {len(cases)}")
    ids = [c.get("case_id") for c in cases]
    _need(all(_text(i) for i in ids), "dataset: every case needs a non-blank case_id")
    _need(len(set(ids)) == len(ids), "dataset: duplicate case_id")
    _need(all(_text(c.get("query")) for c in cases), "dataset: every case needs a non-blank query")
    _need(all(c.get("context") is None or _text(c["context"]) for c in cases), "dataset: context must be non-blank text")
    smoke = raw.get("smoke", [])
    _need(isinstance(smoke, list) and all(isinstance(i, str) for i in smoke), "smoke: must be a list of case ids")
    _need(len(set(smoke)) == len(smoke) and set(smoke) <= set(ids), "smoke: must list distinct known case ids")
    by_id = {c["case_id"]: c for c in cases}
    if dataset == "smoke":
        _need(smoke, "smoke: no smoke cases are listed")
        rows = tuple(dict(by_id[i]) for i in smoke)
    else:
        rows = tuple(dict(c) for c in cases)
    for name in evaluators:
        if SUPPORTED_EVALUATORS[name]["needs_context"]:
            _need(all(_text(r.get("context")) for r in rows),
                  f"{name} needs approved context on every case; instructions alone are not policy evidence")
    return GateConfig(raw["agent"], raw["pattern"], raw["judge_model"].strip(), rate, tuple(evaluators),
                      "approved", rows, f"{path.name}#{dataset}", dataset)


# --- request ------------------------------------------------------------------------------------


def dataset_items(rows):
    """What the service receives per case: id, query and (only when approved) context. Nothing else."""
    items = []
    for row in rows:
        item = {"case_id": row["case_id"], "query": row["query"]}
        if row.get("context"):
            item["context"] = row["context"]
        items.append(item)
    return items


def dataset_sha256(rows):
    """Order-insensitive-per-key, content-sensitive identity of the exact items evaluated."""
    canonical = json.dumps(list(rows), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_criteria(names, judge_model):
    criteria = []
    for name in names:
        entry = SUPPORTED_EVALUATORS[name]
        mapping = {"query": "{{item.query}}", "response": entry["response"]}
        if entry["needs_context"]:
            mapping["context"] = "{{item.context}}"
        criteria.append({
            "type": "azure_ai_evaluator", "name": name, "evaluator_name": f"builtin.{name}",
            "initialization_parameters": {JUDGE_PARAM: judge_model}, "data_mapping": mapping,
        })
    return criteria


def _candidate(agent_name, agent_version, config):
    _need(_text(agent_name), "the candidate agent name is required")
    _need(agent_name == config.agent,
          f"candidate {agent_name!r} is not the agent this evaluation was generated for ({config.agent!r})")
    _need(isinstance(agent_version, str) and _VERSION.match(agent_version),
          "the candidate version is required: an explicit version identifier, not blank or malformed")
    _need(agent_version.lower() not in FORBIDDEN_VERSIONS,
          f"the candidate version must be an explicit number or id, not {agent_version!r}")
    return agent_name, agent_version


def build_request(config, agent_name, agent_version):
    name, version = _candidate(agent_name, agent_version, config)
    items = dataset_items(config.rows)
    digest = dataset_sha256(items)
    return GateRequest(
        agent_name=name, agent_version=version, judge_model=config.judge_model, evaluators=config.evaluators,
        min_pass_rate=config.min_pass_rate, items=tuple(items), dataset_sha256=digest,
        dataset_source=config.source, criteria=tuple(build_criteria(config.evaluators, config.judge_model)),
        item_schema=ITEM_SCHEMA, pattern=config.pattern,
        metadata={"agent": name, "agent_version": version, "dataset_sha256": digest,
                  "dataset_source": config.source, "evaluators": ",".join(config.evaluators),
                  "pipeline": "foundry-agent-skill"},
    )


# --- scoring ------------------------------------------------------------------------------------


def _identity(request):
    return {
        "agent_name": request.agent_name, "agent_version": request.agent_version,
        "dataset_sha256": request.dataset_sha256, "dataset_source": request.dataset_source,
        "evaluators": sorted(request.evaluators), "judge_model": request.judge_model,
        "min_pass_rate": request.min_pass_rate, "pattern": request.pattern,
    }


def _blocked(reasons, request=None, scores=None, report_url=None):
    return GateResult(EXIT_INVALID, "blocked", tuple(reasons), scores, _identity(request) if request else None,
                      report_url)


def _evidence_problems(request, snap):
    """Reasons the run cannot be trusted as evidence for THIS candidate, dataset and evaluator set."""
    why = []
    if snap.status != "completed":
        why.append(f"run status {snap.status!r} is not 'completed'")
    if snap.error:
        why.append(f"the service reported an error: {snap.error}")
    if snap.candidate_name != request.agent_name:
        why.append(f"candidate name {snap.candidate_name!r} does not match the requested {request.agent_name!r}")
    if snap.candidate_version != request.agent_version:
        why.append(f"candidate version {snap.candidate_version!r} does not match the requested "
                   f"{request.agent_version!r}")
    if snap.dataset_sha256 != request.dataset_sha256:
        why.append("dataset hash in the result does not match the dataset requested (stale or wrong evidence)")
    if set(snap.evaluators) != set(request.evaluators):
        why.append(f"evaluator set {sorted(snap.evaluators)} does not match the requested "
                   f"{sorted(request.evaluators)}")
    if snap.errored:
        why.append(f"{snap.errored} item(s) errored in the service, so the scores are incomplete")
    return why


def judge(request, snapshot):
    """Score one finished run. Only the requested evaluators count; every one needs its own result."""
    blocked, failed, scores = _evidence_problems(request, snapshot), [], {}
    results = {}
    for crit in snapshot.criteria:
        if crit.name in results:
            blocked.append(f"evaluator {crit.name}: duplicate result entries")
        results[crit.name] = crit
    expected = len(request.items)
    threshold = Fraction(str(request.min_pass_rate))
    for name in request.evaluators:  # extras in `results` are never looked at
        crit = results.get(name)
        if crit is not None and (crit.passed < 0 or crit.failed < 0):
            blocked.append(f"evaluator {name}: invalid counts {crit.passed}/{crit.failed}")
            continue
        total = crit.passed + crit.failed if crit is not None else 0
        if total == 0:
            blocked.append(f"evaluator {name}: no result (missing or 0 of 0 scored)")
            continue
        if total != expected:
            blocked.append(f"evaluator {name}: scored {total} of {expected} items")
            continue
        scores[name] = {"passed": crit.passed, "total": total, "rate": crit.passed / total,
                        "threshold": request.min_pass_rate}
        if Fraction(crit.passed, total) < threshold:
            failed.append(f"evaluator {name}: {crit.passed} of {total} passed, below the "
                          f"{request.min_pass_rate:.0%} threshold")
    identity, url = _identity(request), snapshot.report_url
    if blocked:
        return GateResult(EXIT_INVALID, "blocked", tuple(blocked + failed), scores, identity, url)
    if failed:
        return GateResult(EXIT_FAIL, "fail", tuple(failed), scores, identity, url)
    if request.pattern == "router-specialists":
        return GateResult(EXIT_INVALID, "blocked", (
            "route evidence is missing: quality scores are not routing accuracy and a local routing check does "
            "not certify a deployed router; a validated route-evidence adapter for this candidate is required",
        ), scores, identity, url)
    return GateResult(EXIT_PASS, "pass", (), scores, identity, url)


# --- the gate -----------------------------------------------------------------------------------


def _try_cancel(client, ref):
    try:
        client.cancel(ref)
        return []
    except Exception as exc:  # best effort: report it, do not mask the blocking reason
        return [f"could not cancel the run: {type(exc).__name__}: {exc}"]


def run_release_gate(client, config, *, agent_name, agent_version, deadline_seconds=1800, poll_seconds=15,
                     clock=time.monotonic, sleep=time.sleep):
    """Evaluate the named candidate version now. Never reuses a previous approval or result."""
    try:
        request = build_request(config, agent_name, agent_version)
        _need(deadline_seconds > 0 and poll_seconds > 0, "deadline and poll interval must be positive")
    except ConfigError as exc:
        return _blocked([str(exc)])
    try:
        ref = client.start(request)
    except Exception as exc:
        return _blocked([f"could not start the evaluation: {type(exc).__name__}: {exc}"], request)
    end, last = clock() + deadline_seconds, "not polled"
    while True:
        try:
            snap = client.poll(ref)
        except Exception as exc:
            reasons = [f"could not read the run: {type(exc).__name__}: {exc}"] + _try_cancel(client, ref)
            return _blocked(reasons, request)
        last = snap.status
        if snap.status in TERMINAL:
            break
        if snap.status not in IN_PROGRESS:
            return _blocked([f"run status {snap.status!r} is not recognised"] + _try_cancel(client, ref), request)
        if clock() >= end:
            reasons = [f"timed out after {deadline_seconds}s; last run status {last!r}"] + _try_cancel(client, ref)
            return _blocked(reasons, request)
        sleep(poll_seconds)
    result = judge(request, snap)
    identity = {**result.identity, "run_id": ref.run_id, "eval_id": ref.eval_id}
    return GateResult(result.exit_code, result.verdict, result.reasons, result.scores, identity, result.report_url)


# --- Foundry client (the only code that talks to the service) -------------------------------------


def _get(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _error_text(error):
    if not error:
        return None
    text = _get(error, "message") or _get(error, "code")
    return str(text) if text else None


class FoundryEvalClient:
    """Thin wrapper over the OpenAI-compatible evals API of a Foundry project (`get_openai_client()`)."""

    def __init__(self, oai):
        self._oai = oai

    def start(self, request):
        definition = self._oai.evals.create(
            name=f"{request.agent_name} release gate",
            metadata={"agent": request.agent_name, "pipeline": "foundry-agent-skill"},
            data_source_config={"type": "custom", "include_sample_schema": True, "item_schema": request.item_schema},
            testing_criteria=list(request.criteria),
        )
        run = self._oai.evals.runs.create(
            eval_id=definition.id,
            name=f"{request.agent_name} v{request.agent_version}",
            metadata=dict(request.metadata),
            data_source={
                "type": "azure_ai_target_completions",
                "source": {"type": "file_content", "content": [{"item": dict(i)} for i in request.items]},
                "input_messages": {"type": "template", "template": [{
                    "type": "message", "role": "user",
                    "content": {"type": "input_text", "text": "{{item.query}}"}}]},
                "target": {"type": "azure_ai_agent", "name": request.agent_name,
                           "version": request.agent_version},
            },
        )
        return RunRef(definition.id, run.id)

    def poll(self, ref):
        run = self._oai.evals.runs.retrieve(ref.run_id, eval_id=ref.eval_id)
        definition = self._oai.evals.retrieve(ref.eval_id)
        target = _get(_get(run, "data_source"), "target")
        version = _get(target, "version")
        counts = _get(run, "result_counts")
        return RunSnapshot(
            status=str(_get(run, "status") or ""),
            candidate_name=_get(target, "name"),
            candidate_version=str(version) if version is not None else None,
            dataset_sha256=(_get(run, "metadata") or {}).get("dataset_sha256"),
            evaluators=tuple(_get(c, "name") for c in (_get(definition, "testing_criteria") or [])),
            criteria=tuple(CriterionResult(_get(c, "testing_criteria"), _get(c, "passed"), _get(c, "failed"))
                           for c in (_get(run, "per_testing_criteria_results") or [])),
            errored=int(_get(counts, "errored") or 0),
            report_url=_get(run, "report_url") or None,
            error=_error_text(_get(run, "error")),
        )

    def cancel(self, ref):
        self._oai.evals.runs.cancel(ref.run_id, eval_id=ref.eval_id)


# --- command line -------------------------------------------------------------------------------


def _foundry_client(endpoint):
    from azure.ai.projects import AIProjectClient  # imported late: tests and importers need no Azure setup
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    return FoundryEvalClient(project.get_openai_client())


def _parser():
    parser = argparse.ArgumentParser(description="Fail-closed hosted release evaluation for one candidate version.")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent / "evals.yaml"))
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--agent-version", required=True, help="explicit candidate version, never 'latest'")
    parser.add_argument("--dataset", choices=DATASETS, default="baseline")
    parser.add_argument("--deadline-seconds", type=int, default=1800)
    parser.add_argument("--poll-seconds", type=int, default=15)
    parser.add_argument("--output", help="write the evidence JSON here (no prompts or responses)")
    return parser


def main(argv=None, *, client=None, clock=time.monotonic, sleep=time.sleep, environ=None):
    environ = os.environ if environ is None else environ
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed its message
        return int(exc.code or 0)
    try:
        config = load_config(args.config, args.dataset)
    except ConfigError as exc:
        print(f"BLOCKED (exit 2): {exc}", file=sys.stderr)
        return EXIT_INVALID
    if client is None:
        endpoint = environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
        if not endpoint:
            print("BLOCKED (exit 2): FOUNDRY_PROJECT_ENDPOINT is not set; the evaluation target is unconfigured",
                  file=sys.stderr)
            return EXIT_INVALID
        client = _foundry_client(endpoint)
    result = run_release_gate(client, config, agent_name=args.agent_name, agent_version=args.agent_version,
                              deadline_seconds=args.deadline_seconds, poll_seconds=args.poll_seconds,
                              clock=clock, sleep=sleep)
    evidence = {
        "scope": SCOPE, "verdict": result.verdict, "exit_code": result.exit_code, "reasons": list(result.reasons),
        "scores": result.scores, "identity": result.identity, "report_url": result.report_url,
        "note": "Result of one hosted evaluation run for the named candidate. It is not a promotion.",
    }
    text = json.dumps(evidence, indent=2, sort_keys=True)
    if args.output:
        try:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"BLOCKED (exit 2): cannot write the evidence file: {exc}", file=sys.stderr)
            return EXIT_INVALID
    print(text)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

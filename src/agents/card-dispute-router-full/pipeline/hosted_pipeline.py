# Adapted from github.com/DeepuMM/agent-deployment @ 2220a3c2e3964728aef4444048944db0ebf41ecc
# (scripts/agent_pipeline.py). That repository has no license file; reuse of its design was
# approved by the person running this skill (verbally, not in writing); confirm with the repository's author. Kept: the validate -> candidate -> evaluate -> promote -> smoke
# lifecycle, per-evaluator pass rates against a minimum, the fail-closed gate when an evaluator reports
# nothing, and the run summary. Not copied: the prompt-agent deployment call, the gateway and developer
# portal publishing, the definition hash, skipping evaluation for a previously approved version, and
# the REST calls. They are replaced by the `LifecycleAdapter` interface below, implemented at the
# Foundry CLI/SDK boundary.
"""Hosted-agent release lifecycle (standard library only).

    validate   offline checks of release.json and the agent project (no Azure access)
    check      validate + adapter capability check; exits 3 (BLOCKED) when promotion cannot be made safe
    release    candidate -> evaluate -> session check -> promote -> smoke, rollback on a smoke failure

Every external effect goes through a `LifecycleAdapter`. The shipped `unconfigured` adapter has no
capabilities and refuses every operation, so a freshly generated pipeline is BLOCKED until a reviewed
adapter that can (a) create a candidate version that does not serve traffic and (b) promote it
atomically is supplied. Nothing here talks to Foundry, Azure or GitHub, and the local tests that exercise
this module use a hand-written fake adapter: they show the gate and ordering logic, not that any hosted
pipeline has run.

Exit codes: 0 PROMOTED, 3 BLOCKED, 4 NOT_PROMOTED, 5 ROLLED_BACK, 6 REMEDIATION_REQUIRED, 2 usage or
configuration error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path

PROMOTED, BLOCKED, NOT_PROMOTED, ROLLED_BACK, REMEDIATION_REQUIRED = (
    "PROMOTED", "BLOCKED", "NOT_PROMOTED", "ROLLED_BACK", "REMEDIATION_REQUIRED")
EXIT_CODES = {PROMOTED: 0, BLOCKED: 3, NOT_PROMOTED: 4, ROLLED_BACK: 5, REMEDIATION_REQUIRED: 6}
USAGE_ERROR = 2
COMPLETED = "completed"
ROUTE_SCOPE = "candidate"
REPORT_PATH = Path(".pipeline") / "release-report.json"
REQUIRED_ENV = ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID", "FOUNDRY_PROJECT_ENDPOINT",
                "AZURE_AI_MODEL_DEPLOYMENT_NAME")


class AdapterUnavailable(RuntimeError):
    """The adapter cannot perform this operation (not configured, or unsupported by the platform)."""


class ConfigError(ValueError):
    """The release configuration or project is missing, empty or malformed."""


# --- records exchanged with the adapter ----------------------------------------------------------


@dataclass(frozen=True)
class Capabilities:
    non_serving_candidate: bool = False  # can create a version that serves no traffic
    promotion_boundary: bool = False     # can switch serving to exactly one named version
    route_evidence: bool = False         # can return candidate-scoped route evidence (router releases)
    notes: str = ""


@dataclass(frozen=True)
class Candidate:
    name: str
    version: str
    artifact_id: str


@dataclass(frozen=True)
class RouteEvidence:
    candidate_name: str
    version: str
    scope: str  # must be "candidate": observations from that deployed version, not a local run
    cases_total: int
    cases_correct: int


@dataclass(frozen=True)
class EvaluationResult:
    candidate_name: str
    version: str
    dataset_hash: str
    evaluator_set: tuple
    pass_counts: dict  # evaluator -> (passed, total)
    status: str
    artifact_id: str = ""
    source_id: str = ""
    run_id: str = ""
    criteria_hash: str = ""
    route_evidence: RouteEvidence | None = None


@dataclass(frozen=True)
class SessionEvidence:
    candidate_name: str
    version: str
    manager_enabled: bool | None  # effective Foundry session-manager state of this candidate
    continuity_passed: bool | None  # a hosted multi-turn follow-up needing the earlier turn


@dataclass(frozen=True)
class SmokeResult:
    passed: bool
    checks: int
    version: str = ""  # the version that actually answered
    detail: str = ""


@dataclass(frozen=True)
class ReleaseIdentity:
    agent: str
    environment: str
    run_id: str
    source_id: str
    artifact_id: str
    dataset_hash: str
    evaluators: tuple
    min_pass_rate: float
    case_count: int
    criteria_hash: str
    router: bool = False
    blockers: tuple = ()


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reasons: tuple = ()


@dataclass(frozen=True)
class ReleaseReport:
    status: str
    reasons: tuple = ()
    agent: str = ""
    environment: str = ""
    run_id: str = ""
    source_id: str = ""
    artifact_id: str = ""
    dataset_hash: str = ""
    criteria_hash: str = ""
    evaluators: tuple = ()
    candidate_version: str = ""
    serving_before: str | None = None
    serving_after: str | None = None
    rollback: str = "not-needed"  # not-needed | rolled-back | remediation-required
    evaluator_results: dict | None = None
    adapter: str = ""

    def to_dict(self):
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        data["evaluators"] = list(self.evaluators)
        data["evaluator_results"] = {k: list(v) for k, v in (self.evaluator_results or {}).items()}
        return data


# --- the external boundary -----------------------------------------------------------------------


class LifecycleAdapter(ABC):
    """Everything that touches Foundry, the Azure CLI or an SDK. Implementations live outside this file."""

    name = "abstract"

    @abstractmethod
    def capabilities(self) -> Capabilities:
        """What this adapter can do safely. Unknown means False."""

    @abstractmethod
    def serving_version(self, agent: str) -> str | None:
        """The version currently serving traffic, or None before the first release."""

    @abstractmethod
    def deploy_candidate(self, identity: ReleaseIdentity) -> Candidate:
        """Create a new version of the hosted agent from the built artifact WITHOUT serving traffic."""

    @abstractmethod
    def resolve_candidate_version(self, candidate: Candidate) -> str:
        """Look the candidate's version up from the platform (not from what deploy returned)."""

    @abstractmethod
    def run_evaluation(self, candidate: Candidate, identity: ReleaseIdentity) -> EvaluationResult:
        """Evaluate exactly this candidate (name + version) against the release dataset."""

    @abstractmethod
    def check_session(self, candidate: Candidate) -> SessionEvidence:
        """Read the candidate's effective session-manager state and run a hosted continuity check."""

    @abstractmethod
    def promote(self, candidate: Candidate) -> None:
        """Make exactly this version the serving target."""

    @abstractmethod
    def smoke(self, candidate: Candidate) -> SmokeResult:
        """Exercise the serving target after promotion."""

    @abstractmethod
    def rollback(self, agent: str, to_version: str | None) -> None:
        """Make `to_version` the serving target again."""


class UnconfiguredAdapter(LifecycleAdapter):
    """Default: no hosted lifecycle is configured. Reports no capability and refuses every operation.

    Replace it with a reviewed adapter and register it in `ADAPTERS` (or pass `adapters=` to `main`)
    only after confirming the platform can create a non-serving candidate and promote atomically.
    """

    name = "unconfigured"

    def __init__(self, env=None):
        self.env = env

    def _refuse(self, op):
        raise AdapterUnavailable(
            f"{op}: no hosted lifecycle adapter is configured. Supply a reviewed LifecycleAdapter that "
            "creates a non-serving candidate and promotes one named version; see the pipeline README.")

    def capabilities(self):
        return Capabilities(False, False, False, "no hosted lifecycle adapter configured")

    def serving_version(self, agent):
        self._refuse("serving_version")

    def deploy_candidate(self, identity):
        self._refuse("deploy_candidate")

    def resolve_candidate_version(self, candidate):
        self._refuse("resolve_candidate_version")

    def run_evaluation(self, candidate, identity):
        self._refuse("run_evaluation")

    def check_session(self, candidate):
        self._refuse("check_session")

    def promote(self, candidate):
        self._refuse("promote")

    def smoke(self, candidate):
        self._refuse("smoke")

    def rollback(self, agent, to_version):
        self._refuse("rollback")


ADAPTERS = {"unconfigured": UnconfiguredAdapter}

# --- the promotion gate (pure: no adapter calls) -------------------------------------------------


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _count_pair(value):
    """(passed, total) as exact ints, or None when the shape or values are not credible."""
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    passed, total = value
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (passed, total)):
        return None
    if total <= 0 or not 0 <= passed <= total:
        return None
    return passed, total


def _meets(passed, total, minimum):
    """Exact comparison: `7 >= 0.14 * 50` is False in binary floating point, 7/50 >= Fraction('0.14') is not."""
    return Fraction(passed, total) >= Fraction(str(minimum))


def _check_evaluation(identity, candidate, resolved_version, ev):
    if ev is None:
        return ["evaluation returned no result"]
    if ev.status != COMPLETED:
        return [f"evaluation status is {ev.status!r}, not {COMPLETED!r}"]
    reasons = []
    if ev.candidate_name != identity.agent or ev.candidate_name != candidate.name:
        reasons.append(f"evidence is for candidate {ev.candidate_name!r}, not {identity.agent!r}")
    if not _text(ev.version) or ev.version != resolved_version or ev.version != candidate.version:
        reasons.append(f"evaluated version {ev.version!r} is not the version being promoted {resolved_version!r}")
    for label, got, want in (
        ("dataset", ev.dataset_hash, identity.dataset_hash), ("artifact", ev.artifact_id, identity.artifact_id),
        ("source", ev.source_id, identity.source_id), ("run", ev.run_id, identity.run_id),
        ("criteria", ev.criteria_hash, identity.criteria_hash),
    ):
        if not _text(got):
            reasons.append(f"evidence has no {label} identity")
        elif got != want:
            reasons.append(f"stale or mismatched {label} identity: evidence {got!r}, release {want!r}")
    if candidate.artifact_id != identity.artifact_id:
        reasons.append(f"candidate artifact {candidate.artifact_id!r} is not the built artifact {identity.artifact_id!r}")
    if set(ev.evaluator_set or ()) != set(identity.evaluators):
        reasons.append(f"evaluator set {sorted(ev.evaluator_set or ())} differs from configured {sorted(identity.evaluators)}")
    counts = ev.pass_counts if isinstance(ev.pass_counts, dict) else {}
    for name in identity.evaluators:
        if name not in counts:
            reasons.append(f"evaluator {name!r} reported no result")
            continue
        pair = _count_pair(counts[name])
        if pair is None:
            reasons.append(f"evaluator {name!r} reported an unusable count {counts[name]!r}")
            continue
        passed, total = pair
        if total != identity.case_count:
            reasons.append(f"evaluator {name!r} covered {total} cases, the dataset has {identity.case_count}")
        if not _meets(passed, total, identity.min_pass_rate):
            reasons.append(f"evaluator {name!r} passed {passed}/{total}, below the minimum {identity.min_pass_rate}")
    return reasons


def _check_session(candidate, session):
    if session is None:
        return ["session check returned no evidence"]
    reasons = []
    if session.candidate_name != candidate.name or session.version != candidate.version:
        reasons.append(f"session evidence is for {session.candidate_name!r} v{session.version!r}, "
                       f"not {candidate.name!r} v{candidate.version!r}")
    if session.manager_enabled is not True:
        reasons.append(f"session manager enablement is {session.manager_enabled!r}; it must be confirmed enabled")
    if session.continuity_passed is not True:
        reasons.append(f"session continuity check is {session.continuity_passed!r}; it must pass")
    return reasons


def _check_route(identity, candidate, ev):
    if not identity.router:
        return []
    route = ev.route_evidence if ev is not None else None
    if route is None:
        return ["router release has no candidate-scoped route evidence"]
    reasons = []
    if route.scope != ROUTE_SCOPE:
        reasons.append(f"route evidence scope is {route.scope!r}, not {ROUTE_SCOPE!r} (a local run cannot stand in)")
    if route.candidate_name != candidate.name or route.version != candidate.version:
        reasons.append("route evidence is not for this candidate version")
    if route.cases_total != identity.case_count or route.cases_total <= 0:
        reasons.append(f"route evidence covers {route.cases_total} cases, the dataset has {identity.case_count}")
    elif route.cases_correct != route.cases_total:
        reasons.append(f"route evidence: {route.cases_correct}/{route.cases_total} cases routed correctly")
    return reasons


def evaluate_gate(identity, candidate, resolved_version, evaluation, session):
    """Allow promotion only when every identity matches and every required piece of evidence is present."""
    reasons = _check_evaluation(identity, candidate, resolved_version, evaluation)
    reasons += _check_session(candidate, session)
    reasons += _check_route(identity, candidate, evaluation)
    return GateDecision(not reasons, tuple(reasons))


# --- identities: what must invalidate evidence ---------------------------------------------------

DEPLOYABLE = ("agent.py", "main.py", "requirements.txt", "instructions.md", "azure.yaml", "specialists.json")
_SKIP_PARTS = {"__pycache__", ".venv", ".pipeline", "evals", "pipeline", "docs", "tests", ".git"}


def _sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest_files(root, relpaths):
    h = hashlib.sha256()
    for rel in sorted(relpaths):
        h.update(rel.encode("utf-8") + b"\0" + (Path(root) / rel).read_bytes() + b"\0")
    return "sha256:" + h.hexdigest()


def artifact_digest(agent_root):
    """Identity of what would be deployed: source, dependencies, instructions and hosting config only."""
    root = Path(agent_root)
    if not root.is_dir():
        raise ConfigError(f"{root}: agent project folder not found")
    rels = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if not path.is_file() or set(rel.parts[:-1]) & _SKIP_PARTS or path.suffix in (".pyc", ".pyo"):
            continue
        if rel.parts[-1] in DEPLOYABLE or path.suffix == ".py":
            rels.append(rel.as_posix())
    if not rels:
        raise ConfigError(f"{root}: no deployable source found")
    return digest_files(root, rels)


def pipeline_digest(pipeline_dir):
    folder = Path(pipeline_dir)
    rels = [p.name for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []
    if not rels:
        raise ConfigError(f"{folder}: no pipeline files found")
    return digest_files(folder, rels)


def dataset_hash(path):
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read dataset ({exc.strerror or exc})") from exc
    if not data.strip():
        raise ConfigError(f"{path}: dataset is empty")
    return _sha(data)


def criteria_hash(*, dataset_hash, evaluators, min_pass_rate, judge_model, tool_schema_hash, pipeline_digest):
    """Changes whenever the dataset, evaluator set, threshold, judge, tool schema or pipeline changes."""
    return _sha(_canonical({
        "dataset": dataset_hash, "evaluators": sorted(evaluators), "min_pass_rate": str(min_pass_rate),
        "judge_model": judge_model, "tool_schema": tool_schema_hash, "pipeline": pipeline_digest}))


# --- the release lifecycle -----------------------------------------------------------------------


def _blank(value):
    return not isinstance(value, str) or not value.strip()


def _identity_problems(identity):
    problems = []
    for field in ("agent", "environment", "run_id", "source_id", "artifact_id", "dataset_hash", "criteria_hash"):
        if _blank(getattr(identity, field)):
            problems.append(f"release identity is missing {field}")
    if not identity.evaluators:
        problems.append("no evaluators are configured")
    rate = identity.min_pass_rate
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= 1:
        problems.append(f"min_pass_rate {rate!r} must be a number above 0 and at most 1")
    if isinstance(identity.case_count, bool) or not isinstance(identity.case_count, int) or identity.case_count <= 0:
        problems.append("the dataset has no cases")
    return problems


def _report(identity, adapter, status, reasons=(), **extra):
    return ReleaseReport(
        status=status, reasons=tuple(reasons), agent=identity.agent, environment=identity.environment,
        run_id=identity.run_id, source_id=identity.source_id, artifact_id=identity.artifact_id,
        dataset_hash=identity.dataset_hash, criteria_hash=identity.criteria_hash,
        evaluators=tuple(identity.evaluators), adapter=getattr(adapter, "name", type(adapter).__name__), **extra)


def _capability_blockers(identity, caps):
    reasons = []
    if not caps.non_serving_candidate:
        reasons.append("adapter cannot create a non-serving candidate; deployment would change the serving "
                       "target before evaluation")
    if not caps.promotion_boundary:
        reasons.append("adapter cannot identify a promotion boundary; promotion semantics are unknown")
    if identity.router and not caps.route_evidence:
        reasons.append("router release is blocked until a candidate-scoped route evidence adapter is supplied; "
                       "a local routing gate cannot stand in")
    if caps.notes and reasons:
        reasons.append(f"adapter notes: {caps.notes}")
    return reasons


def release_blockers(adapter, identity):
    """Why this release must not start (empty list: it may). Touches the adapter only to read capabilities."""
    config = list(identity.blockers) + _identity_problems(identity)
    if config:
        return config
    try:
        caps = adapter.capabilities()
    except Exception as exc:  # noqa: BLE001
        return [f"adapter capability probe failed ({type(exc).__name__}: {exc})"]
    return _capability_blockers(identity, caps)


def _evaluator_results(ev):
    counts = getattr(ev, "pass_counts", None)
    if not isinstance(counts, dict):
        return None
    return {k: tuple(v) for k, v in counts.items() if _count_pair(v) is not None}


def _failed(identity, adapter, reasons, before, **extra):
    """A release that stopped before promotion: re-read serving so the report shows what is actually live."""
    try:
        after = adapter.serving_version(identity.agent)
    except Exception:  # noqa: BLE001 - the report must still be produced
        after = before
    return _report(identity, adapter, NOT_PROMOTED, reasons, serving_before=before, serving_after=after, **extra)


def _roll_back(identity, adapter, candidate, before, reasons, ev):
    """Smoke (or promote bookkeeping) failed after serving moved: restore the previous version or say so."""
    common = dict(candidate_version=candidate.version, serving_before=before, evaluator_results=_evaluator_results(ev))
    if before is None or before == candidate.version:
        return _report(identity, adapter, REMEDIATION_REQUIRED,
                       reasons + ["no distinct previous version to restore; manual remediation required"],
                       serving_after=candidate.version, rollback="remediation-required", **common)
    try:
        adapter.rollback(identity.agent, before)
        now = adapter.serving_version(identity.agent)
    except Exception as exc:  # noqa: BLE001
        return _report(identity, adapter, REMEDIATION_REQUIRED,
                       reasons + [f"rollback to {before!r} failed ({type(exc).__name__}); manual remediation required"],
                       serving_after=candidate.version, rollback="remediation-required", **common)
    if now != before:
        return _report(identity, adapter, REMEDIATION_REQUIRED,
                       reasons + [f"rollback did not restore {before!r} (serving is {now!r}); manual remediation required"],
                       serving_after=now, rollback="remediation-required", **common)
    return _report(identity, adapter, ROLLED_BACK, reasons + [f"rolled back to {before!r}"],
                   serving_after=now, rollback="rolled-back", **common)


def _smoke_problem(candidate, smoke):
    """None when the serving target passed with real checks and the promoted version answered."""
    problems = []
    if smoke.passed is not True:
        problems.append("a smoke check failed")
    if not isinstance(smoke.checks, int) or isinstance(smoke.checks, bool) or smoke.checks <= 0:
        problems.append(f"smoke ran {smoke.checks!r} checks")
    if smoke.version != candidate.version:
        problems.append(f"version {smoke.version!r} answered, not the promoted {candidate.version!r}")
    return "smoke failed: " + "; ".join(problems) if problems else None


def run_release(adapter, identity):
    """candidate -> evaluate exactly that candidate -> session check -> promote -> smoke the serving target."""
    blocked = release_blockers(adapter, identity)
    if blocked:
        return _report(identity, adapter, BLOCKED, blocked)
    try:
        before = adapter.serving_version(identity.agent)
    except Exception as exc:  # noqa: BLE001
        return _report(identity, adapter, BLOCKED, [f"cannot read the serving version ({type(exc).__name__}: {exc})"])

    try:
        candidate = adapter.deploy_candidate(identity)
        live = adapter.serving_version(identity.agent)
    except Exception as exc:  # noqa: BLE001
        return _failed(identity, adapter, [f"candidate deployment failed ({type(exc).__name__}: {exc})"], before)
    if live != before:
        return _report(identity, adapter, REMEDIATION_REQUIRED,
                       [f"serving changed from {before!r} to {live!r} while creating the candidate; the adapter is "
                        "not non-serving. Restore the previous version manually"],
                       candidate_version=candidate.version, serving_before=before, serving_after=live,
                       rollback="remediation-required")
    try:
        resolved = adapter.resolve_candidate_version(candidate)
    except Exception as exc:  # noqa: BLE001
        return _failed(identity, adapter, [f"candidate version lookup failed ({type(exc).__name__}: {exc})"], before,
                       candidate_version=candidate.version)
    if _blank(resolved) or resolved != candidate.version:
        return _failed(identity, adapter, [f"resolved candidate version {resolved!r} differs from the deployed "
                                           f"version {candidate.version!r}"], before, candidate_version=candidate.version)

    try:
        evaluation = adapter.run_evaluation(candidate, identity)
    except Exception as exc:  # noqa: BLE001
        return _failed(identity, adapter, [f"evaluation did not complete ({type(exc).__name__}: {exc})"], before,
                       candidate_version=candidate.version)
    try:
        session = adapter.check_session(candidate)
    except Exception as exc:  # noqa: BLE001
        session = None
        session_error = f"session check failed ({type(exc).__name__}: {exc})"
    else:
        session_error = None
    decision = evaluate_gate(identity, candidate, resolved, evaluation, session)
    reasons = list(decision.reasons) + ([session_error] if session_error else [])
    if not decision.allowed or session_error:
        return _failed(identity, adapter, reasons, before, candidate_version=candidate.version,
                       evaluator_results=_evaluator_results(evaluation))

    common = dict(candidate_version=candidate.version, serving_before=before,
                  evaluator_results=_evaluator_results(evaluation))
    try:
        adapter.promote(candidate)
        after = adapter.serving_version(identity.agent)
    except Exception as exc:  # noqa: BLE001
        try:
            after = adapter.serving_version(identity.agent)
        except Exception:  # noqa: BLE001
            after = None
        why = [f"promotion failed ({type(exc).__name__}: {exc})"]
        if after == candidate.version:  # the switch happened before the error: undo it
            return _roll_back(identity, adapter, candidate, before, why, evaluation)
        return _report(identity, adapter, NOT_PROMOTED, why, serving_after=after, **common)
    if after != candidate.version:
        return _report(identity, adapter, NOT_PROMOTED,
                       [f"promote returned but serving is {after!r}, not {candidate.version!r}"],
                       serving_after=after, **common)

    try:
        smoke_problem = _smoke_problem(candidate, adapter.smoke(candidate))
    except Exception as exc:  # noqa: BLE001
        smoke_problem = f"smoke test raised {type(exc).__name__}: {exc}"
    if smoke_problem:
        return _roll_back(identity, adapter, candidate, before, [smoke_problem], evaluation)
    return _report(identity, adapter, PROMOTED, [], serving_after=after, **common)


# --- release configuration and the command line --------------------------------------------------

RELEASE_JSON = Path("pipeline") / "release.json"
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENV_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def load_release_config(path):
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read release configuration ({exc.strerror or exc})") from exc
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(cfg, dict) or cfg.get("schema") != 1:
        raise ConfigError(f"{path}: expected a mapping with schema 1")
    for key in ("agent", "adapter", "dataset_path"):
        if not _text(cfg.get(key)):
            raise ConfigError(f"{path}: {key} must be a non-blank string")
    if not isinstance(cfg.get("evaluators", []), list) or not isinstance(cfg.get("blockers", []), list):
        raise ConfigError(f"{path}: evaluators and blockers must be lists")
    return cfg


def _config_problems(cfg):
    """Unconfigured values that block a release. Wording matches the generator so duplicates collapse."""
    problems = [str(b) for b in cfg.get("blockers") or []]
    if not _text(cfg.get("judge_model")):
        problems.append("evaluation.judge_model is unconfigured")
    if not cfg.get("evaluators"):
        problems.append("evaluation.evaluators is unconfigured")
    if cfg.get("min_pass_rate") is None:
        problems.append("evaluation.min_pass_rate is unconfigured")
    if cfg.get("dataset_status") != "approved":
        problems.append("evaluation dataset is not approved")
    return list(dict.fromkeys(problems))


def build_identity(config, agent_root, env, *, agent=None, environment=None):
    """Assemble the release identity from the config, the project on disk and the CI environment."""
    root = Path(agent_root)
    agent = config["agent"] if agent is None else agent
    if agent != config["agent"]:
        raise ConfigError(f"dispatched agent {agent!r} is not this project's agent {config['agent']!r}")
    if not _NAME.match(agent):
        raise ConfigError(f"agent {agent!r} must be kebab-case")
    if not isinstance(environment, str) or not _ENV_NAME.match(environment):
        raise ConfigError(f"environment {environment!r} is not a valid GitHub environment name")
    source = env.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ConfigError("GITHUB_SHA is missing or not a 40-character commit id; the source cannot be identified")
    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    if not (run_id.isdigit() and attempt.isdigit()):
        raise ConfigError("GITHUB_RUN_ID / GITHUB_RUN_ATTEMPT are missing; this run cannot be identified")
    data = dataset_hash(root / config["dataset_path"])
    evaluators = tuple(config.get("evaluators") or ())
    rate = config.get("min_pass_rate")
    crit = criteria_hash(
        dataset_hash=data, evaluators=evaluators, min_pass_rate=rate, judge_model=config.get("judge_model") or "",
        tool_schema_hash=_sha(_canonical(config.get("tools") or [])),
        pipeline_digest=pipeline_digest(root / "pipeline"))
    return ReleaseIdentity(
        agent=agent, environment=environment, run_id=f"{run_id}-{attempt}", source_id=source,
        artifact_id=artifact_digest(root), dataset_hash=data, evaluators=evaluators,
        min_pass_rate=rate, case_count=int(config.get("case_count") or 0), criteria_hash=crit,
        router=bool(config.get("router")), blockers=tuple(_config_problems(config)))


def _missing_env(env):
    return [name for name in REQUIRED_ENV if not str(env.get(name, "")).strip()]


def _summary(env, text):
    print(text)
    target = env.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")


def _write_report(root, report):
    path = Path(root) / REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _format(report):
    lines = [f"**{report.agent}** ({report.environment}) release: {report.status}"]
    lines += [f"- {r}" for r in report.reasons]
    if report.candidate_version:
        lines.append(f"- candidate version {report.candidate_version}; serving {report.serving_before} -> "
                     f"{report.serving_after}; rollback: {report.rollback}")
    for name, (passed, total) in sorted((report.evaluator_results or {}).items()):
        lines.append(f"- {name}: {passed}/{total}")
    return "\n".join(lines)


def _parser():
    p = argparse.ArgumentParser(prog="hosted_pipeline.py", description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["validate", "check", "release"])
    p.add_argument("--root", default=".", help="the agent project folder (default: current folder)")
    p.add_argument("--agent", help="agent to release (must match release.json)")
    p.add_argument("--environment", help="GitHub environment being released to")
    return p


def _validate(root, config):
    """Offline: the configuration and project files must be readable. Unconfigured values are reported, not fatal."""
    placeholder_run = {"GITHUB_SHA": "0" * 40, "GITHUB_RUN_ID": "0", "GITHUB_RUN_ATTEMPT": "0"}
    build_identity(config, root, placeholder_run, environment="validate")
    print(f"{config['agent']}: release configuration and project files are readable")
    for problem in _config_problems(config):
        print(f"unconfigured: {problem}")
    if config["adapter"] == "unconfigured":
        print("unconfigured: no hosted lifecycle adapter; a release stays BLOCKED until one is supplied")
    return 0


def main(argv=None, adapters=None, env=None):
    args = _parser().parse_args(argv)
    env = dict(os.environ) if env is None else dict(env)
    registry = {**ADAPTERS, **(adapters or {})}
    root = Path(args.root)
    try:
        config = load_release_config(root / RELEASE_JSON)
        if args.command == "validate":
            return _validate(root, config)
        missing = _missing_env(env)
        if missing:
            raise ConfigError("missing environment values: " + ", ".join(missing))
        identity = build_identity(config, root, env, agent=args.agent, environment=args.environment)
        factory = registry.get(config["adapter"])
        if factory is None:
            raise ConfigError(f"adapter {config['adapter']!r} is not registered")
        adapter = factory(env)
        if args.command == "check":
            blocked = release_blockers(adapter, identity)
            if blocked:
                _summary(env, _format(_report(identity, adapter, BLOCKED, blocked)))
                return EXIT_CODES[BLOCKED]
            _summary(env, f"**{identity.agent}** ({identity.environment}) check passed: the adapter can create a "
                          "non-serving candidate and promote one named version. Nothing was deployed.")
            return 0
        report = run_release(adapter, identity)
        _write_report(root, report)
        _summary(env, _format(report))
        return EXIT_CODES[report.status]
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return USAGE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

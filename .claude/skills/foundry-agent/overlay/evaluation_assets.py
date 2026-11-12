"""Hosted evaluation assets (plan Task 3b): one EvalPlan rendered to every evaluation file.

evals/evals.yaml is the canonical release-evaluation intent (fields as in the pinned reference,
github.com/DeepuMM/agent-deployment @ 2220a3c2e3964728aef4444048944db0ebf41ecc: judge_model, min_pass_rate,
evaluators, dataset, plus case ids and, for routers, expected routes). evals/tests.yaml records smoke intent.
The JSONL datasets are derived from the same plan, never edited by hand as a second truth.

The tree is composed from an explicit source manifest of new files, never by editing the legacy
run_evaluation.py. Everything is preflighted (spec, evaluators, sources, destinations) before the first
write; the source tree is never mutated and caches or bytecode are never copied. An evals-disabled spec
produces nothing. Missing operational values (judge model, threshold, evaluators, approved cases) are
rendered as explicit UNCONFIGURED markers and block execution at the gate; nothing is invented.
Generating files proves nothing about a deployed agent.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from agent_spec import PATTERNS, SpecError, yaml_scalar

SKILL_ROOT = Path(__file__).resolve().parent.parent
SMOKE_CAP = 5
SMOKE_MIN = 2
REFUSAL_ROUTE = "refusal"
REFERENCE = "github.com/DeepuMM/agent-deployment @ 2220a3c2e3964728aef4444048944db0ebf41ecc"

# The gate's supported evaluators; kept in step with SUPPORTED_EVALUATORS in run_release_evaluation.py by a test.
SUPPORTED_EVALUATORS = ("relevance", "coherence", "fluency", "intent_resolution", "task_adherence", "groundedness")
NEEDS_CONTEXT = ("groundedness",)

_EVALS = ("hosted", "assets", "evals")
_ROUTER = ("hosted", "patterns", "router-specialists")
# destination (relative to the output root) -> source (relative to the skill root). Explicit; no globbing.
SHARED_SOURCES = {
    "evals/run_release_evaluation.py": (*_EVALS, "run_release_evaluation.py"),
    "evals/tests/test_release_evaluation.py": (*_EVALS, "tests", "test_release_evaluation.py"),
    "evals/.gitignore": (*_EVALS, ".gitignore"),
}
ROUTER_SOURCES = {
    "run_routing_evaluation.py": (*_ROUTER, "run_routing_evaluation.py"),
    "tests/test_routing_evaluation.py": (*_ROUTER, "tests", "test_routing_evaluation.py"),
}
REQUIREMENTS = (
    "# Declared dependencies for the generated evaluation tree (run the tests with these installed).\n"
    "azure-ai-projects>=2.9.0\n"
    "azure-identity>=1.15.0\n"
    "pyyaml>=6.0\n"
    "pytest>=8\n"
)


class EvalAssetError(SpecError):
    """The evaluation tree cannot be rendered; nothing was written."""


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    query: str
    expected_route: str | None = None
    context: str | None = None


@dataclass(frozen=True)
class EvalPlan:
    agent: str
    pattern: str
    judge_model: str | None
    min_pass_rate: float | None
    evaluators: tuple
    dataset_status: str
    cases: tuple
    smoke_ids: tuple
    routes: tuple = ()
    omitted_specialists: tuple = ()
    missing_out_of_scope: bool = False
    warnings: tuple = ()


@dataclass(frozen=True)
class RenderResult:
    files: tuple
    plan: EvalPlan | None


def _norm(text):
    return " ".join(text.split())


def _case_id(position):
    return f"c{position:03d}"


def check_agent(spec):
    """The checks every renderer shares. Raises EvalAssetError; never writes."""
    if spec.agent_kind != "hosted":
        raise EvalAssetError("evaluation assets are generated for hosted agents only")
    if spec.framework != "maf":
        raise EvalAssetError(f"framework {spec.framework!r}: evaluation assets target maf-hosted agents only")
    if PATTERNS[spec.pattern].status != "runnable-unvalidated":
        raise EvalAssetError(f"pattern {spec.pattern!r} is design-only and has no evaluation assets")
    unsupported = [e for e in spec.evaluation.evaluators if e not in SUPPORTED_EVALUATORS]
    if unsupported:
        raise EvalAssetError(f"evaluators {unsupported} are not supported by the release gate; "
                             f"choose from {', '.join(SUPPORTED_EVALUATORS)}")


def check_context(plan, evaluators):
    for name in evaluators:
        if name in NEEDS_CONTEXT and not all(c.context for c in plan.cases):
            raise EvalAssetError(f"{name} needs approved context on every case; instructions alone are not "
                                 "policy evidence")


# --- plan -----------------------------------------------------------------------------------------


def _single_agent_plan(spec):
    cases = tuple(
        EvalCase(_case_id(i), c["query"], None, c.get("context"))
        for i, c in enumerate(spec.evaluation.cases, start=1))
    return _with_config(spec, cases, tuple(c.case_id for c in cases[:SMOKE_CAP]))


def _with_config(spec, cases, smoke_ids, **extra):
    cfg = spec.evaluation
    return EvalPlan(
        agent=spec.name, pattern=spec.pattern, judge_model=cfg.judge_model or None,
        min_pass_rate=cfg.min_pass_rate, evaluators=tuple(cfg.evaluators), dataset_status=cfg.dataset_status,
        cases=cases, smoke_ids=smoke_ids, **extra)


def build_plan(spec, skill_root=SKILL_ROOT):
    """The single model every evaluation file is rendered from. Raises EvalAssetError; never writes."""
    check_agent(spec)
    if spec.pattern == "router-specialists":
        from router_assets import build_router_plan
        plan = build_router_plan(spec, skill_root)
    else:
        plan = _single_agent_plan(spec)
    check_context(plan, spec.evaluation.evaluators)
    return plan


# --- renderers ------------------------------------------------------------------------------------

UNCONFIGURED = "UNCONFIGURED"


def _scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return _escape_c1(yaml_scalar(value))


def _escape_c1(rendered):
    """yaml_scalar leaves U+007F-U+009F as raw characters; YAML rejects DEL and folds NEL. Escape them."""
    return "".join("\\u%04x" % ord(ch) if 0x7F <= ord(ch) <= 0x9F else ch for ch in rendered)


def _flow_list(items):
    return "[" + ", ".join(_scalar(i) for i in items) + "]"


def _marker(configured, what):
    return "" if configured else f"  # {UNCONFIGURED}: {what}; the release gate blocks until it is set\n"


def render_evals_yaml(plan):
    router = plan.pattern == "router-specialists"
    lines = [
        f"# Canonical release-evaluation intent for {plan.agent}. Fields follow {REFERENCE}.\n",
        "# The JSONL files under datasets/ are derived from this plan; edit the spec, not them.\n",
        f"agent: {_scalar(plan.agent)}\n",
        f"pattern: {_scalar(plan.pattern)}\n",
        f"judge_model: {_scalar(plan.judge_model)}" + (
            "\n" if plan.judge_model else f"  # {UNCONFIGURED}: set the judge deployment; the gate blocks until then\n"),
        f"min_pass_rate: {_scalar(plan.min_pass_rate)}" + (
            "\n" if plan.min_pass_rate is not None
            else f"  # {UNCONFIGURED}: set the approved threshold; the gate blocks until then\n"),
        f"evaluators: {_flow_list(plan.evaluators)}" + (
            "\n" if plan.evaluators else f"  # {UNCONFIGURED}: list the evaluators to gate on\n"),
        f"dataset_status: {_scalar(plan.dataset_status)}\n",
    ]
    if router:
        lines.append(f"routes: {_flow_list(plan.routes)}\n")
    lines.append(f"smoke: {_flow_list(plan.smoke_ids)}\n")
    if plan.cases:
        lines.append("dataset:\n")
    else:
        lines.append(f"dataset: []  # {UNCONFIGURED}: no approved cases yet\n")
    for case in plan.cases:
        lines.append(f"  - case_id: {_scalar(case.case_id)}\n")
        lines.append(f"    query: {_scalar(case.query)}\n")
        if router:
            lines.append(f"    expected_route: {_scalar(case.expected_route)}\n")
        if case.context:
            lines.append(f"    context: {_scalar(case.context)}\n")
    return "".join(lines)


def render_tests_yaml(plan):
    router = plan.pattern == "router-specialists"
    lines = [
        f"# Smoke intent for {plan.agent} (fields as in {REFERENCE}: input, must_contain_any).\n",
        "# This is NOT a security, financial-policy or multi-turn correctness gate: substring matching can pass\n",
        "# a wrong answer. Add explicit domain and negative checks before relying on it. Left empty, a smoke\n",
        f"# check has nothing to assert ({UNCONFIGURED}): approve expected phrases per case.\n",
    ]
    if not plan.cases:
        return "".join(lines) + "[]\n"
    by_id = {c.case_id: c for c in plan.cases}
    for case_id in plan.smoke_ids or tuple(by_id):
        case = by_id[case_id]
        lines.append(f"- case_id: {_scalar(case.case_id)}\n")
        lines.append(f"  input: {_scalar(case.query)}\n")
        if router:
            lines.append(f"  expected_route: {_scalar(case.expected_route)}\n")
        lines.append("  must_contain_any: []\n")
    return "".join(lines)


def render_jsonl(plan, smoke=False):
    """Derived dataset. Newline-delimited, UTF-8, one record per case, in plan order."""
    wanted = set(plan.smoke_ids) if smoke else None
    out = []
    for case in plan.cases:
        if wanted is not None and case.case_id not in wanted:
            continue
        row = {"case_id": case.case_id, "query": case.query}
        if plan.pattern == "router-specialists":
            row["expected_route"] = case.expected_route
        if case.context:
            row["context"] = case.context
        out.append(json.dumps(row, ensure_ascii=False) + "\n")
    return "".join(out)


# --- documentation --------------------------------------------------------------------------------


def render_readme(plan):
    router = plan.pattern == "router-specialists"
    unconfigured = []
    if not plan.judge_model:
        unconfigured.append("judge model")
    if plan.min_pass_rate is None:
        unconfigured.append("minimum pass rate")
    if not plan.evaluators:
        unconfigured.append("evaluators")
    if plan.dataset_status != "approved":
        unconfigured.append("dataset approval")
    if not plan.cases:
        unconfigured.append("approved cases")
    lines = [
        f"# {plan.agent} evaluation\n\n",
        "Generated from the confirmed agent spec. `evals.yaml` is the canonical release-evaluation intent; the "
        "files under `datasets/` are derived from the same plan, so edit the spec and regenerate rather than "
        "editing them.\n\n",
        f"Adapted from {REFERENCE}: judge model, minimum pass rate, explicit evaluators and dataset, with "
        "`tests.yaml` for smoke intent. The reference's APIM and prompt-agent deployment steps are not part of "
        "this tree.\n\n",
        "## Status of operational values\n\n",
    ]
    if unconfigured:
        lines.append(f"{UNCONFIGURED}: {', '.join(unconfigured)}. The release gate blocks (exit 2) until these "
                     "are set from approved project decisions. Nothing here supplies a default.\n\n")
    else:
        lines.append("Every operational value is set in `evals.yaml`. They are the project's confirmed choices, "
                     "not an organisational standard.\n\n")
    lines += [
        "## Files\n\n",
        "| File | Purpose |\n| --- | --- |\n",
        "| `evals.yaml` | canonical intent: judge model, min pass rate, evaluators, cases |\n",
        "| `tests.yaml` | smoke intent; substring checks are not a security or policy gate |\n",
        "| `datasets/baseline.jsonl` | every approved case once, with a stable `case_id` |\n",
        "| `datasets/ci-smoke.jsonl` | a capped subset of the baseline |\n",
        "| `run_release_evaluation.py` | fail-closed hosted gate for one explicit candidate name and version |\n",
        "| `tests/` | offline tests of the gate; no Azure access |\n",
    ]
    if router:
        lines += [
            "| `../run_routing_evaluation.py` | deterministic local routing gate |\n",
            "| `tests/test_routing_evaluation.py` | offline tests of the routing gate |\n",
        ]
    lines += [
        "\n## Running\n\n```\npip install -r requirements.txt\npython -m pytest tests -q\n"
        "python run_release_evaluation.py --agent-name <name> --agent-version <explicit version>\n```\n\n"
        "The hosted gate needs `FOUNDRY_PROJECT_ENDPOINT` and an authorised sign-in. It evaluates exactly the "
        "named version every time, rejects results for another candidate, dataset or evaluator set, and exits "
        "0 (pass), 1 (a threshold was missed) or 2 (invalid configuration or evidence). A result is evidence "
        "for that one run; it does not promote anything.\n\n",
    ]
    if router:
        lines += [
            "## Routing\n\n",
            "`run_routing_evaluation.py` runs each case through the router and compares the route that "
            "actually ran with `expected_route` (`refusal` is the reserved refusal route). The route comes "
            "from execution, never from answer text. It does not certify a deployed router: that needs a "
            "validated route-evidence adapter that correlates observations to the deployed candidate, and "
            "generic quality scores are not routing accuracy. Hosted release of a router therefore stays "
            "blocked (exit 2) on quality scores alone.\n\n",
        ]
        for warning in plan.warnings:
            lines.append(f"- Coverage: {warning}\n")
        if plan.warnings:
            lines.append("\n")
    return "".join(lines)


# --- tree -----------------------------------------------------------------------------------------


def _generated(plan):
    files = {
        "evals/evals.yaml": render_evals_yaml(plan),
        "evals/tests.yaml": render_tests_yaml(plan),
        "evals/datasets/baseline.jsonl": render_jsonl(plan),
        "evals/datasets/ci-smoke.jsonl": render_jsonl(plan, smoke=True),
        "evals/requirements.txt": REQUIREMENTS,
        "evals/README.md": render_readme(plan),
    }
    return files


def _sources(plan, skill_root):
    manifest = dict(SHARED_SOURCES)
    if plan.pattern == "router-specialists":
        manifest.update(ROUTER_SOURCES)
    resolved, missing = {}, []
    for dest, parts in manifest.items():
        src = Path(skill_root).joinpath(*parts)
        if src.is_file():
            resolved[dest] = src
        else:
            missing.append("/".join(parts))
    if missing:
        raise EvalAssetError(f"required source asset(s) missing from the skill: {', '.join(missing)}")
    return resolved


def _contents(spec, skill_root):
    """(plan, {relative path: bytes}) after every check; touches no output directory."""
    if not spec.evals_enabled:
        return None, {}
    plan = build_plan(spec, skill_root)
    generated = _generated(plan)
    copied = _sources(plan, skill_root)
    contents = {**generated, **{dest: src.read_bytes() for dest, src in copied.items()}}
    encoded = {}
    for rel, data in contents.items():
        try:
            encoded[rel] = data.encode("utf-8") if isinstance(data, str) else data
        except UnicodeEncodeError as exc:
            raise EvalAssetError(f"{rel}: text cannot be written as UTF-8 ({exc.reason})") from exc
    return plan, encoded


def eval_files(spec, *, skill_root=SKILL_ROOT):
    """Relative path -> bytes for the whole evaluation tree. Empty when evals are disabled; touches no disk.

    The scaffold merges these with every other generated file into one all-or-nothing write.
    render_eval_tree writes exactly this content, so the two cannot drift apart.
    """
    return _contents(spec, skill_root)[1]


def render_eval_tree(spec, out_dir, *, skill_root=SKILL_ROOT):
    """Write the evaluation tree under out_dir; returns the files written. All checks run before any write."""
    plan, encoded = _contents(spec, skill_root)
    if plan is None:
        return RenderResult((), None)
    out = Path(out_dir)
    if out.exists() and not out.is_dir():
        raise EvalAssetError(f"{out}: the output path exists and is not a directory")
    conflicts = [rel for rel in sorted(encoded) if (out / rel).exists()]
    if conflicts:
        raise EvalAssetError(f"refusing to overwrite existing file(s): {', '.join(conflicts)}")
    blocked = sorted({"/".join(parts[:i]) for rel in encoded for parts in [rel.split("/")]
                      for i in range(1, len(parts)) if (out.joinpath(*parts[:i])).exists()
                      and not out.joinpath(*parts[:i]).is_dir()})
    if blocked:
        raise EvalAssetError(f"a file sits where a directory is needed: {', '.join(blocked)}")
    written = []
    try:
        for rel in sorted(encoded):
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded[rel])
            written.append(target)
    except OSError as exc:
        raise EvalAssetError(f"cannot write {out}: {exc.strerror or exc}") from exc
    return RenderResult(tuple(written), plan)

"""Agent spec and pattern registry for the foundry-agent skill (plan Task 1).

Structural validation only. Declared values (auth modes, session state, framework) are what the
user said, not verified facts. Frozen dataclasses, no config framework.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


class SpecError(ValueError):
    """The spec is structurally invalid or incompatible with the requested mode."""


class UnsupportedPattern(SpecError):
    """Unknown pattern id, or a design-only pattern requested for an executable mode."""


@dataclass(frozen=True)
class Pattern:
    status: str  # runnable-unvalidated | design-only
    summary: str


PATTERNS = {
    "single-agent": Pattern("runnable-unvalidated", "One agent solves the task with instructions and optional tools."),
    "router-specialists": Pattern("runnable-unvalidated", "Pick one independent tool-less specialist per request."),
    "supervisor-worker": Pattern("design-only", "Coordinator delegates, combines results or iterates with workers."),
    "agent-handoffs": Pattern("design-only", "Another agent takes responsibility for the conversation."),
}

CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
AGENT_KINDS = ("prompt", "hosted")
FRAMEWORKS = ("maf", "not-applicable", "legacy-langgraph", "legacy-other")
SESSION_STATES = ("enabled", "disabled", "unknown", "not-applicable")
AUTH_MODES = ("none", "managed-identity", "user-token", "api-key", "oauth-connection")
DATASET_STATUSES = ("proposed", "approved")
MODES = ("create-new", "overlay-existing", "design-only")
# Router internals: the refusal route label plus the executor ids the MAF router template uses.
RESERVED_IDS = ("refusal", "router", "unknown", "classifier", "normalize", "refuse")
RESERVED_PREFIXES = ("dispatch-",)
MIN_ROUTER_QUERIES = 2
MIN_RELEASE_QUERIES = 3

_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_ID = 63


@dataclass(frozen=True)
class ToolSpec:
    name: str
    auth: str
    requires_approval: bool  # a declared requirement, not an enforcement


@dataclass(frozen=True)
class SpecialistSpec:
    name: str
    instructions: str
    examples: tuple = ()  # verbatim, in order


@dataclass(frozen=True)
class EvaluationConfig:
    judge_model: str = ""
    min_pass_rate: float | None = None
    evaluators: tuple = ()
    dataset_status: str = "proposed"
    cases: tuple = ()  # verbatim mappings, in order


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    owner: str
    classification: str
    agent_kind: str
    framework: str
    pattern: str
    pattern_rationale: str
    model: str
    hosted_session_manager: str
    tools: tuple = ()
    specialists: tuple = ()
    out_of_scope_examples: tuple = ()
    evals_enabled: bool = False
    pipeline_enabled: bool = False
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    runtime: str = "unspecified"  # recorded selection, not verified compatibility
    resource: dict = field(default_factory=dict)
    evaluation_configured: bool = False


# --- helpers ----------------------------------------------------------------------------------


def _text(data, key, *, required=True):
    value = data.get(key)
    if value is None or not isinstance(value, str) or not value.strip():
        if required:
            raise SpecError(f"{key}: required, must be a non-blank string")
        return ""
    return value.strip()


def _choice(data, key, allowed, default=None):
    value = data.get(key, default)
    if value not in allowed:
        raise SpecError(f"{key}: {value!r} is not one of {', '.join(allowed)}")
    return value


def _flag(data, key):
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise SpecError(f"{key}: must be true or false, got {value!r}")
    return value


def _kebab(value, label):
    v = value.strip().lower()
    if len(v) > _MAX_ID or not _KEBAB.match(v):
        raise SpecError(f"{label}: {value!r} must be kebab-case (a-z, 0-9, single hyphens, <= {_MAX_ID} chars)")
    return v


def _is_text(value):
    return isinstance(value, str) and bool(value.strip())


def _strings(value, label):
    """A list of non-blank strings, kept verbatim and in order."""
    if not isinstance(value, list) or not all(_is_text(v) for v in value):
        raise SpecError(f"{label}: must be a list of non-blank strings")
    return tuple(value)


def _list(data, key):
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError(f"{key}: must be a list")
    return value


# JSON leaves these raw, but YAML either rejects them (DEL, C1 controls) or folds them into a space
# (NEL, LS, PS), so a JSON string is not always a faithful YAML scalar. They are written as unicode escapes.
_YAML_UNSAFE = re.compile("[\x7f-\x9f\u2028\u2029]")


def yaml_scalar(text):
    """Render text as a YAML double-quoted scalar that round-trips every character unchanged."""
    return _YAML_UNSAFE.sub(lambda m: "\\u%04x" % ord(m.group()), json.dumps(text, ensure_ascii=False))


# --- parsing ----------------------------------------------------------------------------------


def _tool(raw):
    if not isinstance(raw, dict):
        raise SpecError("tools: each tool must be a mapping")
    return ToolSpec(
        name=_text(raw, "name"),
        auth=_choice(raw, "auth", AUTH_MODES, "none"),
        requires_approval=_flag(raw, "requires_approval"),
    )


def _specialist(raw):
    if not isinstance(raw, dict):
        raise SpecError("specialists: each specialist must be a mapping")
    name = _kebab(_text(raw, "name"), "specialist name")
    if name in RESERVED_IDS or name.startswith(RESERVED_PREFIXES):
        raise SpecError(f"specialist name {name!r} is reserved ({', '.join(RESERVED_IDS)}, "
                        f"or starting with {', '.join(RESERVED_PREFIXES)})")
    examples = _strings(_list(raw, "examples"), f"specialist {name!r} examples")
    return SpecialistSpec(name=name, instructions=_text(raw, "instructions"), examples=examples)


def _case(entry, index):
    if not isinstance(entry, dict) or not _is_text(entry.get("query")):
        raise SpecError(f"evaluation.cases[{index}]: must be a mapping with a non-blank string query")
    if "expected_route" in entry and not isinstance(entry["expected_route"], str):
        raise SpecError(f"evaluation.cases[{index}].expected_route: must be a string")
    return entry


def _evaluation(raw):
    if raw is None:
        return EvaluationConfig(), False
    if not isinstance(raw, dict):
        raise SpecError("evaluation: must be a mapping")
    rate = raw.get("min_pass_rate")
    if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 1):
        raise SpecError("evaluation.min_pass_rate: must be a number from 0 to 1")
    judge = raw.get("judge_model")
    if judge is not None and not isinstance(judge, str):
        raise SpecError("evaluation.judge_model: must be a string")
    evaluators = raw.get("evaluators")
    cases = raw.get("cases")
    cfg = EvaluationConfig(
        judge_model=(judge or "").strip(),
        min_pass_rate=rate,
        evaluators=_strings(evaluators, "evaluation.evaluators") if evaluators is not None else (),
        dataset_status=_choice(raw, "dataset_status", DATASET_STATUSES, "proposed"),
        cases=_cases(cases) if cases is not None else (),
    )
    return cfg, True


def _cases(cases):
    if not isinstance(cases, list):
        raise SpecError("evaluation.cases: must be a list of mappings")
    return tuple(_case(c, i) for i, c in enumerate(cases))


def _norm_query(text):
    """Whitespace-normalized form used only to compare queries; stored text stays verbatim."""
    return " ".join(text.split())


def _check_query_consistency(specialists, out_of_scope, cases, router):
    """Reject a query that appears twice, or that implies two different routes, anywhere in the spec.

    Sources are specialist examples (route = that specialist), out-of-scope examples (route = refusal)
    and evaluation cases (their own expected_route, which may be absent). Single-agent specs carry no
    routes, so only exact repeats inside evaluation.cases are errors there.
    """
    seen = {}  # normalized query -> (label, route)
    for spec in specialists:
        for i, q in enumerate(spec.examples):
            _record(seen, q, f"specialist {spec.name!r} examples[{i}]", spec.name, router, same_list=False)
    for i, q in enumerate(out_of_scope):
        _record(seen, q, f"out_of_scope_examples[{i}]", "refusal", router, same_list=False)
    cases_seen = {}
    for i, case in enumerate(cases):
        key = _norm_query(case["query"])
        route = case.get("expected_route") if router else None
        label = f"evaluation.cases[{i}]"
        if key in cases_seen:
            first_label, first_route = cases_seen[key]
            kind = "duplicate" if first_route == route else "conflict"
            if kind == "duplicate":
                raise SpecError(f"{label}: duplicate of {first_label} (same query {case['query']!r})")
            raise SpecError(f"{label}: conflict with {first_label}: same query {case['query']!r} has expected "
                            f"routes {first_route!r} and {route!r}")
        cases_seen[key] = (label, route)
        if router and key in seen and route is not None and seen[key][1] != route:
            other_label, other_route = seen[key]
            raise SpecError(f"{label}: conflict with {other_label}: query {case['query']!r} is expected to route "
                            f"to {route!r} here but {other_route!r} there")


def _record(seen, query, label, route, router, same_list):
    key = _norm_query(query)
    if not router:
        return
    if key in seen:
        other_label, other_route = seen[key]
        raise SpecError(f"{label}: conflict with {other_label}: query {query!r} belongs to "
                        f"{other_route!r} and {route!r}")
    seen[key] = (label, route)


def parse_spec(data):
    if not isinstance(data, dict) or not data:
        raise SpecError("spec: must be a non-empty mapping")
    pattern = data.get("pattern")
    if not isinstance(pattern, str) or pattern not in PATTERNS:
        raise UnsupportedPattern(f"pattern: {pattern!r} is not supported; choose one of {', '.join(PATTERNS)}")
    kind = _choice(data, "agent_kind", AGENT_KINDS)
    framework = _choice(data, "framework", FRAMEWORKS)
    if kind == "prompt" and framework != "not-applicable":
        raise SpecError("framework: a prompt agent must use 'not-applicable'")
    if kind == "hosted" and framework == "not-applicable":
        raise SpecError("framework: a hosted agent must name its framework")
    session = _choice(data, "hosted_session_manager", SESSION_STATES, "not-applicable" if kind == "prompt" else "unknown")
    if kind == "prompt" and session != "not-applicable":
        raise SpecError("hosted_session_manager: a prompt agent must use 'not-applicable' (it has no hosted session)")
    if kind == "hosted" and session == "not-applicable":
        raise SpecError("hosted_session_manager: a hosted agent must declare enabled, disabled or unknown")
    tools = tuple(_tool(t) for t in _list(data, "tools"))
    names = [t.name.lower() for t in tools]
    if len(set(names)) != len(names):
        raise SpecError("tools: duplicate tool names")
    specialists = tuple(_specialist(s) for s in _list(data, "specialists"))
    ids = [s.name for s in specialists]
    if len(set(ids)) != len(ids):
        raise SpecError("specialists: duplicate specialist names after normalization")
    evals, pipeline = _flag(data, "evals_enabled"), _flag(data, "pipeline_enabled")
    if pipeline and not evals:
        raise SpecError("pipeline_enabled requires evals_enabled")
    evaluation, configured = _evaluation(data.get("evaluation"))
    resource = data.get("resource")
    if resource is None:
        resource = {}
    if not isinstance(resource, dict) or not all(_is_text(k) and _is_text(v) for k, v in resource.items()):
        raise SpecError("resource: must map non-blank strings to non-blank strings")
    runtime = data.get("runtime")
    if runtime is not None and not _is_text(runtime):
        raise SpecError("runtime: must be a non-blank string when given")
    if "out_of_scope_examples" in data and data["out_of_scope_examples"] is not None:
        out_of_scope = _strings(data["out_of_scope_examples"], "out_of_scope_examples")
    else:
        out_of_scope = ()
    routes = {x.name for x in specialists} | {"refusal"}
    for i, case in enumerate(evaluation.cases):
        if pattern == "router-specialists":
            if "expected_route" in case and case["expected_route"] not in routes:
                raise SpecError(f"evaluation.cases[{i}].expected_route: {case['expected_route']!r} is not one of "
                                f"{', '.join(sorted(routes))}")
        elif "expected_route" in case:
            raise SpecError(f"evaluation.cases[{i}].expected_route: only router-specialists cases carry a route")
    _check_query_consistency(specialists, out_of_scope, evaluation.cases, pattern == "router-specialists")
    return AgentSpec(
        name=_kebab(_text(data, "name"), "name"),
        description=_text(data, "description"),
        owner=_text(data, "owner"),
        classification=_choice(data, "classification", CLASSIFICATIONS),
        agent_kind=kind, framework=framework, pattern=pattern,
        pattern_rationale=_text(data, "pattern_rationale"),
        model=_text(data, "model"),
        hosted_session_manager=session,
        tools=tools, specialists=specialists,
        out_of_scope_examples=out_of_scope,
        evals_enabled=evals, pipeline_enabled=pipeline,
        evaluation=evaluation, evaluation_configured=configured,
        runtime=runtime if runtime is not None else "unspecified", resource=dict(resource),
    )


def load_spec(path):
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"{path}: cannot read spec ({exc.strerror or exc})") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path}: invalid JSON ({exc})") from exc
    try:
        return parse_spec(data)
    except SpecError as exc:
        raise type(exc)(f"{path}: {exc}") from exc


# --- compatibility checks ---------------------------------------------------------------------


def validate_new_hosted_spec(spec):
    """Gate for either new-hosted creator. Does not apply to existing overlays."""
    if spec.agent_kind != "hosted":
        raise SpecError("new hosted output requires agent_kind 'hosted'")
    if spec.framework != "maf":
        raise SpecError(f"framework {spec.framework!r}: new hosted agents must use maf")
    if spec.hosted_session_manager != "enabled":
        raise SpecError(
            f"hosted_session_manager is {spec.hosted_session_manager!r}: new hosted agents require the "
            "Foundry-managed session manager enabled (unknown/disabled blocks readiness)"
        )


def validate_for_mode(spec, mode):
    if mode not in MODES:
        raise SpecError(f"mode {mode!r} is not one of {', '.join(MODES)}")
    if mode == "design-only":
        return
    if PATTERNS[spec.pattern].status == "design-only":
        raise UnsupportedPattern(f"pattern {spec.pattern!r} is design-only; use --mode design-only")
    if mode == "overlay-existing":
        return
    # create-new: complete MAF-hosted tool-less router only
    if spec.pattern != "router-specialists":
        raise SpecError(f"create-new builds routers only; {spec.pattern!r} is created by its base flow")
    if spec.agent_kind == "prompt":
        raise SpecError("a runnable prompt router is not supported; use a hosted MAF router or design-only")
    if spec.tools:
        raise SpecError("router tools and action approvals are not supported in create-new; use design-only")
    validate_new_hosted_spec(spec)
    if not spec.specialists:
        raise SpecError("a runnable router needs at least one specialist")
    if spec.evals_enabled:
        if any(not s.examples for s in spec.specialists):
            raise SpecError("evals enabled: every specialist needs at least one example query of its own")
        if sum(len(s.examples) for s in spec.specialists) + len(spec.out_of_scope_examples) < MIN_ROUTER_QUERIES:
            raise SpecError("evals enabled: need at least two example queries overall")


def validate_release_config(spec):
    """Separate preflight before a real release gate. Template generation never needs this."""
    if not spec.evals_enabled:
        raise SpecError("release gate requires evals enabled")
    if not spec.evaluation_configured:
        raise SpecError("evaluation is unconfigured: judge model, evaluators, threshold and approved cases are required")
    cfg = spec.evaluation
    if not cfg.judge_model:
        raise SpecError("evaluation.judge_model is unconfigured")
    if cfg.min_pass_rate is None:
        raise SpecError("evaluation.min_pass_rate is unconfigured")
    if not cfg.evaluators:
        raise SpecError("evaluation.evaluators is unconfigured")
    if cfg.dataset_status != "approved":
        raise SpecError("evaluation dataset is not approved")
    if spec.pattern == "router-specialists" and any("expected_route" not in c for c in cfg.cases):
        raise SpecError("router release cases each need an expected_route")
    if len(cfg.cases) < MIN_RELEASE_QUERIES:
        raise SpecError(f"release gate needs at least {MIN_RELEASE_QUERIES} approved queries, got {len(cfg.cases)}")

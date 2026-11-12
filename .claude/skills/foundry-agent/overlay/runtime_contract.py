"""Honest runtime contract for the foundry-agent skill (plan Task 2).

Separates declared requirements, platform capabilities and application obligations. No generated
row is ever `passed`: that value is constructible only from scoped deployed evidence with
provenance, which this module never fabricates. Local tests and documentation are recorded as
local evidence and cannot promote a control.
"""

import json
import re
from dataclasses import dataclass, field

from agent_spec import MODES, PATTERNS, SpecError

CONTROL_IDS = (
    "identity-propagation", "approved-tool-access", "context-memory-isolation", "bounded-reasoning-retries",
    "handoff-controls", "safe-termination", "traceability", "audit-evidence",
)
IMPLEMENTATIONS = ("declared", "implemented", "not-implemented")
VERIFICATIONS = ("not-run", "passed", "failed", "inconclusive", "not-applicable")
RESPONSIBILITIES = ("declared-requirement", "platform", "application", "shared")
EVIDENCE_SCOPES = ("local", "deployed")
SESSION_OBSERVED = ("enabled", "disabled", "unknown", "not-observed")
# Basis says where a capability comes from. Nothing here is ever "observed": that needs deployed evidence.
CAPABILITY_BASES = ("provided-by-template", "declared-requirement")
SESSION_DECLARED = ("enabled", "disabled", "unknown", "not-applicable")

# Declared controls per column; everything else is not-implemented. Router isolation is not declared
# because a finite tool-less workflow is not an isolation mechanism.
_SINGLE_DECLARED = {"identity-propagation", "approved-tool-access", "context-memory-isolation", "traceability"}
_ROUTER_DECLARED = {"identity-propagation", "approved-tool-access", "traceability"}

_HANDOFF_REASON = "topology: a single-agent or router runs no control transfer between agents"
_PROVENANCE = (
    "microsoft/agent-framework 6b675d68 packages/foundry_hosting README 'Agent Sessions'; "
    "pinned agent-framework-foundry-hosting release; hosted/assets/preflight.py (source check only)"
)


class ContractError(ValueError):
    """A contract value is invalid or would overstate what has been verified."""


# --- evidence and per-control status ----------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    description: str
    scope: str
    environment: str = ""
    runtime_version: str = ""
    test: str = ""
    run: str = ""

    def __post_init__(self):
        if self.scope not in EVIDENCE_SCOPES:
            raise ContractError(f"evidence scope {self.scope!r} is not one of {', '.join(EVIDENCE_SCOPES)}")


_DEPLOYED_FIELDS = ("description", "environment", "runtime_version", "test", "run")


def _require_deployed(ev, what):
    if not isinstance(ev, Evidence) or ev.scope != "deployed":
        raise ContractError(f"{what} requires deployed evidence (scope 'deployed'); local evidence cannot promote")
    for name in _DEPLOYED_FIELDS:
        if not (getattr(ev, name) or "").strip():
            raise ContractError(f"{what}: deployed evidence {name} must be non-blank")


@dataclass(frozen=True)
class ControlStatus:
    control_id: str
    implementation: str
    verification: str = "not-run"
    responsibility: str = "declared-requirement"
    not_applicable_reason: str = ""
    deployed_evidence: Evidence | None = None
    local_evidence: tuple = ()
    mechanisms: tuple = ()
    gaps: tuple = ()
    notes: str = ""
    component_refs: tuple = ()  # pointers to what implements/exercises the control; never generated
    source_refs: tuple = ()
    test_refs: tuple = ()

    def __post_init__(self):
        for fld, allowed in (("control_id", CONTROL_IDS), ("implementation", IMPLEMENTATIONS),
                             ("verification", VERIFICATIONS), ("responsibility", RESPONSIBILITIES)):
            if getattr(self, fld) not in allowed:
                raise ContractError(f"{fld}: {getattr(self, fld)!r} is not one of {', '.join(allowed)}")
        for fld in ("component_refs", "source_refs", "test_refs"):
            refs = getattr(self, fld)
            if not isinstance(refs, tuple) or not all(isinstance(r, str) and r.strip() for r in refs):
                raise ContractError(f"{fld}: must be a tuple of non-blank strings")
        for ev in self.local_evidence:
            if not isinstance(ev, Evidence) or ev.scope != "local":
                raise ContractError("local_evidence entries must have scope 'local'")
        reason = (self.not_applicable_reason or "").strip()
        if self.verification == "not-applicable" and not reason:
            raise ContractError("not-applicable verification needs a reason")
        if reason and self.verification != "not-applicable":
            raise ContractError("a not-applicable reason is only valid with verification 'not-applicable'")
        if self.verification == "passed":
            _require_deployed(self.deployed_evidence, f"{self.control_id} passed")
        elif self.deployed_evidence is not None:
            _require_deployed(self.deployed_evidence, f"{self.control_id} deployed_evidence")


# --- records ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    name: str
    responsibility: str
    summary: str
    limits: str = ""
    basis: str = "declared-requirement"

    def __post_init__(self):
        if self.basis not in CAPABILITY_BASES:
            raise ContractError(
                f"basis: {self.basis!r} is not one of {', '.join(CAPABILITY_BASES)}; "
                "a capability is never observed without deployed evidence")


@dataclass(frozen=True)
class ToolRequirement:
    name: str
    auth: str
    requires_approval: bool
    enforced: bool = False  # declarations are never enforcement
    note: str = ""


@dataclass(frozen=True)
class SessionRecord:
    required: str  # enabled | reported-only | not-applicable
    declared: str
    observed: str
    observed_evidence: Evidence | None
    owner: str
    provenance: str
    separate_concerns: tuple = ()

    def __post_init__(self):
        if self.observed not in SESSION_OBSERVED:
            raise ContractError(f"observed: {self.observed!r} is not one of {', '.join(SESSION_OBSERVED)}")
        if self.observed == "enabled":
            _require_deployed(self.observed_evidence, "observed session enablement (evidence)")

    @property
    def blocks_readiness(self):
        if self.required == "not-applicable":
            return False
        return self.observed != "enabled"


@dataclass(frozen=True)
class Contract:
    name: str
    agent_kind: str
    framework: str
    framework_source: str
    pattern: str
    pattern_status: str
    mode: str
    rationale: str
    controls: tuple
    capabilities: tuple
    tools: tuple
    session: SessionRecord
    evaluation_status: str
    pipeline_status: str
    gaps: tuple

    @property
    def claims_implementation(self):
        """True only when a control is marked implemented; declarations alone claim nothing."""
        return any(c.implementation == "implemented" for c in self.controls)


# --- build ------------------------------------------------------------------------------------


def _column(spec, mode):
    if mode == "design-only" or PATTERNS[spec.pattern].status == "design-only":
        return set()
    return _ROUTER_DECLARED if spec.pattern == "router-specialists" else _SINGLE_DECLARED


def _controls(spec, mode, declared):
    design = mode == "design-only"
    out = []
    for cid in CONTROL_IDS:
        implementation = "declared" if cid in declared else "not-implemented"
        verification, reason = "not-run", ""
        responsibility = "declared-requirement" if implementation == "declared" else "application"
        gaps = ()
        if cid == "handoff-controls" and not design and spec.pattern in ("single-agent", "router-specialists"):
            verification, reason = "not-applicable", _HANDOFF_REASON
        if cid == "approved-tool-access" and spec.tools:
            gaps = ("tool approval declarations have no enforcement mechanism in this skill",)
        if cid == "context-memory-isolation" and implementation == "not-implemented":
            gaps = ("platform session defaults do not prove user or tenant isolation",)
        out.append(ControlStatus(cid, implementation, verification, responsibility, reason, gaps=gaps))
    return tuple(out)


def _capabilities(spec, mode):
    """Mechanisms the output provides (create-new) or requires (overlay). Never observed facts."""
    if mode == "design-only":
        return ()
    basis = "provided-by-template" if mode == "create-new" else "declared-requirement"
    caps = []
    maf = spec.agent_kind == "hosted" and spec.framework == "maf"
    if maf:
        caps.append(Capability(
            "session-history", "platform",
            "Foundry-managed session store keeps sessions and history; no custom session code is delivered.",
            "retention, deletion, tenant authorization and business-case state are separate decisions", basis))
        caps.append(Capability(
            "invocation-protocol", "platform",
            "The standard SDK hosting adapter handles invocation, streaming and continuation.",
            "no custom protocol code in this skill", basis))
    if spec.pattern == "router-specialists" and (maf or mode == "create-new"):
        caps.append(Capability(
            "context-propagation", "platform",
            "Host-supplied conversation context reaches the application; specialist instructions stay scoped.",
            "local context tests are not hosted isolation proof", basis))
        caps.append(Capability(
            "bounded-routing", "application",
            "Finite classify then at most one tool-less specialist run.",
            "underlying SDK/HTTP retries, duration, cost, token limits and cancellation are not bounded", basis))
        caps.append(Capability(
            "refusal", "application",
            "Blank or unroutable requests have an explicit refusal outcome.",
            "no timeout, exception or cancellation recovery; not safe business-action termination", basis))
    if spec.evals_enabled or spec.pipeline_enabled:
        caps.append(Capability(
            "release-evidence", "shared",
            "Generated evaluation and pipeline artifacts are declarations until executed.",
            "evaluating a candidate version is not evidence that every runtime or security control passed",
            basis))
    return tuple(caps)


def _tools(spec):
    out = []
    for t in spec.tools:
        note = ""
        if t.auth == "user-token":
            note = ("user-token propagation depends on the real tool catalog and auth being compatible; "
                    "it is not assumed for arbitrary MCP servers")
        out.append(ToolRequirement(t.name, t.auth, t.requires_approval, False, note))
    return tuple(out)


def _session(spec, mode):
    """Applicability comes from agent kind and mode, never from the declared value, so invalid
    metadata cannot turn a hosted requirement into a non-blocking one. Observed state starts
    'not-observed'; only deployed evidence can change it."""
    if spec.agent_kind not in ("prompt", "hosted"):
        raise ContractError(f"agent_kind: {spec.agent_kind!r} is not 'prompt' or 'hosted'")
    if spec.hosted_session_manager not in SESSION_DECLARED:
        raise ContractError(
            f"hosted_session_manager: {spec.hosted_session_manager!r} is not one of {', '.join(SESSION_DECLARED)}")
    if spec.agent_kind == "prompt":
        return SessionRecord("not-applicable", spec.hosted_session_manager, "not-observed", None,
                             "not-applicable", "", ())
    if mode == "overlay-existing":
        required, owner = "reported-only", "platform (not inspected; declared only)"
    else:
        required, owner = "enabled", "platform (Foundry-managed session store via the SDK hosting integration)"
    concerns = ("semantic long-term memory", "business-case state", "tenant authorization",
                "retention and deletion policy")
    return SessionRecord(required, spec.hosted_session_manager, "not-observed", None, owner, _PROVENANCE, concerns)


def _eval_status(spec):
    """disabled | unconfigured (no settings) | incomplete (some missing) | configured-proposed/approved.
    Dataset approval never implies the evaluation is configured."""
    if not spec.evals_enabled:
        return "disabled"
    cfg = spec.evaluation
    present = (bool(cfg.judge_model), cfg.min_pass_rate is not None, bool(cfg.evaluators), bool(cfg.cases))
    if not spec.evaluation_configured or (not any(present) and cfg.dataset_status == "proposed"):
        return "unconfigured"
    if not all(present):
        return "incomplete"
    return "configured-approved" if cfg.dataset_status == "approved" else "configured-proposed"


def _gaps(spec, mode):
    gaps = []
    if mode == "overlay-existing" and spec.framework != "maf" and spec.agent_kind == "hosted":
        gaps.append(f"existing {spec.framework} runtime not inspected: no routing, context, tool or "
                    "termination behavior is asserted")
    if mode != "design-only" and spec.pattern == "router-specialists" and spec.evals_enabled:
        missing = [x.name for x in spec.specialists if not x.examples]
        if missing:
            gaps.append(f"specialists without example queries: {', '.join(missing)}")
        if not spec.out_of_scope_examples:
            gaps.append("no out-of-scope queries supplied; refusal coverage is missing and none were invented")
    return tuple(gaps)


def build_contract(spec, mode):
    if mode not in MODES:
        raise SpecError(f"mode {mode!r} is not one of {', '.join(MODES)}")
    design = mode == "design-only"
    declared = _column(spec, mode)
    if mode == "overlay-existing":
        source = "declared by the spec; existing code was not inspected"
    else:
        source = "declared by the spec"
    if spec.pipeline_enabled:
        pipeline = "selected-not-executed"
    else:
        pipeline = "disabled"
    return Contract(
        name=spec.name, agent_kind=spec.agent_kind, framework=spec.framework, framework_source=source,
        pattern=spec.pattern, pattern_status=PATTERNS[spec.pattern].status, mode=mode,
        rationale=spec.pattern_rationale, controls=_controls(spec, mode, declared),
        capabilities=_capabilities(spec, mode), tools=_tools(spec), session=_session(spec, mode),
        evaluation_status=_eval_status(spec), pipeline_status=pipeline, gaps=_gaps(spec, mode),
    )


# --- render -----------------------------------------------------------------------------------


def _dc(obj):
    if isinstance(obj, (list, tuple)):
        return [_dc(x) for x in obj]
    if hasattr(obj, "__dataclass_fields__"):
        data = {f: _dc(getattr(obj, f)) for f in obj.__dataclass_fields__}
        if isinstance(obj, SessionRecord):
            data["blocks_readiness"] = obj.blocks_readiness
        return data
    return obj


def render_json(contract):
    data = _dc(contract)
    data["rationale"] = contract.rationale
    data["claims_implementation"] = contract.claims_implementation
    return json.dumps(data, indent=2, ensure_ascii=False)


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|~])")


def _esc(text):
    """Escape user text for one Markdown table cell or line: no breaks, markup or HTML."""
    flat = " ".join(str(text).split())
    return _MD_SPECIAL.sub(r"\\\1", flat)


def _evidence_line(ev):
    parts = [f"{ev.scope}: {ev.description}"]
    for label, value in (("environment", ev.environment), ("version", ev.runtime_version),
                         ("test", ev.test), ("run", ev.run)):
        if value:
            parts.append(f"{label} {value}")
    return _esc("; ".join(parts))


def _table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_esc(c) if not c.startswith("\0") else c[1:] for c in row) + " |" for row in rows]
    return out


def render_markdown(contract):
    """Human-readable form of the same data render_json emits: nothing held in JSON is dropped here."""
    c = contract
    lines = [f"# Runtime contract: {_esc(c.name)}", ""]
    if c.mode == "design-only":
        lines += ["> Design only. Nothing here is implemented or declared as running code.", ""]
    lines += [
        f"* Kind: {_esc(c.agent_kind)}", f"* Framework: {_esc(c.framework)} ({_esc(c.framework_source)})",
        f"* Pattern: {_esc(c.pattern)} ({_esc(c.pattern_status)})", f"* Mode: {_esc(c.mode)}",
        f"* Rationale: {_esc(c.rationale)}", f"* Evaluation: {_esc(c.evaluation_status)}",
        f"* Pipeline: {_esc(c.pipeline_status)}",
        f"* Claims implementation: {'yes' if c.claims_implementation else 'no'}", "",
        "## Evidence scope", "",
        "Generated rows are declarations. Local tests and platform documentation do not establish deployed "
        "verification; only scoped deployed evidence with environment, version, test and run can.", "",
        "## Controls", "",
    ]
    lines += _table(["Control", "Implementation", "Verification", "Responsibility", "Gaps"], [
        (x.control_id, x.implementation,
         x.verification if not x.not_applicable_reason else f"{x.verification}: {x.not_applicable_reason}",
         x.responsibility, "; ".join(x.gaps)) for x in c.controls])
    detail = [x for x in c.controls if x.deployed_evidence or x.local_evidence or x.mechanisms or x.notes
              or x.component_refs or x.source_refs or x.test_refs]
    if detail:
        lines += ["", "## Control evidence and references", ""]
        for x in detail:
            lines.append(f"### {_esc(x.control_id)}")
            lines.append("")
            if x.deployed_evidence:
                lines.append(f"* Deployed evidence: {_evidence_line(x.deployed_evidence)}")
            for ev in x.local_evidence:
                lines.append(f"* Local evidence (cannot promote a control): {_evidence_line(ev)}")
            for label, values in (("Mechanisms", x.mechanisms), ("Components", x.component_refs),
                                  ("Sources", x.source_refs), ("Tests", x.test_refs)):
                if values:
                    lines.append(f"* {label}: {_esc('; '.join(values))}")
            if x.notes:
                lines.append(f"* Notes: {_esc(x.notes)}")
            lines.append("")
    s = c.session
    lines += ["", "## Session manager", "",
              f"* Required: {_esc(s.required)}", f"* Declared: {_esc(s.declared)}", f"* Observed: {_esc(s.observed)}",
              f"* Owner: {_esc(s.owner)}", f"* Blocks readiness: {'yes' if s.blocks_readiness else 'no'}"]
    if s.observed_evidence:
        lines.append(f"* Observed evidence: {_evidence_line(s.observed_evidence)}")
    if s.provenance:
        lines.append(f"* Provenance: {_esc(s.provenance)}")
    if s.separate_concerns:
        lines.append(f"* Separate concerns: {_esc(', '.join(s.separate_concerns))}")
    if c.capabilities:
        lines += ["", "## Capabilities and mechanisms", ""]
        lines += _table(["Name", "Responsibility", "Basis", "Summary", "Limits"],
                        [(k.name, k.responsibility, k.basis, k.summary, k.limits) for k in c.capabilities])
    if c.tools:
        lines += ["", "## Tool requirements (declared, not enforced)", ""]
        lines += _table(["Tool", "Auth", "Approval", "Enforced", "Note"],
                        [(t.name, t.auth, "required" if t.requires_approval else "no",
                          "yes" if t.enforced else "no", t.note) for t in c.tools])
    if c.gaps:
        lines += ["", "## Coverage gaps", ""] + [f"* {_esc(g)}" for g in c.gaps]
    return "\n".join(lines) + "\n"

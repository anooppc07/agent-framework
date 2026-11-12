"""Router-specific half of the evaluation plan (plan Task 3b): one set of approved cases with routes, and the
smoke selection derived from it.

Cases come from three places, in this fixed order: each specialist's examples (route = that specialist), the
out-of-scope examples (route = the reserved refusal route) and evaluation.cases (their own expected_route).
A query appears once: a restatement with the same route is merged, a different route is a conflict that is
rejected before anything is written. Queries are kept verbatim; none is ever invented, so an absent
out-of-scope example is reported as missing coverage, not filled in.

Smoke: the first example of each specialist (in order), then one reserved out-of-scope slot if any exists,
capped at five; if fewer than two rows result, it is filled from the unselected baseline rows in baseline order.
Reported, never hidden: specialists that did not fit under the cap, and missing out-of-scope coverage.
"""

from evaluation_assets import (
    REFUSAL_ROUTE,
    SKILL_ROOT,
    SMOKE_CAP,
    SMOKE_MIN,
    EvalAssetError,
    EvalCase,
    _case_id,
    _norm,
    _with_config,
)

MIN_QUERIES = 2


def _gather(spec):
    """(query, route, source label) in fixed source order, merged by normalised query."""
    merged, order = {}, []

    def add(query, route, label, context=None):
        key = _norm(query)
        if key in merged:
            first = merged[key]
            if route is not None and first["route"] not in (None, route):
                raise EvalAssetError(f"{label}: conflict with {first['label']}: query {query!r} is expected to "
                                     f"route to {route!r} here but {first['route']!r} there")
            if first["route"] is None and route is not None:
                first["route"] = route
            if first["context"] is None:
                first["context"] = context
            return
        merged[key] = {"query": query, "route": route, "label": label, "context": context}
        order.append(key)

    for spec_item in spec.specialists:
        for i, query in enumerate(spec_item.examples):
            add(query, spec_item.name, f"specialist {spec_item.name!r} examples[{i}]")
    for i, query in enumerate(spec.out_of_scope_examples):
        add(query, REFUSAL_ROUTE, f"out_of_scope_examples[{i}]")
    for i, case in enumerate(spec.evaluation.cases):
        add(case["query"], case.get("expected_route"), f"evaluation.cases[{i}]", case.get("context"))
    return [merged[k] for k in order]


def _smoke(spec, cases):
    """Select smoke case ids and report what the selection leaves uncovered."""
    selected, first_seen = [], set()
    for case in cases:
        if case.expected_route not in first_seen and case.expected_route != REFUSAL_ROUTE:
            first_seen.add(case.expected_route)
            selected.append(case)
    specialists = [s.name for s in spec.specialists]
    refusal = next((c for c in cases if c.expected_route == REFUSAL_ROUTE), None)
    room = SMOKE_CAP - (1 if refusal else 0)
    kept = selected[:room]
    omitted = tuple(name for name in specialists if name not in {c.expected_route for c in kept})
    if refusal:
        kept = kept + [refusal]
    if len(kept) < SMOKE_MIN:
        have = {c.case_id for c in kept}
        kept += [c for c in cases if c.case_id not in have][:SMOKE_MIN - len(kept)]
    return tuple(c.case_id for c in kept), omitted, refusal is None


def build_router_plan(spec, skill_root=SKILL_ROOT):
    """The router EvalPlan: every approved case once, with ids, routes and the derived smoke selection."""
    if not spec.specialists:
        raise EvalAssetError("a router needs at least one specialist to evaluate")
    gathered = _gather(spec)
    if len(gathered) < MIN_QUERIES:
        raise EvalAssetError(f"a router needs at least two approved queries for evaluation, got {len(gathered)}")
    missing = [g["query"] for g in gathered if g["route"] is None]
    if missing:
        raise EvalAssetError(f"every router case needs an expected_route; none given for {missing[0]!r}")
    cases = tuple(EvalCase(_case_id(i), g["query"], g["route"], g["context"]) for i, g in enumerate(gathered, 1))
    smoke_ids, omitted, no_oos = _smoke(spec, cases)
    warnings = []
    if omitted:
        warnings.append(f"specialist(s) {', '.join(omitted)} are not in the smoke set (cap of {SMOKE_CAP}); "
                        "the baseline still covers them")
    if no_oos:
        warnings.append("no out-of-scope query was supplied, so refusal routing is not covered; none was invented "
                        "(add approved out-of-scope examples)")
    return _with_config(
        spec, cases, smoke_ids, routes=(*(s.name for s in spec.specialists), REFUSAL_ROUTE),
        omitted_specialists=omitted, missing_out_of_scope=no_oos, warnings=tuple(warnings))

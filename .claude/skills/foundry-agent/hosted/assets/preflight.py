"""Fail-closed check that the Foundry-managed session store is not bypassed.

Foundry session management has no enable flag: `ResponsesHostServer` uses the Foundry
`FoundryAgentSessionStore` unless `agent_session_store_provider` is overridden (microsoft/
agent-framework packages/foundry_hosting README "Agent Sessions", pinned release in
requirements.txt).

This reads source only and accepts exactly two entrypoint shapes; anything else is 'unknown'
(blocks readiness) rather than guessed at. Single agent:

    from agent_framework_foundry_hosting import ResponsesHostServer   # the only import of it, unaliased
    def build_server(agent):
        return ResponsesHostServer(agent=agent, history_source="agent_server")   # the only construction
    if __name__ == "__main__":
        ... build_server(...) ...                                     # the entrypoint reaches it

Native workflow (router): the same, but `build_server()` constructs
`ResponsesHostServer(workflow=<factory>, parse_response=<parser>)`. `agent=` and `workflow=` are never
mixed, and wrapping a workflow with `.as_agent()` (deprecated by the hosting package) is not accepted.

It cannot prove a deployed state: effective hosted enablement stays unverified until a Phase 2
hosted continuity check.

Usage: python preflight.py [main.py]   exit 0 = enabled, 2 = disabled/unknown
"""

import ast
import sys
from pathlib import Path

MODULE = "agent_framework_foundry_hosting"
HOST = "ResponsesHostServer"
FACTORY = "build_server"
OVERRIDE = "agent_session_store_provider"
# Any of these replaces Foundry-managed state with something else, so each is a bypass.
OVERRIDES = (OVERRIDE, "checkpoint_store_provider", "function_approval_store_provider")
AGENT_KEYWORDS = {"agent", "history_source"}
WORKFLOW_KEYWORDS = {"workflow", "parse_response"}


def _unknown(reason):
    return "unknown", reason


def _binds_name(tree, name):
    """True if any statement other than the host import binds `name`."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return True
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == name and not (
                    isinstance(node, ast.ImportFrom) and node.module == MODULE and alias.name == HOST
                    and alias.asname is None
                ):
                    return True
    return False


def _reaches_factory(tree):
    for node in tree.body:
        if isinstance(node, ast.If) and ast.unparse(node.test).replace("'", '"') in (
            '__name__ == "__main__"', '"__main__" == __name__'
        ):
            return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == FACTORY
                       for stmt in node.body for n in ast.walk(stmt))
    return False


def check_session_manager(source):
    """Return (status, reason); status is 'enabled', 'disabled' or 'unknown'."""
    if not isinstance(source, str) or not source.strip():
        return _unknown("no source to inspect")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return _unknown(f"source does not parse: {exc.msg}")

    # A bypass is reported as 'disabled' wherever the host is built, before any shape check.
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Name) and n.func.id == HOST)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == HOST)
        ):
            for kw in n.keywords:
                if kw.arg in OVERRIDES and not (isinstance(kw.value, ast.Constant) and kw.value.value is None):
                    return "disabled", f"{kw.arg} overrides the Foundry-managed store"

    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == MODULE]
    imported = [(a.name, a.asname) for n in imports for a in n.names]
    # One statement from the hosting package; the host unaliased exactly once. Other names (for example
    # WorkflowTurn) may ride along, but a star import or any alias could hide a different object.
    if len(imports) != 1 or imported.count((HOST, None)) != 1 or any(
        a.name == "*" or (a.asname is not None) for a in imports[0].names
    ):
        return _unknown(f"expected exactly one unaliased 'from {MODULE} import {HOST}'")
    if any(isinstance(n, ast.Import) and any(a.name.split(".")[0] == MODULE for a in n.names)
           for n in ast.walk(tree)):
        return _unknown(f"{MODULE} must not also be imported as a module")
    if _binds_name(tree, HOST):
        return _unknown(f"{HOST} is rebound or shadowed")

    factories = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == FACTORY]
    if len(factories) != 1:
        return _unknown(f"expected one module-level {FACTORY}() factory")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ((isinstance(n.func, ast.Name) and n.func.id == HOST)
                  or (isinstance(n.func, ast.Attribute) and n.func.attr == HOST))]
    if len(calls) != 1:
        return _unknown(f"expected exactly one {HOST} construction, found {len(calls)}")
    call = calls[0]
    if not isinstance(call.func, ast.Name) or not any(call is n for n in ast.walk(factories[0])):
        return _unknown(f"{HOST} must be constructed directly inside {FACTORY}()")
    if call.args:
        return _unknown(f"{HOST} positional arguments hide whether a store is overridden")
    if any(kw.arg is None for kw in call.keywords):
        return _unknown(f"{HOST}(**kwargs) hides whether {OVERRIDE} is set")
    names = {kw.arg for kw in call.keywords} - set(OVERRIDES)
    if "workflow" in names:
        if names != WORKFLOW_KEYWORDS:
            return _unknown(f"{HOST}(workflow=...) must use exactly workflow= and parse_response=, "
                            f"got {', '.join(sorted(names))}")
    elif "agent" in names:
        if not names <= AGENT_KEYWORDS:
            return _unknown(f"unexpected {HOST} arguments: {', '.join(sorted(names - AGENT_KEYWORDS))}")
    else:
        return _unknown(f"{HOST} must receive agent= or workflow= as a keyword")
    if not _reaches_factory(tree):
        return _unknown(f"the __main__ entrypoint does not call {FACTORY}()")
    return "enabled", f"{HOST} uses the default Foundry-managed session store"


def main(argv):
    path = Path(argv[1] if len(argv) > 1 else Path(__file__).with_name("main.py"))
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"unknown: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    status, reason = check_session_manager(source)
    print(f"{status}: {reason}")
    return 0 if status == "enabled" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

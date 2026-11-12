"""Fail when a template could bypass the AI Gateway.  python check_templates.py [templates-dir]

Exit 0 = clean, 1 = findings, 2 = nothing to check or a file could not be read.
Rules map to templates/_gateway/GATEWAY-CONTRACT.md (R1, R2, R4, R5, R7, R9, R10).
This is a lint for regressions; the real enforcement is the platform (private endpoints, firewall).
"""

import ast
import re
import sys
from pathlib import Path

import yaml

ALLOWED_DIRECT_MODEL = {"foundry-memory"}   # documented exception: in-account memory-store models
PLACEHOLDER = re.compile(r"<subscription-id>|<resource-group>|<account>|<policy-name>")
GATEWAY_URL = re.compile(r"^\$\{GATEWAY_[A-Z_]+\}(/|$)")
BAD_CALLS = {"FoundryChatClient": "calls the model on the project endpoint, not the gateway (R1)",
             "get_openai_client": "calls the model on the project endpoint, not the gateway (R1)",
             "AzureOpenAI": "calls a model endpoint directly (R1)",
             "AzureChatOpenAI": "calls a model endpoint directly (R1)"}
BAD_TOOL_TYPES = {"web_search", "code_interpreter", "bing_grounding"}   # built-in tools egress outside the gateway


def findings_for_python(path: Path, template: str) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as e:
        return [f"{path}: cannot parse ({e})"]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in BAD_CALLS and template not in ALLOWED_DIRECT_MODEL:
                out.append(f"{path}:{node.lineno}: {name} {BAD_CALLS[name]}")
            if name == "MCPStreamableHTTPTool" and path.name != "gateway_client.py":
                out.append(f"{path}:{node.lineno}: build MCP tools with gateway_client.mcp_tool (R4)")
    return out


def walk_yaml(node, path, out, template):
    if isinstance(node, dict):
        if node.get("type") in BAD_TOOL_TYPES:
            out.append(f"{path}: built-in tool '{node['type']}' leaves the gateway (R4)")
        if node.get("type") == "mcp":
            # A toolbox-backed tool has no server_url: the toolbox endpoint is resolved by Foundry.
            if "server_url" in node and not GATEWAY_URL.match(str(node["server_url"])):
                out.append(f"{path}: mcp server_url '{node['server_url']}' must start with a "
                           "${GATEWAY_...} placeholder (R4)")
            if node.get("require_approval") == "never":
                out.append(f"{path}: bare require_approval 'never'; use {{never: {{tool_names: [...]}}}} "
                           "for read-only tools only (R6)")
        if "kind" in node and node.get("kind") == "prompt":
            model = str(node.get("model", ""))
            if not model.startswith("ai-gateway/"):
                out.append(f"{path}: prompt agent model '{model}' must be ai-gateway/<model> (R2)")
        if node.get("category") == "RemoteTool" and not GATEWAY_URL.match(str(node.get("target", ""))):
            out.append(f"{path}: RemoteTool connection target must be a ${{GATEWAY_...}} placeholder (R4)")
        if node.get("authType") in {"CustomKeys", "ApiKey"} or "credentials" in node:
            out.append(f"{path}: stored tool credential; the gateway holds them (R5)")
        if str(node.get("ENABLE_SENSITIVE_DATA", "")).lower() == "true":
            out.append(f"{path}: ENABLE_SENSITIVE_DATA must not be true (R7)")
        for value in node.values():
            walk_yaml(value, path, out, template)
    elif isinstance(node, list):
        for item in node:
            walk_yaml(item, path, out, template)
    elif isinstance(node, str) and PLACEHOLDER.search(node):
        out.append(f"{path}: unreplaced placeholder '{PLACEHOLDER.search(node).group()}'")


def check(root: Path) -> tuple[int, list[str]]:
    manifests = sorted(root.glob("*/*/azure.yaml"))
    if not manifests:
        return 2, [f"{root}: no templates found (expected <kind>/<name>/azure.yaml)"]
    out = []
    for manifest in manifests:
        template = manifest.parent.name
        try:
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            out.append(f"{manifest}: unreadable ({e})")
            continue
        if not doc:
            out.append(f"{manifest}: empty")
            continue
        walk_yaml(doc, manifest, out, template)
        for py in sorted(manifest.parent.rglob("*.py")):
            out += findings_for_python(py, template)
        for bicep in manifest.parent.rglob("*.bicep"):
            if re.search(r"publicNetworkAccess:\s*'Enabled'", bicep.read_text(encoding="utf-8")):
                out.append(f"{bicep}: publicNetworkAccess must be 'Disabled' (R9)")
    return (1 if out else 0), out


if __name__ == "__main__":
    code, lines = check(Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent))
    print("\n".join(lines) if lines else "OK: no gateway bypass found")
    sys.exit(code)

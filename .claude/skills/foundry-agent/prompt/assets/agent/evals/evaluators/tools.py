r"""Tool usage evaluators: tool_call_accuracy, tool_selection, tool_input_accuracy.

Drop this module (and the ``tool_definitions`` dataset column) when grounding=none.

These confirm the agent actually calls its grounding tool (rather than answering from the model's
own knowledge) and supplies the right input.

Key constraints, confirmed live against the Foundry Evals API:

* ``tool_definitions`` must be supplied via a ``{{item.*}}`` / ``{{sample.*}}`` data mapping — the
  API rejects a literal JSON string in ``data_mapping`` (it must match
  ``^\{\{(?:item|sample)\.[a-zA-Z0-9_.]+\}\}$``). So every dataset row carries a
  ``tool_definitions`` column even though the value is identical across rows.
* The schema is a flat ``name``/``description``/``parameters`` object per tool, NOT the nested
  OpenAI ``{"type": "function", "function": {...}}`` wrapper — the nested form is rejected with
  ``tool_definitions[0].name: ... missing a 'name' field``.
* ``name`` must be the exact tool name the agent calls at runtime. For a Fabric data agent that is
  ``DataAgent_<FabricConnectionName>``; for an MCP tool it is the MCP tool's runtime name; for a
  function tool it is the function name. A mismatch makes ``tool_call_accuracy`` return "Not
  applicable" and scores the wrong tool entirely. Update ``{{TOOL_RUNTIME_NAME}}`` below to match
  your deployment.
* These three evaluators require ``deployment_name`` (not ``model``) as the initialization
  parameter — the API rejects them with ``MissingRequiredParameter`` otherwise.
"""

from azure.ai.projects.models import TestingCriterionAzureAIEvaluator

# The agent's tool definition, as the evaluators reason over it. Keep ``name`` equal to the tool's
# runtime name. This same object is embedded in every dataset row's ``tool_definitions`` column.
TOOL_DEFINITIONS = [
    {
        "name": "{{TOOL_RUNTIME_NAME}}",
        "description": "{{TOOL_DESCRIPTION}}",
        "parameters": {
            "type": "object",
            "properties": {
                "{{TOOL_PARAM_NAME}}": {
                    "type": "string",
                    "description": "{{TOOL_PARAM_DESCRIPTION}}",
                },
            },
            "required": ["{{TOOL_PARAM_NAME}}"],
        },
    }
]

_TOOL_METRICS = ("tool_call_accuracy", "tool_selection", "tool_input_accuracy")


def get_tool_evaluators(model_deployment: str) -> list[TestingCriterionAzureAIEvaluator]:
    """Return tool_call_accuracy, tool_selection, and tool_input_accuracy evaluators."""
    return [
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name=name,
            evaluator_name=f"builtin.{name}",
            initialization_parameters={"deployment_name": model_deployment},
            data_mapping={
                "query": "{{item.query}}",
                "response": "{{sample.output_items}}",
                "tool_definitions": "{{item.tool_definitions}}",
            },
        )
        for name in _TOOL_METRICS
    ]

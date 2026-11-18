"""Task adherence evaluators: task_adherence, intent_resolution.

``task_adherence`` checks the response against the agent's declared instructions (scope, grounding,
action boundary) using the full structured output (``output_items``), which includes tool calls the
plain-text evaluators can't see. ``intent_resolution`` checks whether the response actually
addresses what was asked.

Known limitation (confirmed live, ``azure_ai_target_completions`` data source): when
``{{sample.output_items}}`` is passed as ``response``, the Foundry service can flatten structured
tool_call/tool_result messages into a ``[TOOL_CALL] ... [TOOL_RESULT] ...`` text block before the
judge model sees them — so ``task_adherence`` can score a real, correctly-tooled response as a
failure ("no corroborating tool interaction"). Treat a low ``task_adherence`` score as a signal to
inspect the item's ``sample.output_items`` directly (does it actually contain a ``tool_call``
message?) before concluding the agent skipped its tool. ``task_adherence`` is excluded from the
pass/fail gate in ``run_evaluation.py`` for this reason; re-enable it once the serialization issue
is resolved for your project.
"""

from azure.ai.projects.models import TestingCriterionAzureAIEvaluator


def get_task_evaluators(
    model_deployment: str, for_traces: bool = False
) -> list[TestingCriterionAzureAIEvaluator]:
    """Return task_adherence and intent_resolution evaluators.

    See ``quality.get_quality_evaluators`` for what ``for_traces`` does.
    """
    criteria = [
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="task_adherence",
            evaluator_name="builtin.task_adherence",
            initialization_parameters={"model": model_deployment},
            data_mapping={"query": "{{item.query}}", "response": "{{sample.output_items}}"},
        ),
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="intent_resolution",
            evaluator_name="builtin.intent_resolution",
            initialization_parameters={"model": model_deployment},
            data_mapping={"query": "{{item.query}}", "response": "{{sample.output_text}}"},
        ),
    ]

    if for_traces:
        for criterion in criteria:
            criterion.pop("data_mapping", None)

    return criteria

"""Evaluator configuration modules for the azure-service-advisor Foundry evaluation suite.

Each module returns a list of ``TestingCriterionAzureAIEvaluator`` built around Foundry's
``builtin.*`` evaluators. They are grouped by concern:

    quality.py       coherence, fluency, relevance
    groundedness.py  groundedness, similarity          (drop for grounding=none)
    safety.py        violence, sexual, self_harm, hate_unfairness
    task.py          task_adherence, intent_resolution
    tools.py         tool_call_accuracy, tool_selection, tool_input_accuracy  (drop for grounding=none)
"""

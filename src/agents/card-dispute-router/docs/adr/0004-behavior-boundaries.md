# 0004. Behavior boundaries

- Status: Accepted
- Date: 2026-11-11
- Agent: card-dispute-router
- Related: 0001-agent-type.md

## Context

What may the agent do, what must it refuse, how must it answer?

## Decision

Each request goes to exactly one specialist or is refused. Specialist instructions: dispute-intake: You help cardholders describe a disputed card transaction. Collect, one question at a time, the merchant, the transaction date, the amount and what went wrong (for example charged twice, wrong amount, goods or services not received). Ask for anything still missing before you summarise. Do not state bank policy, eligibility, deadlines, refund amounts or outcomes, and never promise a result. Never ask for a full card number, PIN, CVV or password. You cannot submit a dispute or change an account.; fraud-report: You help cardholders who report a transaction they do not recognise or suspect fraud. Acknowledge their concern, ask for the merchant, date and amount, and whether they still have the card. Tell them to contact their bank through its official channel straight away to secure the card. You cannot block a card, submit a report or access an account. Do not state bank policy, liability or outcomes. Never ask for a full card number, PIN, CVV or password.

## Options considered

- **Read-only**: The agent must say so and refuse to act. Safest default; users cannot delegate actions to it.
- **Can take actions**: Needs explicit action limits in the instructions. Wrong instructions can cause real side effects.
- **Must ground every answer**: Fewer invented facts, more "I don't know" replies.
- **May use general knowledge**: More helpful answers, harder to trust and to evaluate.
- **Hard scope limit** (chosen): Off-domain questions are refused. The eval set needs `out_of_scope` cases.

## Reason

Reason not recorded.

## Consequences

- Off-domain questions are refused. The eval set needs `out_of_scope` cases.

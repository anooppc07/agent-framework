"""Deny-by-default access policy for shared facts (loaded from config/policy.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.settings import SETTINGS

DEFAULT_CLASSIFICATION = "confidential"


@dataclass(frozen=True)
class AgentPolicy:
    name: str
    read: frozenset[str]
    write: frozenset[str]
    max_level: int


class Policy:
    def __init__(self, raw: dict):
        self._order: list[str] = raw["classifications"]["order"]
        self._facts: dict[str, str] = raw.get("facts", {})
        self._agents: dict[str, AgentPolicy] = {
            name: AgentPolicy(
                name=name,
                read=frozenset(cfg.get("read", [])),
                write=frozenset(cfg.get("write", [])),
                max_level=self._order.index(cfg.get("max_classification", "public")),
            )
            for name, cfg in raw.get("agents", {}).items()
        }

    def classification_of(self, key: str) -> str:
        return self._facts.get(key, DEFAULT_CLASSIFICATION)

    def _level(self, key: str) -> int:
        return self._order.index(self.classification_of(key))

    def for_agent(self, agent: str) -> AgentPolicy:
        # Unknown agent -> empty policy (deny everything).
        return self._agents.get(agent, AgentPolicy(agent, frozenset(), frozenset(), -1))

    def can_read(self, agent: str, key: str) -> bool:
        p = self.for_agent(agent)
        return key in p.read and self._level(key) <= p.max_level

    def can_write(self, agent: str, key: str) -> bool:
        return key in self.for_agent(agent).write


@lru_cache(maxsize=1)
def load_policy(path: Path = SETTINGS.policy_path) -> Policy:
    with open(path, encoding="utf-8") as f:
        return Policy(yaml.safe_load(f))

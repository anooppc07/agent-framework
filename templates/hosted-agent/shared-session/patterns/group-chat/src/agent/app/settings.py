"""Central configuration and the request-scoped identity every call must carry."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _policy_path() -> Path:
    """POLICY_PATH overrides. Otherwise config/policy.yaml next to app/ (every template vendors both)."""
    override = os.environ.get("POLICY_PATH", "").strip()
    return Path(override) if override else ROOT / "config" / "policy.yaml"


@dataclass(frozen=True)
class Settings:
    cosmos_endpoint: str = os.environ.get("AZURE_COSMOS_ENDPOINT", "")
    cosmos_db: str = os.environ.get("AZURE_COSMOS_DATABASE_NAME", "agent-framework")
    history_container: str = os.environ.get("COSMOS_HISTORY_CONTAINER", "chat-history")
    facts_container: str = os.environ.get("COSMOS_FACTS_CONTAINER", "shared-facts")
    checkpoint_container: str = os.environ.get("COSMOS_CHECKPOINT_CONTAINER", "workflow-checkpoints")
    fact_ttl_seconds: int = int(os.environ.get("FACT_TTL_SECONDS", "86400"))
    history_ttl_seconds: int = int(os.environ.get("HISTORY_TTL_SECONDS", "2592000"))      # 30 days
    checkpoint_ttl_seconds: int = int(os.environ.get("CHECKPOINT_TTL_SECONDS", "604800"))  # 7 days
    policy_path: Path = _policy_path()


SETTINGS = Settings()

# Default tenant for a single-tenant deployment (one hosted agent per company).
# Multi-tenant hosts must resolve the tenant per request from a trusted claim instead.
DEFAULT_TENANT_ID = os.environ.get("TENANT_ID", "")
APP_ENV = os.environ.get("APP_ENV", "production").lower()
_NON_PROD = {"dev", "test", "local"}


def _load_pepper() -> str:
    """Fail closed: outside dev/test a missing or short pepper stops the process, instead of
    silently hashing everyone's ids with a public default. Changing the pepper later orphans
    all existing facts, history and checkpoints (new partitions), so set it once."""
    pepper = os.environ.get("SCOPE_HASH_PEPPER", "")
    if APP_ENV in _NON_PROD:
        return pepper or "dev-only-pepper-not-secret"
    if len(pepper) < 32:
        raise RuntimeError(
            "SCOPE_HASH_PEPPER must be set (>= 32 chars, from Key Vault) when APP_ENV is "
            f"'{APP_ENV}'. Generate one with: openssl rand -hex 32")
    return pepper


SCOPE_HASH_PEPPER = _load_pepper()


def scope_hash(*parts: str) -> str:
    """Stable, non-reversible key for storage names (F2, F11).

    Framed as JSON so ("a.b", "c") and ("a", "b.c") can never collide, and peppered so a
    leaked partition key can't be brute-forced back to a user id. All services that must
    see the same facts need the same SCOPE_HASH_PEPPER (keep it in Key Vault).
    """
    framed = json.dumps([SCOPE_HASH_PEPPER, *parts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()[:40]


@dataclass(frozen=True)
class RequestScope:
    """Who is asking, and inside which conversation.

    tenant_id and user_id MUST come from a trusted source: the platform's verified user
    (Foundry hosted scope.user_id) or a validated Entra token. Never from the request body.
    conversation_id may come from the caller: it is always combined with the trusted user,
    so sending someone else's conversation id gives you an empty, different partition.

    Every storage key is hashed: raw tenant, user and conversation ids are never written.
    Hashes are hex, so the "." separator is legal in Cosmos ids and partition keys (F4).
    """

    tenant_id: str
    user_id: str
    conversation_id: str

    @property
    def key(self) -> str:
        return scope_hash(self.tenant_id, self.user_id, self.conversation_id)

    @property
    def facts_partition(self) -> str:
        """Partition for shared facts: one per tenant + user + conversation."""
        return f"f.{self.key}"

    def private_session_id(self, agent_name: str) -> str:
        """Each agent gets its OWN history partition, so transcripts never leak."""
        return f"h.{scope_hash(self.tenant_id, self.user_id, self.conversation_id, agent_name)}"

    @property
    def checkpoint_prefix(self) -> str:
        """Prefix for checkpoint workflow names, one per tenant + user + conversation (F3)."""
        return f"c.{self.key}."

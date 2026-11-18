"""Shared, policy-filtered fact store on Azure Cosmos DB (NoSQL).

This is the ONLY channel through which agents share information with each other.
Transcripts stay private in each agent's own history partition.

Container: shared-facts, partition key /scope, container TTL ON (provisioned by infra/cosmos/main.tf).
The app never creates databases or containers: with Entra-only data-plane auth, a create
call is refused with 403 (verification case 2).

Document (no raw tenant, user or conversation id is stored, F11):
    {
      "id": "<key>",                     # one current value per key per scope
      "scope": "f.<sha256(tenant,user,conversation)>",
      "key": "refund_reason",
      "value": "...",                    # redacted, max 4000 chars, never blank
      "classification": "confidential",
      "owner_agent": "billing",
      "updated_at": "2026-11-18T03:00:00Z",
      "ttl": 86400
    }

Concurrency: last writer wins per key. Use the `_etag` with an if-match condition if
two agents may legitimately race on the same key.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Protocol

from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

from app.memory.policy import Policy, load_policy
from app.memory.redaction import redact
from app.settings import _NON_PROD, APP_ENV, SETTINGS, RequestScope

audit = logging.getLogger("audit.shared_facts")

MAX_VALUE_CHARS = 4000

# `key` and `value` are reserved words in Cosmos SQL: `c.value` fails with SC1001 (F1).
# Always use bracket notation for these fields.
READ_QUERY = 'SELECT c["key"], c["value"] FROM c WHERE ARRAY_CONTAINS(@keys, c["key"])'


class AccessDenied(PermissionError):
    pass


class InvalidFact(ValueError):
    pass


class FactStore(Protocol):
    """What ScopedFactsProvider needs. SharedFactStore is the implementation here (direct Cosmos)."""

    async def read_visible(self, scope: RequestScope, agent: str) -> dict[str, str]: ...
    async def publish(self, scope: RequestScope, agent: str, key: str, value: str) -> None: ...
    async def writable_keys(self, agent: str) -> list[str]: ...


class SharedFactStore:
    def __init__(self, container: ContainerProxy, policy: Policy | None = None):
        self._c = container
        self._policy = policy or load_policy()

    # ---------- lifecycle ----------
    @classmethod
    def from_client(cls, client: CosmosClient) -> "SharedFactStore":
        """Bind to the pre-provisioned container. No create call: a missing container
        surfaces as a plain 404 on first use instead of a misleading 403."""
        container = client.get_database_client(SETTINGS.cosmos_db).get_container_client(
            SETTINGS.facts_container)
        return cls(container)

    async def writable_keys(self, agent: str) -> list[str]:
        return sorted(self._policy.for_agent(agent).write)

    # ---------- write ----------
    async def publish(self, scope: RequestScope, agent: str, key: str, value: str) -> None:
        if not self._policy.can_write(agent, key):
            audit.warning("DENY write agent=%s key=%s scope=%s", agent, key, scope.facts_partition)
            raise AccessDenied(f"Agent '{agent}' may not write '{key}'")

        clean = redact(value or "").strip()
        if not clean:
            raise InvalidFact("fact value must not be empty or whitespace")

        doc = {
            "id": key,
            "scope": scope.facts_partition,
            "key": key,
            "value": clean[:MAX_VALUE_CHARS],  # cap size so no agent can flood the others
            "classification": self._policy.classification_of(key),
            "owner_agent": agent,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "ttl": SETTINGS.fact_ttl_seconds,
        }
        await self._c.upsert_item(doc)
        audit.info("WRITE agent=%s key=%s scope=%s", agent, key, scope.facts_partition)

    # ---------- read ----------
    async def read_visible(self, scope: RequestScope, agent: str) -> dict[str, str]:
        """Return ONLY the facts this agent is allowed to see. Single-partition query."""
        allowed = sorted(self._policy.for_agent(agent).read)
        if not allowed:
            return {}

        items = self._c.query_items(
            query=READ_QUERY,
            parameters=[{"name": "@keys", "value": allowed}],
            partition_key=scope.facts_partition,  # tenant + user + conversation isolation
        )
        visible: dict[str, str] = {}
        async for item in items:
            if self._policy.can_read(agent, item["key"]):  # classification check
                visible[item["key"]] = item["value"]

        audit.info("READ agent=%s keys=%s scope=%s", agent, list(visible), scope.facts_partition)
        return visible

    # ---------- recovery: carry facts into a fresh conversation ----------
    async def carry_over(self, src: RequestScope, dst: RequestScope) -> int:
        """Copy all facts from src to dst if dst has none yet. Returns the number copied.

        Used when the host has BLOCKED a conversation after a failed turn and the front end
        starts a fresh one. Safe only because both scopes are built from the same TRUSTED
        user and tenant; never call it with scopes for different users.
        """
        if (src.tenant_id, src.user_id) != (dst.tenant_id, dst.user_id):
            raise AccessDenied("carry-over is only allowed within one tenant and user")
        if src.facts_partition == dst.facts_partition:
            return 0
        async for _ in self._c.query_items("SELECT TOP 1 c.id FROM c", partition_key=dst.facts_partition):
            return 0  # dst already has facts: this is not its first turn
        n = 0
        async for doc in self._c.query_items("SELECT * FROM c", partition_key=src.facts_partition):
            doc = {k: v for k, v in doc.items() if not k.startswith("_")}  # drop _rid, _etag, ...
            doc["scope"] = dst.facts_partition
            doc["carried_over"] = True
            await self._c.upsert_item(doc)
            n += 1
        audit.info("CARRY_OVER src=%s dst=%s copied=%d", src.facts_partition, dst.facts_partition, n)
        return n

    # ---------- right-to-be-forgotten ----------
    async def purge_conversation(self, scope: RequestScope) -> int:
        n = 0
        async for item in self._c.query_items(
            "SELECT c.id FROM c", partition_key=scope.facts_partition
        ):
            await self._c.delete_item(item["id"], partition_key=scope.facts_partition)
            n += 1
        audit.info("PURGE scope=%s deleted=%d", scope.facts_partition, n)
        return n


def cosmos_credential():
    """Which identity talks to Cosmos.

    Deployed (APP_ENV is not dev/test/local): the agent's own managed identity, deterministic, no fallback chain
    (Azure guidance: ManagedIdentityCredential in production). Set AZURE_CLIENT_ID only for a user-assigned identity.
    Local (APP_ENV dev/test/local): DefaultAzureCredential, which picks up `az login`.
    The data-plane role must be granted to that identity: hooks/postdeploy.sh does it, on one database only.
    """
    if APP_ENV in _NON_PROD:
        return DefaultAzureCredential()
    return ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID") or None)


def cosmos_client() -> CosmosClient:
    """Entra ID auth only (key auth is disabled on the account). No create calls: containers are provisioned."""
    return CosmosClient(SETTINGS.cosmos_endpoint, credential=cosmos_credential())

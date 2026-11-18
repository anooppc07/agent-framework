"""Tenant-safe workflow checkpoints on Cosmos DB.

Why a wrapper (verification F3/F4/F5/F7):
  * CosmosCheckpointStorage partitions by workflow_name only, and load(id)/delete(id) run a
    cross-partition query, so one tenant could load or delete another tenant's checkpoint.
  * Cosmos forbids "/" in document ids, so scope prefixes use "." (hex hashes, no raw ids).
  * The handoff "wait for the user" request carries list[Message] (a types.GenericAlias).
    Without allow-listing it, the checkpoint at the pause is silently NOT written and a
    restart resumes from before the pause.
  * A hosted workflow (ResponsesHostServer) owns checkpointing: it must get a
    checkpoint_store_provider, never a builder-level checkpoint_storage.

ScopedCheckpointStorage implements the full CheckpointStorage protocol:
  save, load, list_checkpoints, delete, get_latest, list_checkpoint_ids.
It also strips the host-injected raw user id from checkpoint bodies on save (see below).
"""
from __future__ import annotations

import dataclasses
import logging

from agent_framework import CheckpointStorage, WorkflowCheckpoint
from agent_framework.exceptions import WorkflowCheckpointException
from agent_framework_azure_cosmos import CosmosCheckpointStorage
from agent_framework_foundry_hosting import CheckpointStoreProvider, FoundryRequestScope
from azure.cosmos.aio import CosmosClient

from app.settings import DEFAULT_TENANT_ID, SETTINGS, RequestScope, scope_hash

log = logging.getLogger("audit.checkpoints")

# One extra type beyond MAF's safe set, needed for the handoff user-input pause (F5).
# This widens the pickle allow-list by exactly one type. See README "Open decisions".
ALLOWED_CHECKPOINT_TYPES = ["types:GenericAlias"]


# The host writes the trusted identity into every checkpoint under
# state._workflow_run_kwargs.client_kwargs.*.additional_function_arguments (and the resolved
# copies). It never reads that stored copy back: recovery pops additional_function_arguments
# and every turn re-injects the CURRENT trusted identity (foundry_hosting recovery_turn /
# prepare_workflow_kwargs). So the raw user id is dead weight in storage; strip it on save.
_IDENTITY_CONTAINER = "additional_function_arguments"
_RAW_IDENTITY_FIELDS = ("user_id",)


def _strip_identity(value):
    """Return a copy with raw user ids removed from every additional_function_arguments dict."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == _IDENTITY_CONTAINER and isinstance(v, dict):
                out[k] = {ik: iv for ik, iv in v.items() if ik not in _RAW_IDENTITY_FIELDS}
            else:
                out[k] = _strip_identity(v)
        return out
    if isinstance(value, list):
        return [_strip_identity(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_strip_identity(v) for v in value)
    return value


class ScopedCheckpointStorage:
    """Confines an inner CheckpointStorage to one scope prefix.

    Stored workflow_name = "<prefix><workflow_name>", so the Cosmos partition key itself is
    per tenant+user+conversation. Reads by id are verified against the prefix, and a
    mismatch looks exactly like "not found" (no existence oracle across tenants).
    """

    def __init__(self, inner: CheckpointStorage, prefix: str):
        if not prefix or "/" in prefix or "\\" in prefix or "#" in prefix or "?" in prefix:
            raise ValueError("prefix must be non-empty and Cosmos-id safe")
        self._inner = inner
        self._prefix = prefix

    def _wrap(self, cp: WorkflowCheckpoint) -> WorkflowCheckpoint:
        return dataclasses.replace(cp, workflow_name=self._prefix + cp.workflow_name,
                                   state=_strip_identity(cp.state))

    def _unwrap(self, cp: WorkflowCheckpoint) -> WorkflowCheckpoint:
        return dataclasses.replace(cp, workflow_name=cp.workflow_name[len(self._prefix):])

    def _mine(self, cp: WorkflowCheckpoint) -> bool:
        return cp.workflow_name.startswith(self._prefix)

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        return await self._inner.save(self._wrap(checkpoint))

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        try:
            cp = await self._inner.load(checkpoint_id)
        except WorkflowCheckpointException:
            raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}") from None
        if not self._mine(cp):
            log.warning("DENY cross-scope checkpoint load id=%s", checkpoint_id)
            raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}")
        return self._unwrap(cp)

    async def delete(self, checkpoint_id: str) -> bool:
        try:
            await self.load(checkpoint_id)  # ownership check first
        except WorkflowCheckpointException:
            return False
        return await self._inner.delete(checkpoint_id)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        cps = await self._inner.list_checkpoints(workflow_name=self._prefix + workflow_name)
        return [self._unwrap(c) for c in cps if self._mine(c)]

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        cp = await self._inner.get_latest(workflow_name=self._prefix + workflow_name)
        return self._unwrap(cp) if cp is not None and self._mine(cp) else None

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        return await self._inner.list_checkpoint_ids(workflow_name=self._prefix + workflow_name)


def cosmos_checkpoint_storage(client: CosmosClient) -> CosmosCheckpointStorage:
    """Inner store bound to the pre-provisioned container: no create call (case 3)."""
    container = client.get_database_client(SETTINGS.cosmos_db).get_container_client(
        SETTINGS.checkpoint_container)
    return CosmosCheckpointStorage(
        container_client=container,
        database_name=SETTINGS.cosmos_db,
        container_name=SETTINGS.checkpoint_container,
        allowed_checkpoint_types=ALLOWED_CHECKPOINT_TYPES,
    )


def scoped_checkpoints(client: CosmosClient, scope: RequestScope) -> ScopedCheckpointStorage:
    """For self-run workflows (scripts, your own API): one store per request scope."""
    return ScopedCheckpointStorage(cosmos_checkpoint_storage(client), scope.checkpoint_prefix)


class CosmosCheckpointStoreProvider(CheckpointStoreProvider):
    """For hosted workflows: pass as ResponsesHostServer(checkpoint_store_provider=...).

    The host resolves the trusted platform identity (FoundryRequestScope) and the workflow
    context id; we hash tenant + that identity + context into the partition (F3, F7).
    """

    def __init__(self, client: CosmosClient, *, tenant_id: str = DEFAULT_TENANT_ID):
        super().__init__(allowed_checkpoint_types=ALLOWED_CHECKPOINT_TYPES)
        if not tenant_id:
            raise ValueError("Set TENANT_ID (single-tenant) or pass tenant_id explicitly")
        self._inner = cosmos_checkpoint_storage(client)
        self._tenant = tenant_id

    def get_store_for_scope(
        self,
        *,
        scope: FoundryRequestScope | None,
        context_id: str,
        platform_context,
    ) -> CheckpointStorage:
        if scope is None:
            raise RuntimeError("Checkpoints require a resolved request scope")
        if scope.is_hosted and not scope.user_id:
            raise RuntimeError("Hosted checkpoints require a trusted platform user id")
        # storage_key = sha256(user_id, platform session id), computed by the host
        prefix = f"c.{scope_hash(self._tenant, scope.storage_key, context_id)}."
        return ScopedCheckpointStorage(self._inner, prefix)

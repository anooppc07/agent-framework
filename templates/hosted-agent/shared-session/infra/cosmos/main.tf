# Cosmos DB for the shared-session templates, SCBX shape. The app NEVER creates databases or containers:
# with Entra-only data-plane auth a create call is refused with 403 (verified), so everything is provisioned here.
#
#   * key auth is off (local_authentication_enabled = false) and public network access is DISABLED; a private
#     endpoint is created (README rule 2, contract R9);
#   * the agent identity gets the built-in data-plane role on THIS database only, in the post-deploy hook;
#   * container TTLs are on, because an item-level "ttl" is ignored when the container's TTL is off (verified).
#
# NOT validated or applied: the Terraform CLI was not available where this was written. Argument names were
# taken from the azurerm provider documentation (~> 4.2). Run `terraform init && terraform validate` first.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.2"
    }
  }
}

provider "azurerm" {
  features {}
  # The subscription comes from azd (ARM_SUBSCRIPTION_ID / AZURE_SUBSCRIPTION_ID) or `az account`; it is not set here.
}

locals {
  containers = {
    "chat-history"         = { partition_key = "/session_id", ttl = var.history_ttl_seconds }
    "shared-facts"         = { partition_key = "/scope", ttl = var.fact_ttl_seconds }
    "workflow-checkpoints" = { partition_key = "/workflow_name", ttl = var.checkpoint_ttl_seconds }
  }
}

resource "azurerm_cosmosdb_account" "this" {
  name                = var.account_name
  location            = var.location
  resource_group_name = var.resource_group_name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  local_authentication_enabled       = false # Entra only: account keys cannot be used for data
  access_key_metadata_writes_enabled = false
  public_network_access_enabled      = false
  minimal_tls_version                = "Tls12"
  tags                               = var.tags

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = var.location
    failover_priority = 0
    zone_redundant    = false
  }

  dynamic "capabilities" {
    for_each = var.serverless ? ["EnableServerless"] : []
    content {
      name = capabilities.value
    }
  }
}

resource "azurerm_cosmosdb_sql_database" "this" {
  name                = var.database_name
  resource_group_name = var.resource_group_name
  account_name        = azurerm_cosmosdb_account.this.name
}

resource "azurerm_cosmosdb_sql_container" "this" {
  for_each = local.containers

  name                  = each.key
  resource_group_name   = var.resource_group_name
  account_name          = azurerm_cosmosdb_account.this.name
  database_name         = azurerm_cosmosdb_sql_database.this.name
  partition_key_paths   = [each.value.partition_key]
  partition_key_version = 2
  default_ttl           = each.value.ttl # container TTL ON; items may override with their own "ttl"

  dynamic "autoscale_settings" {
    for_each = var.serverless ? [] : [1]
    content {
      max_throughput = 1000
    }
  }
}

resource "azurerm_private_endpoint" "cosmos" {
  name                = "pe-${var.account_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "cosmos"
    is_manual_connection           = false
    private_connection_resource_id = azurerm_cosmosdb_account.this.id
    subresource_names              = ["Sql"]
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }
}

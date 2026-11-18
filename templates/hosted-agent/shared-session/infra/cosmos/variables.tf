variable "location" {
  type        = string
  description = "Azure region for the account and the private endpoint."
}

variable "resource_group_name" {
  type        = string
  description = "Existing resource group the account is created in (the platform stamp owns the group)."
}

variable "account_name" {
  type        = string
  description = "Cosmos DB account name: globally unique, 3-44 characters, lowercase letters, digits and hyphens."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,42}[a-z0-9]$", var.account_name))
    error_message = "account_name must be 3-44 characters: lowercase letters, digits and hyphens, not starting or ending with a hyphen."
  }
}

variable "database_name" {
  type        = string
  default     = "agent-framework"
  description = "Database name. The agent is granted its data role on this database only."
}

variable "serverless" {
  type        = bool
  default     = true
  description = "Serverless suits dev and spiky load. Set false for provisioned autoscale (max 1000 RU/s per container)."
}

variable "private_endpoint_subnet_id" {
  type        = string
  description = "Resource ID of the subnet for the private endpoint (snet-private-endpoints in the Foundry LZ spoke)."
}

variable "private_dns_zone_id" {
  type        = string
  description = "Resource ID of the existing private DNS zone privatelink.documents.azure.com, linked in the hub. Created by the platform stamp, not here."
}

variable "fact_ttl_seconds" {
  type        = number
  default     = 86400
  description = "Shared facts expire after this many seconds."
}

variable "history_ttl_seconds" {
  type        = number
  default     = 2592000 # 30 days
  description = "Private per-agent chat history (raw user text). -1 = never expire."
}

variable "checkpoint_ttl_seconds" {
  type        = number
  default     = 604800 # 7 days
  description = "Workflow checkpoints (each holds the whole conversation, raw). -1 = never expire."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Tags applied to the account and the private endpoint."
}

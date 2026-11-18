# Upper-case output names become azd environment values (azd + Terraform convention), so these three reach
# azure.yaml (AZURE_COSMOS_ENDPOINT, AZURE_COSMOS_DATABASE_NAME) and the post-deploy hooks (COSMOS_ACCOUNT_RESOURCE_ID).

output "AZURE_COSMOS_ENDPOINT" {
  description = "Consumed by azure.yaml. Resolves to the private IP through the hub DNS."
  value       = azurerm_cosmosdb_account.this.endpoint
}

output "AZURE_COSMOS_DATABASE_NAME" {
  description = "Consumed by azure.yaml."
  value       = azurerm_cosmosdb_sql_database.this.name
}

output "COSMOS_ACCOUNT_RESOURCE_ID" {
  description = "Consumed by the post-deploy hook to scope the data-plane role."
  value       = azurerm_cosmosdb_account.this.id
}

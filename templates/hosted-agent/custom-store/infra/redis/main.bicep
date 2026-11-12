// Azure Managed Redis for the custom stores, SCBX shape. Adapted from microsoft-foundry/foundry-samples (MIT),
// agent-framework/responses/26-custom-store/infra/redis. Differences from upstream:
//   * public network access is DISABLED and a private endpoint is created (README rule 2, contract R9);
//   * key authentication stays disabled; the agent identity gets a data access policy in the post-deploy hook.
// NOT compiled or deployed: the Bicep CLI is not available where this was written.

@description('Location for the cluster. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Name of the Azure Managed Redis cluster.')
param clusterName string = 'amr-${uniqueString(resourceGroup().id)}'

@description('Azure Managed Redis SKU. Balanced_B0 is the smallest tier.')
param skuName string = 'Balanced_B0'

@description('Resource ID of the subnet for the private endpoint (snet-private-endpoints in the Foundry LZ spoke).')
param privateEndpointSubnetId string

@description('Resource ID of the existing private DNS zone privatelink.redis.azure.net, linked in the hub. Created by the platform stamp, not here.')
param privateDnsZoneId string

// EnterpriseCluster policy lets the stores run multi-key commands against one endpoint.
resource cluster 'Microsoft.Cache/redisEnterprise@2025-07-01' = {
  name: clusterName
  location: location
  sku: {
    name: skuName
  }
  properties: {
    publicNetworkAccess: 'Disabled'
  }
}

resource database 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = {
  parent: cluster
  name: 'default'
  properties: {
    accessKeysAuthentication: 'Disabled'
    clientProtocol: 'Encrypted'
    port: 10000
    clusteringPolicy: 'EnterpriseCluster'
    evictionPolicy: 'NoEviction'
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-${clusterName}'
  location: location
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'redis'
        properties: {
          privateLinkServiceId: cluster.id
          groupIds: [
            'redisEnterprise'
          ]
        }
      }
    ]
  }
}

resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'redis'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

@description('Hostname. Consumed by azure.yaml as REDIS_HOST; resolves to the private IP through the hub DNS.')
output REDIS_HOST string = cluster.properties.hostName

@description('TLS port. Consumed by azure.yaml as REDIS_PORT.')
output REDIS_PORT string = string(database.properties.port)

@description('Database resource ID. Consumed by the post-deploy hook.')
output REDIS_DATABASE_RESOURCE_ID string = database.id

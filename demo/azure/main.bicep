targetScope = 'resourceGroup'

@description('Public linux/amd64 app image pinned by sha256 digest.')
param appImage string
@description('Public linux/amd64 recruiting index image pinned by sha256 digest.')
param indexImage string
@minLength(64)
@maxLength(64)
param indexSha256 string
param location string = resourceGroup().location
@maxLength(20)
param name string = 'trial-matcher'
@description('Optional private demo login. Set both username and password, or leave both empty for public access.')
param demoUsername string = ''
@secure()
param demoPassword string = ''
@description('Informational tag only. Delete the demo resource group when finished; this does not schedule deletion.')
param reviewOn string

resource environment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: '${name}-env'
  location: location
  tags: { purpose: 'portfolio-demo', reviewOn: reviewOn }
  properties: {
    appLogsConfiguration: { destination: 'none' }
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
  }
}

resource app 'Microsoft.App/containerApps@2025-07-01' = {
  name: name
  location: location
  tags: { purpose: 'portfolio-demo', reviewOn: reviewOn }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      secrets: empty(demoPassword) ? [] : [ { name: 'demo-password', value: demoPassword } ]
    }
    template: {
      terminationGracePeriodSeconds: 60
      scale: {
        minReplicas: 0
        maxReplicas: 1
        cooldownPeriod: 300
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: '1' } } }
        ]
      }
      volumes: [ { name: 'index', storageType: 'EmptyDir' } ]
      initContainers: [
        {
          name: 'seed-index'
          image: indexImage
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [ { name: 'EXPECTED_INDEX_SHA256', value: indexSha256 } ]
          volumeMounts: [ { volumeName: 'index', mountPath: '/data' } ]
        }
      ]
      containers: [
        {
          name: 'matcher'
          image: appImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat([
            { name: 'TRIAL_INDEX_PATH', value: '/data/bm25.sqlite' }
            { name: 'TRIAL_PIPELINE', value: 'fusion' }
            { name: 'MAX_SEARCHES_PER_MINUTE', value: '10' }
            { name: 'DEMO_USERNAME', value: demoUsername }
            { name: 'REQUIRE_AUTH', value: empty(demoUsername) ? 'false' : 'true' }
          ], empty(demoPassword) ? [] : [ { name: 'DEMO_PASSWORD', secretRef: 'demo-password' } ])
          volumeMounts: [ { volumeName: 'index', mountPath: '/data' } ]
          probes: [
            { type: 'Startup', httpGet: { path: '/health/ready', port: 8000 }, periodSeconds: 5, timeoutSeconds: 3, failureThreshold: 60 }
            { type: 'Readiness', httpGet: { path: '/health/ready', port: 8000 }, periodSeconds: 10, timeoutSeconds: 3, failureThreshold: 3 }
            { type: 'Liveness', httpGet: { path: '/health/live', port: 8000 }, periodSeconds: 30, timeoutSeconds: 3, failureThreshold: 3 }
          ]
        }
      ]
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'

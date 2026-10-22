import assert from 'node:assert/strict'

// Check the running server, not a separate production build. A failed Vite
// config reload can leave an older plugin configuration serving HTTP 200.
const origin = process.env.HUB_DEV_UI_URL || 'http://localhost:5173'
const pages = [
  ['/src/features/hub/views/BaselineHubView.vue', 'ElTabs'],
  ['/src/features/hub/views/BaselineTrackView.vue', 'ElCard'],
  ['/src/features/hub/views/BaselineHubDetailView.vue', 'ElCard'],
  ['/src/platform/views/ReportsView.vue', 'ElTable'],
  ['/src/platform/views/ReportView.vue', 'ElButton'],
]
for (const [path, component] of pages) {
  const response = await fetch(new URL(path, origin))
  assert.equal(response.status, 200, `${path}: module load failed`)
  const source = await response.text()
  assert.ok(source.split('\n').some(line => line.startsWith('import') && line.includes(component)),
    `${path}: ${component} import missing; restart Vite after installing dependencies`)
  console.log(`OK ${path}: ${component} resolved`)
}
const health = await fetch(new URL('/api/health', origin))
assert.equal(health.status, 200, 'API proxy health check failed')
console.log('OK API proxy health')

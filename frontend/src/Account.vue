<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from './platform/i18n'
import { exactTime, relativeTime } from './platform/utils/time'
import { api, auth, setSession, type Session } from './client'
import { api as platformApi } from './platform/api/client'
import type { PublicationSummary } from './publication'
import ContestTokenCard from './features/hub/components/ContestTokenCard.vue'
const identity = ref<{ enabled: boolean; login: string | null; publishing_authorized: boolean }>()
const error = ref(''), username = ref(auth.upstream?.username || ''), password = ref('')
const publications = ref<PublicationSummary[]>([])
const publishing = ref(false)
const busy = ref(false), saved = ref(false), binding = ref(false)
const route = useRoute()
const router = useRouter()
const { locale } = useI18n()
const text = (zh: string, en: string) => locale.value === 'zh' ? zh : en
const defaultAccountTab = !auth.upstream?.connected || route.query.github_error ? 'connections' : 'profile'
const tab = computed({
  get: () => ['profile', 'connections', 'publications'].includes(String(route.query.tab))
    ? String(route.query.tab) : defaultAccountTab,
  set: (value: string) => { void router.replace({ query: { ...route.query, tab: value } }) },
})
const publicationsLoading = ref(false), publicationsLoaded = ref(false)
const contributor = ref(''), contributorSaved = ref(false), profileBusy = ref(false)
onMounted(async () => {
  if (typeof route.query.github_error === 'string') error.value = route.query.github_error
  if (auth.user) { await loadIdentity(); await loadPublications() }
  if (auth.user && auth.upstream?.connected) {
    try { contributor.value = (await platformApi.get('/auth/me')).data.contributor || '' }
    catch (e) { error.value = (e as Error).message }
  }
})
async function saveContributor() {
  profileBusy.value = true; contributorSaved.value = false
  try {
    await platformApi.put('/auth/me/contributor', { contributor: contributor.value })
    contributorSaved.value = true
  } catch (e) { error.value = (e as Error).message }
  finally { profileBusy.value = false }
}
async function loadIdentity() {
  try { identity.value = await api('/auth/github/identity') }
  catch (e) { error.value = (e as Error).message }
}
async function loadPublications() {
  if (publicationsLoading.value) return
  publicationsLoading.value = true
  try {
    publications.value = await api('/catalog-export/publications')
    publicationsLoaded.value = true
  } catch (e) { error.value = (e as Error).message }
  finally { publicationsLoading.value = false }
}
async function authorizePublishing() {
  if (publishing.value) return
  publishing.value = true; error.value = ''
  try {
    const result = await api<{ url: string }>('/auth/github/publishing/authorize', 'POST')
    location.assign(result.url)
  } catch (e) { error.value = (e as Error).message; publishing.value = false }
}
async function revokePublishing() {
  publishing.value = true; error.value = ''
  try { await api('/auth/github/publishing', 'DELETE'); await loadIdentity() }
  catch (e) { error.value = (e as Error).message }
  finally { publishing.value = false }
}
async function bind() {
  if (binding.value) return
  binding.value = true; error.value = ''
  try { const result = await api<{ url: string }>('/auth/github/bind', 'POST'); location.assign(result.url) }
  catch (e) { error.value = (e as Error).message; binding.value = false }
}
async function connect() {
  if (busy.value) return
  busy.value = true; saved.value = false; error.value = ''
  try {
    setSession(await api<Session>('/auth/upstream/connect', 'POST', { username: username.value, password: password.value }))
    saved.value = true
    try { contributor.value = (await platformApi.get('/auth/me')).data.contributor || '' }
    catch (e) { error.value = (e as Error).message }
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false; password.value = '' }
}
</script>
<template>
  <section v-if="auth.user" class="page account-page">
    <div class="account-heading">
      <div><h1 class="page-title">{{ text('账号', 'Account') }}</h1><p class="muted">{{ auth.user.username }}</p></div>
      <span class="connection-state" :class="{ disconnected: !auth.upstream?.connected }">
        {{ auth.upstream?.connected ? text('AutoTune 已连接', 'AutoTune connected') : text('AutoTune 需要重新连接', 'Reconnect AutoTune') }}
      </span>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="account-alert" />
    <el-tabs v-model="tab">
      <el-tab-pane name="profile" :label="text('基本信息', 'Profile')">
        <section class="settings-section">
          <div class="section-heading"><h2>{{ text('公开署名', 'Public name') }}</h2><p class="muted">{{ text('用于赛道提交和 LLMBench 展示。', 'Shown on track entries and LLMBench.') }}</p></div>
          <div v-if="auth.upstream?.connected" class="section-content">
            <el-form label-position="top" @submit.prevent="saveContributor">
              <el-form-item :label="text('署名', 'Display name')"><el-input v-model="contributor" maxlength="255" :placeholder="auth.user.username" /></el-form-item>
              <el-button type="primary" native-type="submit" :loading="profileBusy">{{ text('保存署名', 'Save name') }}</el-button>
              <span v-if="contributorSaved" class="saved-note" role="status">{{ text('署名已保存', 'Name saved') }}</span>
            </el-form>
          </div>
          <p v-else class="muted">{{ text('重新连接 AutoTune 后可修改署名。', 'Reconnect AutoTune to edit your public name.') }} <el-button link type="primary" @click="tab = 'connections'">{{ text('连接账号', 'Connect account') }}</el-button></p>
        </section>
      </el-tab-pane>
      <el-tab-pane name="connections" :label="text('连接与授权', 'Connections')">
        <section class="settings-section">
          <div class="section-heading"><h2>AutoTune</h2><p class="muted">{{ text('用于查看赛道和提交评测。', 'Access tracks and submit benchmarks.') }}</p></div>
          <div class="section-content">
            <p class="identity-line">{{ auth.upstream?.username || auth.user.username }} <el-tag :type="auth.upstream?.connected ? 'success' : 'warning'" effect="plain">{{ auth.upstream?.connected ? text('已连接', 'Connected') : text('连接已过期', 'Connection expired') }}</el-tag></p>
            <details class="connection-form" :open="!auth.upstream?.connected"><summary>{{ text('重新连接账号', 'Reconnect account') }}</summary>
              <el-form label-position="top" @submit.prevent="connect">
                <el-form-item :label="text('用户名', 'Username')"><el-input v-model="username" required autocomplete="username" /></el-form-item>
                <el-form-item :label="text('密码', 'Password')"><el-input v-model="password" type="password" required autocomplete="current-password" show-password /></el-form-item>
                <el-button type="primary" native-type="submit" :loading="busy">{{ text('连接 AutoTune', 'Connect AutoTune') }}</el-button>
              </el-form>
            </details>
            <p v-if="saved" class="saved-note" role="status">{{ text('连接成功。', 'Connected.') }} <RouterLink to="/baseline-hub">{{ text('查看赛道', 'View tracks') }}</RouterLink></p>
          </div>
        </section>
        <section class="settings-section">
          <div class="section-heading"><h2>GitHub</h2><p class="muted">{{ text('用于登录和发布到 Catalog。', 'Sign in and publish to Catalog.') }}</p></div>
          <div class="section-content">
            <template v-if="identity?.login">
              <p class="identity-line">@{{ identity.login }} <el-tag type="success" effect="plain">{{ text('已绑定', 'Linked') }}</el-tag></p>
              <div class="authorization-row"><span>{{ text('Catalog 发布', 'Catalog publishing') }}</span><el-tag :type="identity.publishing_authorized ? 'success' : 'info'" effect="plain">{{ identity.publishing_authorized ? text('已授权', 'Authorized') : text('未授权', 'Not authorized') }}</el-tag></div>
              <p v-if="!identity.publishing_authorized" class="muted">{{ text('发布需要公开仓库授权，用于创建 fork、提交文件和 PR。每次发布前仍需审阅变更并确认。', 'Publishing requires public repository access to create forks, commits and PRs. Review and confirm changes before each publication.') }}</p>
              <el-button v-if="!identity.publishing_authorized" type="primary" :disabled="!identity.enabled" :loading="publishing" @click="authorizePublishing">{{ text('授权 GitHub 发布', 'Authorize publishing') }}</el-button>
              <details v-else class="connection-form"><summary>{{ text('管理发布授权', 'Manage publishing authorization') }}</summary>
                <p class="muted">{{ text('发布授权用于创建 fork、提交文件和 PR。', 'Publishing access is used to create forks, commits and PRs.') }}</p>
                <div class="settings-actions"><el-button :disabled="!identity.enabled" :loading="publishing" @click="authorizePublishing">{{ text('重新授权', 'Reauthorize') }}</el-button><el-button :disabled="publishing" @click="revokePublishing">{{ text('撤销本站发布授权', 'Revoke publishing access') }}</el-button></div>
              </details>
            </template>
            <template v-else-if="identity?.enabled"><p class="muted">{{ text('绑定后可使用 GitHub 登录同一个 Hub 账号。', 'Link GitHub to sign in to the same Hub account.') }}</p><el-button :loading="binding" @click="bind">{{ text('绑定 GitHub', 'Link GitHub') }}</el-button></template>
            <p v-else-if="identity" class="muted">{{ text('GitHub 登录尚未配置，请联系管理员。', 'GitHub sign-in is not configured. Contact your administrator.') }}</p>
            <p v-else class="muted">{{ text('正在读取绑定状态…', 'Loading connection…') }}</p>
            <el-button link type="primary" class="refresh-identity" @click="loadIdentity">{{ text('刷新状态', 'Refresh status') }}</el-button>
          </div>
        </section>
        <section v-if="auth.upstream?.connected" class="settings-section token-section">
          <div class="section-heading"><h2>{{ text('竞赛凭证', 'Contest credentials') }}</h2><p class="muted">{{ text('参与竞赛赛道时使用。', 'Used when entering contest tracks.') }}</p></div>
          <details class="section-content connection-form"><summary>{{ text('管理竞赛 Token', 'Manage contest token') }}</summary><ContestTokenCard :me="auth.user" :reload="loadIdentity" /></details>
        </section>
      </el-tab-pane>
      <el-tab-pane name="publications" :label="text('发布历史', 'Publications')">
        <div class="publication-heading"><p class="muted">{{ text('查看发布进度、审阅变更或打开 PR。', 'Track publications, review changes or open PRs.') }}</p><el-button :loading="publicationsLoading" @click="loadPublications">{{ text('刷新', 'Refresh') }}</el-button></div>
        <el-table v-loading="publicationsLoading" :data="publications" :empty-text="publicationsLoaded ? text('暂无发布任务。在赛道或测量详情中选择结果，发布到 Catalog。', 'No publications yet. Select a track result and publish to Catalog.') : text('发布历史未加载，请刷新重试。', 'Publications could not be loaded. Refresh to retry.')">
          <el-table-column :label="text('发布任务', 'Publication')" min-width="230"><template #default="{ row }"><RouterLink :to="`/publications/${row.id}`">{{ row.title }}</RouterLink><div class="muted">{{ row.repository }}</div></template></el-table-column>
          <el-table-column prop="status" :label="text('状态', 'Status')" min-width="140" />
          <el-table-column :label="text('创建时间', 'Created')" min-width="130"><template #default="{ row }"><span :title="exactTime(row.created_at)">{{ relativeTime(row.created_at) }}</span></template></el-table-column>
          <el-table-column label="PR" width="100"><template #default="{ row }"><a v-if="row.pr_url" :href="row.pr_url" target="_blank" rel="noopener noreferrer">#{{ row.pr_number }}</a><span v-else class="muted">—</span></template></el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </section>
  <section v-else class="auth-panel"><h1>{{ text('请先登录', 'Sign in to continue') }}</h1><RouterLink class="button" to="/login">{{ text('前往登录', 'Sign in') }}</RouterLink></section>
</template>

<style scoped>
.account-page { max-width: 1120px; }
.account-heading, .publication-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.account-heading { margin-bottom: 24px; }
.account-heading p { margin: 0; }
.connection-state { font-size: 13px; color: var(--el-color-primary); }
.connection-state.disconnected { color: #94630d; }
.account-alert { margin-bottom: 20px; }
.settings-section { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 32px; padding: 28px 0; border-bottom: 1px solid var(--autotune-border); }
.settings-section:last-child { border-bottom: 0; }
.section-heading h2 { font-size: 17px; margin: 0 0 6px; font-weight: 600; }
.section-heading p { margin: 0; max-width: 28ch; }
.section-content { min-width: 0; max-width: 540px; }
.identity-line { display: flex; align-items: center; gap: 12px; margin: 0 0 16px; font-weight: 500; }
.connection-form summary { cursor: pointer; font-size: 14px; color: var(--el-color-primary); }
.connection-form[open] > summary { margin-bottom: 18px; }
.authorization-row, .settings-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.refresh-identity { display: block; margin-top: 16px; }
.saved-note { color: var(--el-color-primary); margin-left: 12px; font-size: 13px; }
.token-section :deep(.el-card) { border: 0; background: transparent; }
.token-section :deep(.el-card__body) { padding: 0; }
.publication-heading { margin: 8px 0 20px; }
a { color: var(--el-color-primary); text-decoration: none; }
a:hover { text-decoration: underline; }
@media (max-width: 720px) {
  .settings-section { grid-template-columns: 1fr; gap: 18px; padding: 24px 0; }
  .account-heading { align-items: flex-start; flex-direction: column; }
  .section-heading p { max-width: none; }
}
</style>

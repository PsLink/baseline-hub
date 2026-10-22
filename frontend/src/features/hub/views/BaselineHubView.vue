<script setup lang="ts">
/** The Hub index offers card and list views of the same tracks.
 * Legacy submissions remain available in a separate drawer. */
import { formatRankingValue } from '../utils/board'
import { inLineLabel, queueTooltip as queueTooltipOf } from '../utils/queue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ownsSubmission } from '@/plugins/api'
import { type BaselineSubmission, type BaselineTrack, type FlagSpec } from '../types'
import BaselineSubmitDialog from '../components/BaselineSubmitDialog.vue'
import BaselineTrackForm from '../components/BaselineTrackForm.vue'
import { InfoHint, PageLoading, PluginSlot } from '@/plugins/api'
import { useFirstLoad } from '@/plugins/api'
import { useI18n } from '@/plugins/api'
import { useAuthStore } from '@/plugins/api'
import { apiErrorText } from '@/plugins/api'
import { useLinkTo, useRowLink } from '@/plugins/api'
import { usePoll } from '@/plugins/api'
import { exactTime, relativeTime } from '@/plugins/api'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { t, locale } = useI18n()
const inLine = (row: { queued_reason?: string; queue_ahead?: string[] }) => inLineLabel(t, row)
const queueTooltip = (row: { queued_reason?: string; queue_ahead?: string[] }) => queueTooltipOf(t, row)
const tracks = ref<BaselineTrack[]>([])
const submissions = ref<BaselineSubmission[]>([])
const gpuTypes = ref<string[]>([])
const flagCatalog = ref<Record<string, FlagSpec[]>>({})
const isAdmin = computed(() => auth.user?.role === 'admin')
const legacyOpen = ref(false)
type TrackView = 'cards' | 'list'
const viewStorageKey = 'autotune.hub.trackView'
function savedTrackView(): TrackView {
  try {
    return localStorage.getItem(viewStorageKey) === 'cards' ? 'cards' : 'list'
  } catch {
    return 'list'
  }
}
const trackView = ref<TrackView>(savedTrackView())
watch(trackView, (value) => {
  try {
    localStorage.setItem(viewStorageKey, value)
  } catch {
    // View switching still works when browser storage is unavailable.
  }
})
const trackRows = useRowLink((row: BaselineTrack) => `/baseline-hub/tracks/${row.slug}`)

/** Retired tracks (superseded boards, one-off backfill siblings) stay in the
 *  database for provenance but clutter the grid; hidden by default. */
const hideRetired = ref(true)
const retiredCount = computed(() => tracks.value.filter((x) => x.status === 'retired').length)
const search = ref('')
const visibleTracks = computed(() => tracks.value.filter((track) => {
  const query = search.value.trim().toLowerCase()
  return (!hideRetired.value || track.status !== 'retired')
    && (!query || [track.name, track.model_name, track.gpu_type].join(' ').toLowerCase().includes(query))
}))
/** Which tab, in the URL, so a tab is a link somebody can send. */
// Old revenue-board links fall back to tracks while that tab is hidden.
const tabOf = (q: unknown) => (typeof q === 'string' && q !== 'revenue' ? q : 'tracks')
const tab = ref(tabOf(route.query.tab))
watch(tab, (name) => {
  if (name !== tabOf(route.query.tab))
    router.replace({ query: name === 'tracks' ? {} : { tab: name } })
})
// …and the other way: back/forward, or the nav's "Baseline Hub" while on
// another tab, change the URL without remounting this page.
watch(() => route.query.tab, (q) => {
  tab.value = tabOf(q)
})
const linkTo = useLinkTo()
const legacyRows = useRowLink((row: BaselineSubmission) => `/baseline-hub/${row.id}`)

const submitOpen = ref(false)
const resubmitOf = ref<BaselineSubmission | null>(null)
const trackFormOpen = ref(false)

async function load() {
  const [t, s] = await Promise.all([
    api.get('/baseline-hub/tracks'),
    api.get('/baseline-hub/submissions'),
  ])
  tracks.value = t.data
  submissions.value = s.data
}

/** Everything that belongs to no track — the free-form path, by definition. */
const legacy = computed(() => submissions.value.filter((s) => !s.track_slug))
const showLegacy = computed(() => isAdmin.value || legacy.value.length > 0)

function openFreeForm() {
  resubmitOf.value = null
  submitOpen.value = true
}

function openResubmit(s: BaselineSubmission) {
  resubmitOf.value = s
  submitOpen.value = true
}

function hardwareAsk(s: BaselineSubmission): string {
  return `${s.gpu_count}×${s.gpu_type || 'any'}`
}

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'
function statusType(status: string): TagType {
  const looks: Record<string, TagType> = {
    queued: 'info', running: 'primary', measured: 'success', failed: 'danger', aborted: 'warning',
  }
  return looks[status] ?? 'info'
}
/** Quantization belongs to the model name; when it is there, the separate
 *  precision facet is noise. */
function showPrecision(track: BaselineTrack): boolean {
  if (!track.precision) return false
  return !track.model_name.toLowerCase().includes(track.precision.toLowerCase())
}

function trackStatusType(status: string): TagType {
  return ({ open: 'success', draft: 'warning', retired: 'info' } as Record<string, TagType>)[status] ?? 'info'
}

function latestValue(s: BaselineSubmission): string {
  const m = s.latest_measurement
  if (!m) return '—'
  if (m.passed === false) return 'not passed'
  const value = m.objective_value ?? m.score
  return value == null ? (m.passed ? 'passed' : 'no score') : fmt(value)
}
function fmt(value: number | null): string {
  if (value == null) return '—'
  return Math.abs(value) >= 100 ? value.toFixed(0) : value.toPrecision(4)
}
function leaderValue(track: BaselineTrack): string {
  return formatRankingValue(track.leader?.value, track.ranking_metric)
}
function leaderCaption(track: BaselineTrack): string {
  return track.ranking_metric.endsWith('tpm_card_norm')
    ? t('baselineHub.ranOn', { n: track.leader?.gpu_count ?? 0 })
    : track.ranking_metric || t('baselineHub.detail.benchmarkScore')
}

async function retryBenchmark(track: BaselineTrack) {
  try {
    const { data } = await api.post(`/baseline-hub/tracks/${track.slug}/retry-benchmark`)
    if (data.status === 'draft')
      ElMessage.warning(data.benchmark_error || t('baselineHub.msg.stillDraft'))
    else ElMessage.success(t('baselineHub.msg.trackOpen', { slug: data.benchmark_slug }))
    await load()
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.msg.retryFailed')))
  }
}

async function abort(s: BaselineSubmission) {
  try {
    await ElMessageBox.confirm(
      s.status === 'running'
        ? t('baselineHub.msg.abortRunning', { name: s.name })
        : t('baselineHub.msg.abortQueued', { name: s.name }),
      t('baselineHub.msg.abortTitle'),
      {
        confirmButtonText: t('baselineHub.msg.abortConfirm'),
        cancelButtonText: t('baselineHub.msg.keep'),
        type: 'warning',
      },
    )
  } catch {
    return
  }
  try {
    await api.post(`/baseline-hub/submissions/${s.id}/abort`)
    await load()
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.msg.abortFailed')))
  }
}

const first = useFirstLoad(load)
const poll = usePoll(load, 10000)
onMounted(async () => {
  // The page's own data first; the lists only its dialogs use come alongside.
  api.get('/machines/gpu-types')
    .then(({ data }) => (gpuTypes.value = data.gpu_types))
    .catch(() => (gpuTypes.value = []))
  api.get('/baseline-hub/flag-catalog')
    .then(({ data }) => (flagCatalog.value = data.engines))
    .catch(() => (flagCatalog.value = {}))
  if (!auth.user) void auth.fetchMe().catch(() => undefined)
  await first.run()
  poll.start()
})
</script>

<template>
  <div class="page">
    <div class="header-row">
      <div>
        <h1 class="page-title">{{ t('baselineHub.title') }}</h1>
        <span class="muted">
          {{ t('baselineHub.subtitle') }}
          <InfoHint :width="420">{{ t('baselineHub.about') }}</InfoHint>
        </span>
      </div>
      <div class="actions">
        <el-button v-if="showLegacy" text size="small" class="legacy-button"
          @click="legacyOpen = true">
          {{ t('baselineHub.legacy') }}<span v-if="legacy.length" class="count">{{ legacy.length }}</span>
        </el-button>
        <el-switch v-if="tab === 'tracks' && retiredCount" v-model="hideRetired" size="small"
          class="hide-retired"
          :active-text="t('baselineHub.hideRetired', { n: retiredCount })" />
        <el-button v-if="isAdmin && tab === 'tracks'" type="primary"
          @click="trackFormOpen = true">
          {{ t('baselineHub.newTrack') }}</el-button>
      </div>
    </div>

    <el-tabs v-model="tab" class="hub-index-tabs">
      <el-tab-pane name="tracks">
        <template #label>{{ t('baselineHub.tabs.tracks') }}</template>

        <div class="track-toolbar">
          <el-input v-model="search" clearable :aria-label="locale === 'zh' ? '搜索赛道' : 'Search tracks'" :placeholder="locale === 'zh' ? '搜索赛道、模型或 GPU' : 'Search tracks, models or GPUs'" class="track-search" />
          <el-radio-group v-model="trackView" size="small"
            :aria-label="t('baselineHub.view.label')">
            <el-radio-button value="cards">{{ t('baselineHub.view.cards') }}</el-radio-button>
            <el-radio-button value="list">{{ t('baselineHub.view.list') }}</el-radio-button>
          </el-radio-group>
        </div>
        <PageLoading v-if="!tracks.length && (first.loading || first.error)" :error="first.error"
          @retry="first.run" />
        <p v-else-if="!tracks.length" class="muted empty">
          {{ isAdmin ? t('baselineHub.noTracksAdmin') : t('baselineHub.noTracks') }}
        </p>
        <p v-if="tracks.length && !visibleTracks.length && trackView === 'cards'" class="muted empty">{{ locale === 'zh' ? '没有匹配的赛道，请调整搜索或筛选条件。' : 'No matching tracks. Adjust your search or filters.' }}</p>
        <div v-if="trackView === 'cards'" class="track-grid">
          <el-card v-for="track in visibleTracks" :key="track.id" shadow="hover" class="track-card"
            :class="track.status" v-bind="linkTo(`/baseline-hub/tracks/${track.slug}`)">
            <div class="track-head">
              <router-link :to="`/baseline-hub/tracks/${track.slug}`" class="nav-link">
                <b class="track-name">{{ track.name }}</b></router-link>
              <el-tag size="small" :type="trackStatusType(track.status)" effect="plain">
                {{ track.status }}</el-tag>
            </div>
            <div class="muted tiny">
              {{ track.model_name }} · {{ track.gpu_type }}
              <template v-if="showPrecision(track)"> · {{ track.precision }}</template>
            </div>
            <div class="gate mono">{{ track.gate_text }}</div>
            <div class="stats muted tiny">
              <span>{{ t(track.entry_count === 1 ? 'baselineHub.entryOne' : 'baselineHub.entryMany',
                { n: track.entry_count }) }}</span>
              <span>{{ t('baselineHub.measuredCount', { n: track.measured_count }) }}</span>
              <span>{{ t(track.images.length === 1 ? 'baselineHub.imageOne' : 'baselineHub.imageMany',
                { n: track.images.length }) }}</span>
            </div>
            <div v-if="track.leader" class="leader">
              <span class="muted tiny">{{ t('baselineHub.leading') }}</span>
              <b>{{ track.leader.name }}</b>
              <span v-if="track.leader.version > 1" class="muted"> v{{ track.leader.version }}</span>
              · <span class="mono">{{ leaderValue(track) }}</span>
              <span class="muted tiny">{{ leaderCaption(track) }}</span>
            </div>
            <div v-else class="muted tiny leader">{{ t('baselineHub.noRanked') }}</div>
            <div v-if="track.status === 'draft'" class="warn tiny draft-note">
              ⚠ {{ track.benchmark_error || t('baselineHub.noBenchmarkYet') }}
              <el-button v-if="isAdmin" size="small" @click.stop="retryBenchmark(track)">
                {{ t('baselineHub.retry') }}</el-button>
            </div>
          </el-card>
        </div>
        <template v-else>
        <el-table :data="visibleTracks" row-key="id" class="track-list"
          :empty-text="t('baselineHub.view.empty')" v-bind="trackRows">
          <el-table-column :label="t('baselineHub.view.track')" min-width="210">
            <template #default="{ row }">
              <router-link :to="`/baseline-hub/tracks/${row.slug}`" class="nav-link">
                <b>{{ row.name }}</b>
              </router-link>
              <div v-if="row.status === 'draft'" class="warn draft-note">
                {{ row.benchmark_error || t('baselineHub.noBenchmarkYet') }}
                <el-button v-if="isAdmin" size="small" @click.stop="retryBenchmark(row)">
                  {{ t('baselineHub.retry') }}</el-button>
              </div>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.model')" min-width="180">
            <template #default="{ row }">
              {{ row.model_name }}
              <div v-if="showPrecision(row)" class="muted">{{ row.precision }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="gpu_type" :label="t('baselineHub.view.gpu')" min-width="110" />
          <el-table-column :label="t('common.status')" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="trackStatusType(row.status)" effect="plain">
                {{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.view.progress')" min-width="140">
            <template #default="{ row }">
              <div>{{ t(row.entry_count === 1 ? 'baselineHub.entryOne' : 'baselineHub.entryMany',
                { n: row.entry_count }) }}</div>
              <div class="muted">{{ t('baselineHub.measuredCount', { n: row.measured_count }) }}</div>

            </template>
          </el-table-column>
          <el-table-column :label="t('baselineHub.leading')" min-width="220">
            <template #default="{ row }">
              <template v-if="row.leader">
                <b>{{ row.leader.name }}</b>
                <span v-if="row.leader.version > 1" class="muted"> v{{ row.leader.version }}</span>
                <div class="mono">{{ leaderValue(row) }}</div>
                <div class="muted">{{ leaderCaption(row) }}</div>
              </template>
              <span v-else class="muted">{{ t('baselineHub.noRanked') }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="mobile-track-list">
          <article v-for="item in visibleTracks" :key="item.id" class="mobile-track">
            <router-link :to="`/baseline-hub/tracks/${item.slug}`" class="nav-link"><b>{{ item.name }}</b></router-link>
            <div class="muted">{{ item.model_name }} · {{ item.gpu_type }}</div>
            <div v-if="item.leader" class="mobile-leading"><strong>{{ leaderValue(item) }}</strong><span class="muted">{{ leaderCaption(item) }}</span></div>
            <p v-else class="muted">{{ t('baselineHub.noRanked') }}</p>
            <div class="mobile-track-footer"><span class="muted">{{ t('baselineHub.measuredCount', { n: item.measured_count }) }}</span><el-tag v-if="item.status !== 'open'" :type="trackStatusType(item.status)" effect="plain">{{ item.status }}</el-tag></div>
            <p v-if="item.status === 'draft'" class="warn">{{ item.benchmark_error || t('baselineHub.noBenchmarkYet') }}</p>
          </article>
          <p v-if="!visibleTracks.length" class="muted">{{ t('baselineHub.view.empty') }}</p>
        </div>
        </template>
      </el-tab-pane>

      <!-- Tabs other plugins add to the Hub (pricing: the revenue board). -->
      <PluginSlot name="hub.tabs" :props="{ isAdmin }" />

    </el-tabs>

    <el-drawer v-model="legacyOpen" :title="t('baselineHub.legacyTitle')" size="72%"
      direction="rtl">
      <div class="legacy-head">
        <p class="muted legacy-note">{{ t('baselineHub.legacyNote') }}</p>
        <el-button v-if="isAdmin" plain @click="openFreeForm">
          {{ t('baselineHub.freeForm') }}</el-button>
      </div>
      <el-table :data="legacy" style="cursor: pointer" v-loading="first.loading"
        :empty-text="first.emptyText(t('baselineHub.nothingOutside'))"
        v-bind="legacyRows">
        <el-table-column prop="id" :label="t('common.id')" width="60" />
        <el-table-column :label="t('baselineHub.entry')" min-width="180">
          <template #default="{ row }">
            <b>{{ row.name }}</b>
            <el-tag v-if="row.version > 1" size="small" effect="plain" class="version">
              v{{ row.version }}
            </el-tag>
            <div class="muted tiny">{{ row.notes }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.model')" min-width="130">
          <template #default="{ row }">
            {{ row.served_model_name }}
            <span class="muted tiny">{{ row.engine }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('baselineHub.benchmark')" min-width="130">
          <template #default="{ row }">
            <span class="mono tiny-mono">{{ row.benchmark_slug || '(platform default)' }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('baselineHub.cards')" width="90">
          <template #default="{ row }">{{ hardwareAsk(row) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="110">
          <template #default="{ row }">
            <el-tooltip v-if="row.status === 'queued' && (row.queued_reason || row.queue_ahead?.length)"
              :content="queueTooltip(row)" placement="top">
              <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
            </el-tooltip>
            <el-tag v-else :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
            <div v-if="row.status === 'running' && row.run_status" class="muted tiny">
              {{ row.run_status }}
            </div>
            <div v-else-if="row.status === 'queued'" class="muted tiny">{{ inLine(row) }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('baselineHub.result')" width="110" align="right">
          <template #default="{ row }">
            <span class="mono">{{ latestValue(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('baselineHub.submitted')" width="140">
          <template #default="{ row }">
            <span :title="exactTime(row.created_at)">{{ relativeTime(row.created_at) }}</span>
            <div class="muted tiny">{{ row.created_by_name }}</div>
          </template>
        </el-table-column>
        <el-table-column label="" width="170">
          <template #default="{ row }">
            <el-button v-if="ownsSubmission(row) && ['queued', 'running'].includes(row.status)" size="small"
              type="danger" plain @click.stop="abort(row)">{{ t('baselineHub.abort') }}</el-button>
            <el-button v-if="isAdmin" size="small" @click.stop="openResubmit(row)">
              {{ t('baselineHub.evolve') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>


    <BaselineSubmitDialog v-model="submitOpen" :track="null" :resubmit-of="resubmitOf"
      :gpu-types="gpuTypes" :flag-catalog="flagCatalog"
      @submitted="(id: number) => { load(); router.push(`/baseline-hub/${id}`) }" />
    <BaselineTrackForm v-model="trackFormOpen" :gpu-types="gpuTypes"
      @created="(t: BaselineTrack) => { load(); router.push(`/baseline-hub/tracks/${t.slug}`) }" />
  </div>
</template>

<style scoped>
.hub-index-tabs > :deep(.el-tabs__header:not(:has(.el-tabs__item ~ .el-tabs__item))) { display: none; }

.mobile-track-list { display: none; }
@media (max-width: 720px) {
  .track-list { display: none; }
  .mobile-track-list { display: grid; gap: 12px; }
  .mobile-track { padding: 18px; border: 1px solid var(--autotune-border); border-radius: 8px; background: white; overflow-wrap: anywhere; }
  .mobile-leading { display: grid; gap: 2px; margin: 16px 0 12px; }
  .mobile-leading strong { font-size: 24px; font-variant-numeric: tabular-nums; }
  .mobile-track-footer { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
  .track-toolbar { flex-wrap: wrap; }
  .track-search { flex-basis: 100%; max-width: none; }
}

.track-search { max-width: 360px; margin-right: auto; }
.track-toolbar { gap: 16px; }
.track-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 14px;
}
.track-list {
  width: 100%;
  cursor: pointer;
}
.track-list .draft-note {
  font-size: 12px;
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 18px;
}
.actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: none;
}
.count {
  margin-left: 4px;
  color: var(--autotune-muted, #909399);
  font-size: 13px;
}
.track-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr));
  gap: 14px;
}
.track-card {
  cursor: pointer;
}
.track-card.retired {
  opacity: 0.7;
}
.track-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
}
.track-name {
  font-size: 16px;
}
.gate {
  display: inline-block;
  margin: 10px 0 8px;
  padding: 3px 8px;
  border-radius: 4px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  color: var(--el-color-primary);
  font-weight: 600;
  font-size: 12.5px;
}
.stats {
  display: flex;
  gap: 14px;
  margin-left: 0;
}
.leader {
  margin-top: 8px;
  font-size: 13.5px;
}
.leader .tiny {
  margin-left: 4px;
}
.draft-note {
  margin-top: 8px;
  margin-left: 0;
}
.legacy-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 12px;
}
.legacy-button {
  color: var(--autotune-muted, #909399);
}
.legacy-button .count {
  margin-left: 4px;
}
.legacy-note {
  max-width: 640px;
  margin: 0;
  font-size: 13px;
}
.warn {
  color: var(--el-color-warning);
}
.tiny {
  font-size: 13px;
  margin-left: 6px;
}
.tiny-mono {
  font-size: 12px;
}
.version {
  margin-left: 6px;
}
.empty {
  margin: 8px 0 20px;
}
</style>

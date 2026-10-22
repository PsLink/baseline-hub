<script setup lang="ts">
/** One saved report, rendered by the same renderer the HTML export carries
 *  (src/report/): the agent's markdown with its chart/table/command blocks
 *  drawn live from the comparison frozen with the report. Older reports with
 *  PNG assets render too — their images are fetched through the API so the
 *  session's credentials apply. */
import { ElMessage } from 'element-plus'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type AgentReportDetail } from '../api/client'
import { useI18n } from '../i18n'
import type { Comparison } from '../report/data'
import { renderReport } from '../report/render'
import { exactTime } from '../utils/time'
import { apiErrorText } from '../utils/apiError'

const LANG_NAMES: Record<string, string> = { en: 'English', zh: '中文' }

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const report = ref<AgentReportDetail | null>(null)
const missing = ref(false)
const loadError = ref('')
const exporting = ref(false)
const body = ref<HTMLElement | null>(null)
let assetUrls: string[] = []
let dispose: (() => void) | null = null

function cleanup() {
  dispose?.()
  dispose = null
  for (const url of assetUrls) URL.revokeObjectURL(url)
  assetUrls = []
}

async function load() {
  cleanup()
  report.value = null
  missing.value = false
  loadError.value = ''
  const id = route.params.id
  let detail: AgentReportDetail
  try {
    detail = (await api.get(`/agent/v1/reports/${id}`)).data
  } catch (error: any) {
    if (error.response?.status === 404) missing.value = true
    else loadError.value = apiErrorText(error, t('common.loadFailed'))
    return
  }
  // An <img> cannot send the auth header: fetch each asset, hand over blob URLs.
  const urls: Record<string, string> = {}
  await Promise.all(detail.asset_names.map(async (name) => {
    try {
      const { data } = await api.get(`/agent/v1/reports/${id}/assets/${name}`, { responseType: 'blob' })
      urls[name] = URL.createObjectURL(data)
    } catch {
      /* a missing image leaves a broken image, not a broken page */
    }
  }))
  assetUrls = Object.values(urls)
  report.value = detail
  await nextTick()
  if (!body.value) return
  try {
    dispose = renderReport(body.value, {
    markdown: detail.markdown,
    comparison: detail.comparison as unknown as Comparison,
    lang: detail.lang === 'zh' ? 'zh' : 'en',
    labels: detail.labels,
    scenarioLabels: detail.scenario_labels ?? {},
    resolveAsset: (name) => urls[name] ?? name,
    })
  } catch {
    loadError.value = locale.value === 'zh' ? '报告内容无法显示，可重试或导出源文件查看。' : 'The report could not be rendered. Retry or download its source.'
  }
}

const translations = computed(() =>
  (report.value?.translations ?? []).length > 1 ? report.value!.translations : [])

function switchTo(id: number) {
  if (id !== report.value?.id) router.push(`/reports/${id}`)
}

function slug(): string {
  const title = report.value?.title ?? 'report'
  return title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'report'
}

async function save(path: string, name: string): Promise<void> {
  const { data } = await api.get(path, { responseType: 'blob' })
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

const downloading = ref(false)

async function downloadSource() {
  if (!report.value) return
  downloading.value = true
  try {
    await save(`/agent/v1/reports/${report.value.id}/bundle.zip`, `${slug()}-${report.value.id}.zip`)
  } catch {
    ElMessage.error(t('reports.exportFailed'))
  } finally {
    downloading.value = false
  }
}

async function exportHtml() {
  if (!report.value) return
  exporting.value = true
  try {
    await save(`/agent/v1/reports/${report.value.id}/export.html`, `${slug()}-${report.value.id}.html`)
    ElMessage.success(t('reports.exported'))
  } catch {
    ElMessage.error(t('reports.exportFailed'))
  } finally {
    exporting.value = false
  }
}

const generator = computed(() => {
  const g = report.value?.generator ?? {}
  return Object.entries(g).map(([k, v]) => `${k}: ${String(v)}`).join(' · ')
})

watch(() => route.params.id, load, { immediate: true })
onBeforeUnmount(cleanup)
</script>

<template>
  <div class="page report-page">
    <div class="header-row">
      <div>
        <router-link to="/reports" class="muted tiny">← {{ t('reports.back') }}</router-link>

      </div>
      <div v-if="report" class="actions">
        <el-radio-group v-if="translations.length" :model-value="report.id" size="small"
          :aria-label="t('reports.language')" @change="(v: string | number | boolean | undefined) => switchTo(Number(v))">
          <el-radio-button v-for="tr in translations" :key="tr.id" :value="tr.id">
            {{ LANG_NAMES[tr.lang] ?? tr.lang }}
          </el-radio-button>
        </el-radio-group>
        <el-dropdown trigger="click" @command="(command: string) => command === 'html' ? exportHtml() : downloadSource()">
          <el-button type="primary" :loading="exporting || downloading" :disabled="exporting || downloading">{{ locale === 'zh' ? '导出报告' : 'Export report' }} ▾</el-button>
          <template #dropdown><el-dropdown-menu>
            <el-dropdown-item command="html">{{ t('reports.exportHtml') }}</el-dropdown-item>
            <el-dropdown-item command="source">{{ t('reports.download') }}</el-dropdown-item>
          </el-dropdown-menu></template>
        </el-dropdown>
      </div>
    </div>

    <el-alert v-if="report && !report.comparable" type="warning" :closable="false" show-icon
      :title="t('reports.notComparableHint')" class="warn" />

    <details v-if="report" class="report-source">
      <summary>{{ locale === 'zh' ? '报告来源与运行记录' : 'Report sources and runs' }}</summary>
        <span v-if="report" class="muted tiny meta">
          <template v-if="report.campaign_id">
            {{ t('reports.campaign') }}:
            <span class="mono">
              #{{ report.campaign_id }}
            </span>
            ·
          </template>
          <template v-if="report.group">
            {{ t('reports.group') }}:
            <router-link v-if="report.group.page_path" :to="report.group.page_path">
              {{ report.group.name || report.group.slug }}
            </router-link>
            <span v-else>{{ report.group.name || report.group.slug }}</span>
            ·
          </template>
          {{ t('reports.inputs') }}:
          <span class="mono">run {{ report.baseline_run_id }}</span>
          →
          <span class="mono">{{ report.attempt_run_ids.map((id) => `run ${id}`).join(', ') }}</span>
          <template v-if="report.created_by_name"> · {{ t('reports.by') }} {{ report.created_by_name }}</template>
          <template v-if="report.created_at"> · {{ exactTime(report.created_at) }}</template>
          <template v-if="generator"> · {{ t('reports.generator') }} {{ generator }}</template>
          <el-tag v-if="!report.comparable" size="small" type="warning" class="tag"
            :title="t('reports.notComparableHint')">
            {{ t('reports.notComparable') }}
          </el-tag>
        </span>
    </details>

    <el-alert v-if="loadError" type="error" :closable="false" :title="loadError" show-icon class="warn"><el-button @click="load">{{ t('common.retry') }}</el-button></el-alert>
    <p v-if="missing" class="muted">{{ t('reports.missing') }}</p>
    <p v-else-if="!report && !loadError" class="muted">{{ t('reports.loading') }}</p>
    <div v-show="report && !loadError" ref="body" class="report-body" />
  </div>
</template>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 12px;
}
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.report-page { max-width: 1040px; }
.meta { display: block; margin-top: 4px; }
.tag { margin-left: 8px; }
.warn { margin: 12px 0; }
.report-body { background: #fff; border: 1px solid var(--autotune-border); border-radius: 8px; padding: 24px 40px 48px; }
.report-body :deep(.ar) { max-width: 800px; --ar-accent: var(--el-color-primary); }
.report-source { margin: 0 0 20px; color: var(--autotune-muted); font-size: 13px; }
.report-source summary { cursor: pointer; }
.report-source .meta { padding: 12px 0; line-height: 1.8; overflow-wrap: anywhere; }
@media (max-width: 720px) { .report-body { padding: 16px 18px 32px; } }
</style>

<script setup lang="ts">
/** Reports being written, above the Reports page's list (`reports.sections`):
 *  each job's step while it runs, what went wrong when it failed, and its
 *  reports once saved. A row opens every step the job took — the prompts as
 *  sent, the answers as received, and what the checks found. */
import { computed, onMounted, ref } from 'vue'
import { api, apiErrorText, relativeTime, useI18n, usePoll } from '@/plugins/api'
import { ElMessage } from 'element-plus'
import { jobs, running } from '../store'
import type { Job } from '../types'

const props = defineProps<{ reload?: () => unknown }>()
const { t } = useI18n()
const expanded = ref(false)
const shown = computed(() => (expanded.value ? jobs.list : jobs.list.slice(0, 3)))

let wasRunning = new Set<number>()
async function refresh() {
  await jobs.refresh()
  const now = new Set(jobs.list.filter(running).map((j) => j.id))
  // A job that finished since the last look saved reports: show them in the list.
  if ([...wasRunning].some((id) => !now.has(id))) await props.reload?.()
  wasRunning = now
  if (jobs.open && running(jobs.open)) await jobs.show(jobs.open.id)
}
const poll = usePoll(refresh, 4000)
onMounted(() => {
  poll.start(true)
})

const drawer = computed({
  get: () => !!jobs.open,
  set: (v: boolean) => { if (!v) jobs.open = null },
})

async function again(job: Job) {
  try {
    const next: Job = (await api.post(`/autoreport/jobs/${job.id}/retry`)).data
    await jobs.refresh()
    await jobs.show(next.id)
  } catch (e) {
    ElMessage.error(apiErrorText(e, t('autoreport.couldNotStart')))
  }
}

function statusType(s: string) {
  return s === 'done' ? 'success' : s === 'failed' ? 'danger' : s === 'running' ? 'warning' : 'info'
}
function what(j: Job) {
  const runs = j.baseline_run_id ? `${t('autoreport.runs')} ${j.baseline_run_id} → ${j.attempt_run_ids.join(', ')}` : ''
  const from = j.source?.kind === 'track' ? j.source.slug : ''
  return [from, runs].filter(Boolean).join(' · ')
}
function tokens(j: Job) {
  const u = j.usage || {}
  const total = u.total_tokens ?? (u.prompt_tokens ?? 0) + (u.completion_tokens ?? 0)
  return total ? total.toLocaleString('en-US') : ''
}
function pretty(v: unknown) {
  return typeof v === 'string' ? v : JSON.stringify(v, null, 2)
}
</script>

<template>
  <div v-if="jobs.list.length" class="jobs">
    <div class="head">
      <h3>{{ t('autoreport.jobs') }}</h3>
      <el-button v-if="jobs.list.length > 3" link size="small" @click="expanded = !expanded">
        {{ expanded ? t('autoreport.fewer') : t('autoreport.all', { n: jobs.list.length }) }}
      </el-button>
    </div>
    <div v-for="j in shown" :key="j.id" class="job" @click="jobs.show(j.id)">
      <el-tag :type="statusType(j.status)" size="small">{{ t(`autoreport.status_${j.status}`) }}</el-tag>
      <span class="what">{{ what(j) }}</span>
      <span v-if="j.status === 'failed'" class="err">{{ j.error }}</span>
      <span v-else-if="running(j)" class="muted">{{ j.step }}</span>
      <template v-else>
        <router-link v-for="(id, lang) in j.report_ids" :key="lang" :to="`/reports/${id}`" class="rep" @click.stop>
          {{ t('autoreport.openReport', { lang: String(lang).toUpperCase() }) }}
        </router-link>
      </template>
      <span class="when muted">{{ relativeTime(j.created_at) }} · {{ j.created_by_name }}</span>
    </div>
  </div>

  <el-drawer v-model="drawer" size="62%" :title="jobs.open ? `${t('autoreport.job')} #${jobs.open.id}` : ''">
    <template v-if="jobs.open">
      <p class="summary">
        <el-tag :type="statusType(jobs.open.status)">{{ t(`autoreport.status_${jobs.open.status}`) }}</el-tag>
        <span class="muted"> {{ what(jobs.open) }}</span>
        <span v-if="jobs.open.llm?.model" class="muted"> · {{ jobs.open.llm.model }}</span>
        <span v-if="tokens(jobs.open)" class="muted"> · {{ tokens(jobs.open) }} tokens</span>
      </p>
      <el-alert v-if="jobs.open.error" type="error" :closable="false" :title="jobs.open.error" />
      <p v-if="Object.keys(jobs.open.report_ids).length">
        <router-link v-for="(id, lang) in jobs.open.report_ids" :key="lang" :to="`/reports/${id}`" class="rep">
          {{ t('autoreport.openReport', { lang: String(lang).toUpperCase() }) }}
        </router-link>
      </p>
      <p>
        <el-button v-if="!running(jobs.open)" size="small" @click="again(jobs.open)">{{ t('autoreport.again') }}</el-button>
      </p>
      <el-collapse>
        <el-collapse-item v-for="(e, i) in jobs.open.log" :key="i" :name="i">
          <template #title>
            <span class="logtitle">
              {{ e.at?.slice(11) }} · <b>{{ t(`autoreport.step_${e.step}`) }}</b>
              <template v-if="e.lang"> · {{ String(e.lang).toUpperCase() }}</template>
              <template v-if="e.round !== undefined && e.step === 'answer'"> · {{ t('autoreport.round', { n: Number(e.round) + 1 }) }}</template>
              <template v-if="e.seconds !== undefined"> · {{ e.seconds }} s</template>
              <el-tag v-if="e.problems?.length" type="danger" size="small">{{ t('autoreport.problems', { n: e.problems.length }) }}</el-tag>
              <el-tag v-else-if="e.step === 'answer'" type="success" size="small">{{ t('autoreport.passed') }}</el-tag>
            </span>
          </template>
          <ul v-if="e.problems?.length" class="problems"><li v-for="p in e.problems" :key="p">{{ p }}</li></ul>
          <template v-if="e.messages">
            <div v-for="(m, k) in e.messages" :key="k">
              <div class="role">{{ m.role }}</div>
              <pre>{{ m.content }}</pre>
            </div>
          </template>
          <pre v-if="e.text">{{ e.text }}</pre>
          <pre v-if="e.outline">{{ e.outline }}</pre>
          <details v-if="e.facts"><summary>facts</summary><pre>{{ pretty(e.facts) }}</pre></details>
          <pre v-if="e.why">{{ e.why }}</pre>
        </el-collapse-item>
      </el-collapse>
    </template>
  </el-drawer>
</template>

<style scoped>
.jobs {
  margin-bottom: 16px;
  padding: 10px 14px;
  border: 1px solid var(--autotune-border);
  border-radius: 8px;
  background: var(--el-bg-color);
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
h3 {
  margin: 0 0 6px;
  font-size: 13px;
}
.job {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  font-size: 13px;
  cursor: pointer;
  border-top: 1px solid var(--autotune-border);
}
.what {
  white-space: nowrap;
}
.err {
  color: var(--el-color-danger);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.when {
  margin-left: auto;
  white-space: nowrap;
  font-size: 12px;
}
.rep {
  margin-right: 6px;
}
.summary {
  margin-top: 0;
}
.role {
  font-size: 12px;
  font-weight: 600;
  margin-top: 8px;
  color: var(--autotune-muted);
}
pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  background: var(--el-fill-color-light);
  padding: 10px;
  border-radius: 6px;
  max-height: 520px;
  overflow: auto;
}
.problems {
  color: var(--el-color-danger);
  font-size: 13px;
}
.logtitle {
  display: flex;
  gap: 6px;
  align-items: center;
}
</style>

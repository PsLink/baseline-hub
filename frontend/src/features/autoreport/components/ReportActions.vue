<script setup lang="ts">
/** The Reports page's header buttons: write a report, and (for an admin) the
 *  model reports are written with. Filled into the page's `reports.actions`
 *  slot. */
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, apiErrorText, useAuthStore, useI18n } from '@/plugins/api'
import { jobs } from '../store'
import type { Job, LLMSettings, Preview, TrackRow } from '../types'

defineProps<{ reload?: () => unknown }>()
const { t } = useI18n()
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')

// -- write a report ---------------------------------------------------------------
const writeOpen = ref(false)
const tracks = ref<TrackRow[]>([])
const form = reactive({
  source: 'track' as 'track' | 'runs',
  track: '',
  baseline: '',
  attempts: '',
  labels: '',
  langs: ['en', 'zh'] as string[],
})
const busy = ref(false)

async function openWrite() {
  writeOpen.value = true
  if (!tracks.value.length) {
    tracks.value = (await api.get('/agent/v1/tracks').catch(() => ({ data: [] }))).data
    if (!tracks.value.length) form.source = 'runs'
  }
}
function body() {
  const ids = (s: string) => s.split(/[\s,]+/).filter(Boolean).map(Number)
  const labels = form.labels.split(',').map((s) => s.trim()).filter(Boolean)
  return form.source === 'track'
    ? { track: form.track, labels, langs: form.langs }
    : { baseline_run_id: Number(form.baseline), attempt_run_ids: ids(form.attempts), labels, langs: form.langs }
}
const ready = computed(() =>
  form.langs.length > 0 && (form.source === 'track' ? !!form.track : !!form.baseline && !!form.attempts))

async function start() {
  busy.value = true
  try {
    const job: Job = (await api.post('/autoreport/jobs', body())).data
    writeOpen.value = false
    await jobs.refresh()
    await jobs.show(job.id)
  } catch (e) {
    ElMessage.error(apiErrorText(e, t('autoreport.couldNotStart')))
  } finally {
    busy.value = false
  }
}

const preview = ref<Preview | null>(null)
const previewOpen = ref(false)
async function showPreview() {
  busy.value = true
  try {
    preview.value = (await api.post('/autoreport/preview', body())).data
    previewOpen.value = true
  } catch (e) {
    ElMessage.error(apiErrorText(e, t('autoreport.couldNotPreview')))
  } finally {
    busy.value = false
  }
}

// -- the model (admin) ---------------------------------------------------------------
const settingsOpen = ref(false)
const settings = ref<LLMSettings | null>(null)
const sf = reactive({
  base_url: '', model: '', api_key: '', temperature: '' as string, max_tokens: 64000,
  json_mode: true, timeout_seconds: 900, fix_rounds: 3, extra_body: '{}',
})
const testing = ref(false)

async function openSettings() {
  const s: LLMSettings = (await api.get('/autoreport/settings')).data
  settings.value = s
  Object.assign(sf, {
    base_url: s.base_url, model: s.model, api_key: '',
    temperature: s.temperature == null ? '' : String(s.temperature),
    max_tokens: s.max_tokens, json_mode: s.json_mode, timeout_seconds: s.timeout_seconds,
    fix_rounds: s.fix_rounds, extra_body: JSON.stringify(s.extra_body ?? {}),
  })
  settingsOpen.value = true
}
function payload(apiKey?: string): Record<string, unknown> | null {
  let extra: Record<string, unknown>
  try {
    extra = JSON.parse(sf.extra_body || '{}')
  } catch {
    ElMessage.error(t('autoreport.extraBodyInvalid'))
    return null
  }
  const temp = sf.temperature.trim()
  if (temp && Number.isNaN(Number(temp))) {
    ElMessage.error(t('autoreport.temperatureInvalid'))
    return null
  }
  const out: Record<string, unknown> = {
    base_url: sf.base_url.trim(), model: sf.model.trim(),
    temperature: temp ? Number(temp) : null, max_tokens: sf.max_tokens, json_mode: sf.json_mode,
    timeout_seconds: sf.timeout_seconds, fix_rounds: sf.fix_rounds, extra_body: extra,
  }
  if (apiKey !== undefined) out.api_key = apiKey
  return out
}
async function saveSettings(apiKey?: string) {
  const p = payload(apiKey ?? (sf.api_key ? sf.api_key : undefined))
  if (!p) return false
  try {
    settings.value = (await api.put('/autoreport/settings', p)).data
    sf.api_key = ''
    ElMessage.success(t('autoreport.saved'))
    return true
  } catch (e) {
    ElMessage.error(apiErrorText(e, t('autoreport.couldNotSave')))
    return false
  }
}
async function saveAndTest() {
  if (!(await saveSettings())) return
  testing.value = true
  try {
    const r = (await api.post('/autoreport/settings/test')).data
    if (r.ok) ElMessage.success(t('autoreport.testOk', { model: r.model, s: r.seconds }))
    else ElMessage.error({ message: r.error, duration: 10000 })
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <el-button v-if="isAdmin" @click="openSettings">{{ t('autoreport.settings') }}</el-button>
  <el-button type="primary" @click="openWrite">{{ t('autoreport.write') }}</el-button>

  <el-dialog v-model="writeOpen" :title="t('autoreport.writeTitle')" width="620px">
    <p class="muted small">{{ t('autoreport.about') }}</p>
    <el-form label-width="110px">
      <el-form-item :label="t('autoreport.from')">
        <el-radio-group v-model="form.source">
          <el-radio-button value="track" :disabled="!tracks.length">{{ t('autoreport.fromTrack') }}</el-radio-button>
          <el-radio-button value="runs">{{ t('autoreport.fromRuns') }}</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.source === 'track'" :label="t('autoreport.track')">
        <el-select v-model="form.track" filterable :placeholder="t('autoreport.pickTrack')" style="width: 100%">
          <el-option v-for="tr in tracks" :key="tr.slug" :value="tr.slug"
            :label="`${tr.name} (${tr.measured_count}/${tr.run_count})`" />
        </el-select>
        <div class="muted small">{{ t('autoreport.trackHint') }}</div>
      </el-form-item>
      <template v-else>
        <el-form-item :label="t('autoreport.baseline')">
          <el-input v-model="form.baseline" style="width: 160px" :placeholder="t('autoreport.runId')" />
        </el-form-item>
        <el-form-item :label="t('autoreport.attempts')">
          <el-input v-model="form.attempts" style="width: 240px" placeholder="102, 103" />
          <div class="muted small">{{ t('autoreport.attemptsHint') }}</div>
        </el-form-item>
      </template>
      <el-form-item :label="t('autoreport.labels')">
        <el-input v-model="form.labels" :placeholder="t('autoreport.labelsPlaceholder')" />
      </el-form-item>
      <el-form-item :label="t('autoreport.languages')">
        <el-checkbox-group v-model="form.langs">
          <el-checkbox value="en">English</el-checkbox>
          <el-checkbox value="zh">中文</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="!ready" :loading="busy" @click="showPreview">{{ t('autoreport.preview') }}</el-button>
      <el-button type="primary" :disabled="!ready" :loading="busy" @click="start">{{ t('autoreport.start') }}</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="previewOpen" :title="t('autoreport.previewTitle')" width="80%" top="4vh">
    <template v-if="preview">
      <p class="muted small">{{ preview.baseline_run_id }} → {{ preview.attempt_run_ids.join(', ') }}
        <span v-if="preview.why"> · {{ preview.why }}</span></p>
      <el-alert v-if="preview.shape.unsupported?.length" type="error" :closable="false"
        :title="preview.shape.unsupported.join('; ')" />
      <el-tabs>
        <el-tab-pane v-for="l in preview.languages" :key="l.lang" :label="l.lang">
          <div v-for="(m, k) in l.messages" :key="k">
            <div class="role">{{ m.role }}</div>
            <pre>{{ m.content }}</pre>
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>
  </el-dialog>

  <el-dialog v-model="settingsOpen" :title="t('autoreport.settings')" width="640px">
    <p class="muted small">{{ t('autoreport.settingsAbout') }}</p>
    <el-form label-width="150px">
      <el-form-item :label="t('autoreport.baseUrl')">
        <el-input v-model="sf.base_url" :placeholder="settings?.env_base_url || 'https://…/v1'" />
        <div class="muted small">{{ t('autoreport.baseUrlHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('autoreport.modelName')">
        <el-input v-model="sf.model" :placeholder="settings?.env_model" />
      </el-form-item>
      <el-form-item :label="t('autoreport.apiKey')">
        <el-input v-model="sf.api_key" type="password" show-password autocomplete="new-password"
          :placeholder="settings?.has_api_key ? t('autoreport.keyStored') : (settings?.env_has_api_key ? t('autoreport.keyEnv') : '')" />
        <el-button v-if="settings?.has_api_key" link size="small" @click="saveSettings('')">
          {{ t('autoreport.clearKey') }}
        </el-button>
      </el-form-item>
      <el-form-item :label="t('autoreport.maxTokens')">
        <el-input-number v-model="sf.max_tokens" :min="0" :step="8000" />
        <div class="muted small">{{ t('autoreport.maxTokensHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('autoreport.temperature')">
        <el-input v-model="sf.temperature" style="width: 160px" :placeholder="t('autoreport.modelDefault')" />
        <div class="muted small">{{ t('autoreport.temperatureHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('autoreport.jsonMode')">
        <el-switch v-model="sf.json_mode" />
        <div class="muted small">{{ t('autoreport.jsonModeHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('autoreport.timeout')">
        <el-input-number v-model="sf.timeout_seconds" :min="10" :step="60" />
      </el-form-item>
      <el-form-item :label="t('autoreport.fixRounds')">
        <el-input-number v-model="sf.fix_rounds" :min="0" :max="10" />
      </el-form-item>
      <el-form-item :label="t('autoreport.extraBody')">
        <el-input v-model="sf.extra_body" type="textarea" :rows="2" placeholder='{"reasoning_effort": "high"}' />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :loading="testing" @click="saveAndTest">{{ t('autoreport.saveTest') }}</el-button>
      <el-button type="primary" @click="saveSettings().then((ok) => ok && (settingsOpen = false))">
        {{ t('autoreport.save') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.small {
  font-size: 12px;
  line-height: 1.5;
  width: 100%;
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
</style>

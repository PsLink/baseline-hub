<script setup lang="ts">
/** Admin: open a track. Everything an entrant used to type is fixed here —
 *  the model side, the card type, the verified images, the gate — and the
 *  benchmark behind it is created on LLMBench from a template (or an
 *  existing benchmark is bound read-only, for the hand-made ones that
 *  predate tracks). */
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { api } from '@/plugins/api'
import { type BaselineTrack, type MachineWeights, type TrackTemplate } from '../types'
import { useI18n } from '@/plugins/api'
import { apiErrorText } from '@/plugins/api'
import { InfoHint } from '@/plugins/api'

const props = defineProps<{ modelValue: boolean; gpuTypes: string[] }>()
const emit = defineEmits<{
  (e: 'update:modelValue', open: boolean): void
  (e: 'created', track: BaselineTrack): void
}>()
const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const { t } = useI18n()
const templates = ref<TrackTemplate[]>([])
const busy = ref(false)
const slugTouched = ref(false)
const servedTouched = ref(false)
const modelTouched = ref(false)
const nameTouched = ref(false)

type ImageRow = { engine: string; image: string; label: string }
const empty = () => ({
  name: '',
  slug: '',
  model_name: '',
  served_model_name: '',
  model_path: '',
  precision: '',
  gpu_type: '',
  images: [{ engine: 'sglang', image: '', label: '' }] as ImageRow[],
  ttft_ms: 8000,
  ttft_percentile: 'p50',
  min_request_output_tps: 15,
  mode: 'template' as 'template' | 'bind',
  template: 'sweeps-quality-v1',
  bind_benchmark_slug: '',
  ranking_metric: 'perf_guidellm_sweep.total_tpm_card_norm',
  max_run_minutes: 900,
  cookbook_url: '',
  contest_benchmark_id: undefined as number | undefined,
  notes: '',
})
const form = ref(empty())

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63)
}
watch(
  () => form.value.name,
  (name) => {
    if (!slugTouched.value) form.value.slug = slugify(name)
  },
)
watch(
  () => form.value.model_name,
  (name) => {
    if (!servedTouched.value) form.value.served_model_name = name
    if (!nameTouched.value) form.value.name = trackName(name, form.value.gpu_type)
  },
)
watch(
  () => form.value.gpu_type,
  (gpu) => {
    if (!nameTouched.value) form.value.name = trackName(form.value.model_name, gpu)
  },
)
/** The weights directory names the model, quantization and all: a directory
 *  called Qwen3.8-27B-FP8 holds a different model from Qwen3.8-27B, and a
 *  board that called them the same thing would compare two models. So the
 *  model name is taken from the path verbatim, never trimmed. */
watch(
  () => form.value.model_path,
  (path) => {
    if (modelTouched.value) return
    const leaf = path.trim().replace(/\/+$/, '').split('/').pop() ?? ''
    if (leaf) form.value.model_name = leaf
  },
)
function trackName(model: string, gpu: string): string {
  return [model.trim(), gpu].filter(Boolean).join(' · ')
}
watch(
  () => props.modelValue,
  async (isOpen) => {
    if (!isOpen) return
    form.value = empty()
    slugTouched.value = false
    servedTouched.value = false
    modelTouched.value = false
    nameTouched.value = false
    try {
      const [listed, defaults] = await Promise.all([
        api.get('/baseline-hub/track-templates'),
        api.get('/baseline-hub/track-defaults'),
      ])
      templates.value = listed.data
      // The API's own defaults, so a track made here matches one made by a
      // caller that leaves the fields out.
      const d = defaults.data
      Object.assign(form.value, {
        template: d.template,
        ranking_metric: d.ranking_metric,
        max_run_minutes: d.max_run_minutes,
        ttft_ms: d.gate.ttft_ms,
        ttft_percentile: d.gate.ttft_percentile,
        min_request_output_tps: d.gate.min_request_output_tps,
      })
      if (templates.value.length && !templates.value.some((t) => t.name === form.value.template))
        form.value.template = templates.value[0].name
    } catch {
      templates.value = []
    }
  },
)

// Where the weights are, asked of every machine of the card type before the
// track exists. Entries only ever run where they are; this says where that is.
const weightsFound = ref<MachineWeights[] | null>(null)
const weightsBusy = ref(false)
watch(() => [form.value.model_path, form.value.gpu_type], () => (weightsFound.value = null))
async function checkWeights() {
  const f = form.value
  weightsBusy.value = true
  try {
    weightsFound.value = (await api.post('/baseline-hub/weights-check', {
      model_path: f.model_path.trim(),
      gpu_type: f.gpu_type,
      image: f.images.find((i) => i.image.trim())?.image.trim() ?? '',
    })).data
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.weights.recheckFailed')))
  } finally {
    weightsBusy.value = false
  }
}
const weightsOn = computed(() =>
  (weightsFound.value ?? []).filter((w) => w.state === 'present').map((w) => w.machine))

const chosenTemplate = computed(() => templates.value.find((t) => t.name === form.value.template))

function tokens(n: number | null): string {
  if (n == null) return '?'
  if (n >= 1000) {
    const k = n / 1000
    return `${Number.isInteger(k) ? k.toFixed(0) : k.toFixed(1)}k`
  }
  return String(n)
}

const problem = computed(() => {
  const f = form.value
  const a = 'baselineHub.admin.newTrack.'
  if (!f.name.trim()) return t(a + 'problemName')
  if (!/^[a-z0-9][a-z0-9-]{1,62}$/.test(f.slug)) return t(a + 'problemSlug')
  if (!f.model_name.trim() || !f.served_model_name.trim() || !f.model_path.trim())
    return t(a + 'problemModel')
  if (!f.gpu_type) return t(a + 'problemCard')
  if (!f.images.some((i) => i.image.trim())) return t(a + 'problemImage')
  if (f.mode === 'bind' && !f.bind_benchmark_slug.trim()) return t(a + 'problemBind')
  if (f.ttft_ms <= 0) return t(a + 'problemTtft')
  return ''
})

async function create() {
  if (problem.value) return
  const f = form.value
  busy.value = true
  try {
    const { data } = await api.post('/baseline-hub/tracks', {
      slug: f.slug,
      name: f.name.trim(),
      model_name: f.model_name.trim(),
      served_model_name: f.served_model_name.trim(),
      model_path: f.model_path.trim(),
      precision: f.precision.trim(),
      gpu_type: f.gpu_type,
      images: f.images
        .filter((i) => i.image.trim())
        .map((i) => ({ engine: i.engine, image: i.image.trim(), label: i.label.trim() })),
      gate: {
        ttft_ms: f.ttft_ms,
        ttft_percentile: f.ttft_percentile,
        min_request_output_tps: f.min_request_output_tps,
      },
      template: f.mode === 'template' ? f.template : '',
      bind_benchmark_slug: f.mode === 'bind' ? f.bind_benchmark_slug.trim() : '',
      ranking_metric: f.ranking_metric.trim(),
      max_run_minutes: f.max_run_minutes,
      cookbook_url: f.cookbook_url.trim(),
      contest_benchmark_id: f.contest_benchmark_id || null,
      notes: f.notes,
    })
    open.value = false
    if (data.status === 'draft')
      ElMessage.warning(`Track saved as a draft — ${data.benchmark_error || 'benchmark not created'}`)
    else ElMessage.success(`Track open · benchmark ${data.benchmark_slug}`)
    emit('created', data)
  } catch (error: any) {
    ElMessage.error(apiErrorText(error, t('baselineHub.msg.trackCreateFailed')))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-dialog v-model="open" :title="t('baselineHub.admin.newTrack.title')" width="820px"
    top="4vh">
    <el-form label-position="top">
      <div class="grid-2">
        <el-form-item :label="t('baselineHub.admin.newTrack.trackName')">
          <el-input v-model="form.name" placeholder="Qwen3.8-27B-FP8 · A100"
            @input="nameTouched = true" />
        </el-form-item>
        <el-form-item>
          <template #label>
            <span>{{ t('baselineHub.admin.newTrack.slug') }}</span>
            <InfoHint>{{ t('baselineHub.admin.newTrack.slugHint') }}</InfoHint>
          </template>
          <el-input v-model="form.slug" class="mono" placeholder="qwen3-8-27b-fp8-a100"
            @input="slugTouched = true" />
        </el-form-item>
      </div>

      <div class="section muted tiny">{{ t('baselineHub.admin.newTrack.modelSection') }}</div>
      <div class="grid-2">
        <el-form-item>
          <template #label>
            <span>{{ t('baselineHub.admin.newTrack.modelName') }}</span>
            <InfoHint :width="380">{{ t('baselineHub.admin.newTrack.modelNameHint') }}</InfoHint>
          </template>
          <el-input v-model="form.model_name" placeholder="Qwen3.8-27B-FP8"
            @input="modelTouched = true" />
        </el-form-item>
        <el-form-item :label="t('baselineHub.admin.newTrack.servedName')">
          <el-input v-model="form.served_model_name" class="mono"
            @input="servedTouched = true" />
        </el-form-item>
      </div>
      <div class="grid-2">
        <el-form-item>
          <template #label>
            <span>{{ t('baselineHub.admin.newTrack.weightsPath') }}</span>
          </template>
          <el-input v-model="form.model_path" class="mono"
            placeholder="/mnt/disk0/models/Qwen3.8-27B-FP8" />
          <div class="weights-check">
            <el-button size="small" link type="primary" :loading="weightsBusy"
              :disabled="!form.model_path.trim() || !form.gpu_type" @click="checkWeights">
              {{ t('baselineHub.weights.check') }}</el-button>
            <template v-if="weightsFound">
              <span v-if="weightsOn.length" class="tiny">
                {{ t('baselineHub.weights.found', { machines: weightsOn.join(', ') }) }}</span>
              <span v-else class="tiny warn">{{ t('baselineHub.weights.none') }}</span>
              <div v-for="w in weightsFound.filter((x) => x.state !== 'present')" :key="w.machine_id"
                class="muted tiny">{{ w.machine }}: {{ t(`baselineHub.weights.${w.state}`) }}
                <template v-if="w.nodes[0]?.detail"> · {{ w.nodes[0].detail }}</template></div>
            </template>
          </div>
        </el-form-item>
        <div class="grid-2">
          <el-form-item>
            <template #label>
              <span>{{ t('baselineHub.admin.newTrack.precision') }}</span>
              <InfoHint>{{ t('baselineHub.admin.newTrack.precisionHint') }}</InfoHint>
            </template>
            <el-input v-model="form.precision" placeholder="fp8" />
          </el-form-item>
          <el-form-item :label="t('baselineHub.admin.newTrack.cardType')">
            <el-select v-model="form.gpu_type" :placeholder="t('baselineHub.admin.newTrack.pickOne')"
              style="width: 100%">
              <el-option v-for="t in gpuTypes" :key="t" :label="t" :value="t" />
            </el-select>
          </el-form-item>
        </div>
      </div>

      <div class="section muted tiny">{{ t('baselineHub.admin.newTrack.imagesSection') }}</div>
      <div v-for="(row, i) in form.images" :key="i" class="image-row">
        <el-select v-model="row.engine" style="width: 110px">
          <el-option value="sglang" label="sglang" />
          <el-option value="vllm" label="vllm" />
        </el-select>
        <el-input v-model="row.image" class="mono"
          placeholder="harbor.4pd.io/hardcore-tech/sglang:v0.5.15-cu129" />
        <el-input v-model="row.label" :placeholder="t('baselineHub.admin.newTrack.labelOptional')"
          style="width: 180px" />
        <el-button text class="row-remove" :title="t('baselineHub.submit.remove')"
          @click="form.images.splice(i, 1)">−</el-button>
      </div>
      <el-button size="small" class="add"
        @click="form.images.push({ engine: 'sglang', image: '', label: '' })">
        {{ t('baselineHub.admin.newTrack.addImage') }}
      </el-button>

      <div class="section muted tiny">{{ t('baselineHub.admin.newTrack.gateSection') }}</div>
      <div class="grid-3">
        <el-form-item :label="t('baselineHub.admin.newTrack.ttftCeiling')">
          <el-input-number v-model="form.ttft_ms" :min="1" :step="1000" :controls="false"
            style="width: 100%" />
        </el-form-item>
        <el-form-item :label="t('baselineHub.admin.newTrack.ttftPercentile')">
          <el-select v-model="form.ttft_percentile" style="width: 100%">
            <el-option v-for="p in ['mean', 'p50', 'p90', 'p99']" :key="p" :value="p" :label="p" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('baselineHub.admin.newTrack.minRequestTps')">
          <el-input-number v-model="form.min_request_output_tps" :min="0" :step="1"
            :controls="false" style="width: 100%" />
        </el-form-item>
      </div>

      <div class="section muted tiny">{{ t('baselineHub.admin.newTrack.benchmarkSection') }}</div>
      <el-radio-group v-model="form.mode" class="mode">
        <el-radio value="template">{{ t('baselineHub.admin.newTrack.modeTemplate') }}</el-radio>
        <el-radio value="bind">{{ t('baselineHub.admin.newTrack.modeBind') }}</el-radio>
      </el-radio-group>
      <div v-if="form.mode === 'template'" class="grid-2">
        <el-form-item :label="t('baselineHub.admin.newTrack.template')">
          <el-select v-model="form.template" style="width: 100%">
            <el-option v-for="tp in templates" :key="tp.name" :value="tp.name"
              :label="`${tp.title}${tp.default ? ` (${t('baselineHub.admin.newTrack.recommended')})` : ''}`" />
          </el-select>
        </el-form-item>
        <div v-if="chosenTemplate" class="template-summary muted tiny">
          <div v-for="m in chosenTemplate.modules" :key="m.order_index">
            {{ tokens(m.input_tokens) }} in + {{ tokens(m.output_tokens) }} out ·
            {{ m.search_mode }} search ·
            {{ m.weight > 0 ? t('baselineHub.rules.ranked') : t('baselineHub.rules.shown') }}
            <span v-if="!m.takes_gate" class="warn">· {{ t('baselineHub.rules.notGated') }}</span>
          </div>
          <div v-if="chosenTemplate.description" class="template-desc">{{ chosenTemplate.description }}</div>
          <div>{{ t('baselineHub.admin.benchmark.templateHint') }}</div>
        </div>
      </div>
      <el-form-item v-else>
        <template #label>
          <span>{{ t('baselineHub.admin.newTrack.bindSlug') }}</span>
          <InfoHint>The platform reads it and never locks or edits it. Its own SLO params
            apply, not the gate above — use this for benchmarks made by hand before
            tracks existed.</InfoHint>
        </template>
        <el-input v-model="form.bind_benchmark_slug" class="mono" placeholder="sweep-test" />
      </el-form-item>

      <div class="grid-3">
        <el-form-item>
          <template #label>
            <span>{{ t('baselineHub.admin.newTrack.rankingMetric') }}</span>
            <InfoHint :width="360">module.metric read off the frozen metrics. The default is
              per-machine total TPM at 8 cards of the first scenario.</InfoHint>
          </template>
          <el-input v-model="form.ranking_metric" class="mono" />
        </el-form-item>
        <el-form-item :label="t('baselineHub.admin.newTrack.budget')">
          <el-input-number v-model="form.max_run_minutes" :min="5" :max="1440"
            style="width: 100%" />
        </el-form-item>
        <el-form-item :label="t('baselineHub.admin.newTrack.cookbook')">
          <el-input v-model="form.cookbook_url" placeholder="https://…" />
        </el-form-item>
      </div>
      <el-form-item>
        <template #label>
          <span>{{ t('baselineHub.admin.newTrack.contestBoard') }}</span>
          <InfoHint :width="380">{{ t('baselineHub.admin.newTrack.contestBoardHint') }}</InfoHint>
        </template>
        <el-input-number v-model="form.contest_benchmark_id" :min="1" :controls="false"
          :placeholder="t('baselineHub.admin.newTrack.contestBoardNone')" style="width: 220px" />
      </el-form-item>
      <el-form-item :label="t('baselineHub.admin.newTrack.notes')">
        <el-input v-model="form.notes" />
      </el-form-item>
    </el-form>

    <template #footer>
      <span v-if="problem" class="muted tiny problem">{{ problem }}</span>
      <el-button @click="open = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="busy" :disabled="!!problem" @click="create">
        {{ t('baselineHub.admin.newTrack.create') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.grid-3 {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 16px;
}
.section {
  margin: 6px 0 8px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.image-row {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
}
.image-row .row-remove {
  padding: 2px 6px;
  font-size: 16px;
  font-weight: 700;
  color: var(--autotune-muted, #909399);
}
.add {
  margin-bottom: 14px;
}
.mode {
  margin-bottom: 10px;
}
.template-summary {
  padding-top: 28px;
  line-height: 1.6;
}
.warn {
  color: var(--el-color-warning);
}
.weights-check {
  width: 100%;
  line-height: 1.5;
}
.template-desc {
  margin: 4px 0;
}
.problem {
  margin-right: 12px;
}
.tiny {
  font-size: 11.5px;
}
</style>

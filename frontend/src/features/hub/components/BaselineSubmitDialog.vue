<script setup lang="ts">
/** The submit dialog. An entry arrives as a pasted cookbook command, so that
 *  is what the dialog asks for up front: a name, the image and the command.
 *  The platform compiles the command as you type and shows what will run;
 *  the parsed flags, env vars and volumes stay editable under Advanced, as
 *  rows or as JSON, and every edit there rebuilds the command.
 *
 *  In TRACK mode the track supplies everything it fixes: the weights path,
 *  served name, card type, precision and benchmark are shown locked, the
 *  image is a dropdown of the verified list, card count is read off the
 *  config, and a pasted command's fixed parts are replaced and reported —
 *  never silently. Without a track it is the free-form (admin) path.
 *
 *  "Start from" copies an earlier entry's config into a new entry; evolving
 *  an entry into its next version is the entry page's job.
 */
import { CopyDocument } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api, apiErrorText, ConfigChips, InfoHint, useI18n } from '@/plugins/api'
import type { BaselineSubmission, BaselineTrack, FlagSpec } from '../types'
import { useSubmissionDraft } from '../composables/useSubmissionDraft'

const props = defineProps<{
  modelValue: boolean
  track?: BaselineTrack | null
  resubmitOf?: BaselineSubmission | null
  gpuTypes: string[]
  flagCatalog: Record<string, FlagSpec[]>
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', open: boolean): void
  (e: 'submitted', id: number): void
}>()

const { t, locale } = useI18n()

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})
const track = computed(() => props.track ?? null)
const resubmitOf = computed(() => props.resubmitOf ?? null)
const {
  contestBoard, hasSavedToken, publishedAs, advanced, asJson,
  busy, uncertain, validationTarget, validationMessage, validationRevision, revealValidation, parsing, parseReport, trackReport, form,
  argRows, envRows, volRows, jsonBad, commandFocused, focusedPane,
  cardsFromRows, supportsNodes, nodeCount, perNodeCards, nodesBad, parsedArgs,
  entries, fromId, copiedFrom,
  isKnown, onArgKeyBlur, onUiEdit, onArgsText, onEnvText, onVolumesText,
  onCommandInput, onCommandBlur, onPaneBlur, useSuggestedImage,
  entryLabel, startFrom, submit,
} = useSubmissionDraft({
  open, track, resubmitOf, flagCatalog: computed(() => props.flagCatalog),
  api, t, notify: ElMessage, errorText: apiErrorText,
  onSubmitted: (id) => emit('submitted', id),
})

const parsedKeys = computed(() => Object.keys(parsedArgs.value))
const extrasLine = computed(() => {
  const parts: string[] = []
  const env = envRows.value.filter((r) => r.key.trim()).length
  const vols = volRows.value.filter((r) => r.host.trim() && r.target.trim()).length
  if (env) parts.push(t('baselineHub.submit.envCount', { n: env }))
  if (vols) parts.push(t('baselineHub.submit.volumeCount', { n: vols }))
  return parts.join(' · ')
})
const dialogBody = ref<HTMLElement | null>(null)
const groupCounts = computed(() => {
  const f = form.value
  return {
    launch: argRows.value.filter((row) => row.key.trim()).length
      + envRows.value.filter((row) => row.key.trim()).length
      + volRows.value.filter((row) => row.host.trim() || row.target.trim()).length,
    placement: Number(f.service_port !== 28200) + Number(nodeCount.value > 1)
      + Number(f.share_machine) + Number(!track.value && !!f.gpu_type),
    measurement: Number(!!f.benchmark_slug) + Number(!!f.target_metric)
      + Number(f.max_run_minutes !== 150),
    publishing: Number(!!f.contributor.trim()) + Number(!!f.contest_api_token.trim())
      + Number(!!f.notes.trim()) + Number(f.is_baseline),
  }
})
function validationElement() {
  const field = validationTarget.value
  if (!open.value || !field) return null
  return dialogBody.value?.querySelector<HTMLElement>(`[data-field="${field}"] input, [data-field="${field}"] textarea`) ?? null
}
let validationObserver: ResizeObserver | null = null
function stopValidationObserver() {
  validationObserver?.disconnect()
  validationObserver = null
}
onBeforeUnmount(stopValidationObserver)
watch(open, (value) => { if (!value) stopValidationObserver() })
watch(validationTarget, (value) => { if (!value) stopValidationObserver() })
watch(validationRevision, async () => {
  const revision = validationRevision.value
  await nextTick()
  if (revision !== validationRevision.value) return
  stopValidationObserver()
  const target = validationElement()
  if (!target) return
  const align = () => {
    if (open.value && revision === validationRevision.value && document.activeElement === target) {
      target.scrollIntoView({ block: 'nearest', behavior: 'auto' })
    }
  }
  target.focus()
  align()
  const group = target.closest('.el-collapse-item__wrap')
  if (group) {
    validationObserver = new ResizeObserver(align)
    validationObserver.observe(group)
  }
})
watch(() => [form.value.name, form.value.image, form.value.model_path, form.value.command,
  form.value.argsText, form.value.envText, form.value.volumesText, form.value.machine_count,
  form.value.gpu_count, form.value.service_port, form.value.contributor, form.value.contest_api_token],
(values, previous) => {
  const fields = ['name', 'image', 'model_path', 'command', 'args', 'env', 'vols', 'nodes',
    'gpu', 'placement', 'publishing', 'publishing']
  if (fields.some((field, index) => field === validationTarget.value && values[index] !== previous[index])) {
    validationTarget.value = ''
    validationMessage.value = ''
  }
})

async function submitWithValidation() {
  if (busy.value || uncertain.value) return
  validationTarget.value = ''
  const required = [
    { field: 'name', value: form.value.name, message: t('baselineHub.submit.nameRequired') },
    { field: 'image', value: form.value.image, message: t('baselineHub.submit.imageRequired') },
    ...(!track.value ? [{ field: 'model_path', value: form.value.model_path,
      message: t('baselineHub.submit.modelRequired') }] : []),
  ].find((item) => !item.value.trim())
  if (required) {
    await nextTick()
    revealValidation(required.field, required.message)
    return
  }
  await submit()
}

const title = computed(() => {
  const s = resubmitOf.value
  if (s) return locale.value === 'zh' ? `基于 ${s.name} 提交 v${s.version + 1}` : `Evolve ${s.name} to v${s.version + 1}`
  return track.value
    ? (locale.value === 'zh' ? `提交配置 · ${track.value.name}` : `Enter track · ${track.value.name}`)
    : (locale.value === 'zh' ? '提交独立评测' : 'Submit baseline (free-form)')
})
</script>

<template>
  <el-dialog v-model="open" :title="title" width="860px" top="4vh" class="hub-dialog">
    <div ref="dialogBody" class="submission-content">
    <el-alert v-if="uncertain" type="warning" :closable="false" show-icon
      title="提交结果尚未确认，请先核查赛道提交记录。">
      <el-button text @click="uncertain = false">已核查记录，允许再次提交</el-button>
    </el-alert>
    <el-alert v-if="validationMessage" :title="validationMessage" type="error" :closable="false" show-icon class="validation-message" />
    <div class="head-row">
      <div v-if="track" class="track-banner">
        <b>{{ track.model_name }} · {{ track.gpu_type }}</b>
        <span v-if="track.gate_text" class="gate mono">{{ track.gate_text }}</span>
      </div>
      <span v-else />
      <el-select v-if="!resubmitOf && entries.length" v-model="fromId" filterable
        class="start-from" :placeholder="t('baselineHub.submit.startFrom')"
        @change="startFrom">
        <el-option v-for="e in entries" :key="e.id" :label="entryLabel(e)" :value="e.id" />
      </el-select>
    </div>
    <div v-if="copiedFrom" class="copied-from muted tiny">
      <el-icon><CopyDocument /></el-icon>
      {{ t('baselineHub.submit.copiedFrom') }}
      <router-link :to="`/baseline-hub/${copiedFrom.id}`" target="_blank" class="link">
        {{ copiedFrom.name }}</router-link>
    </div>

    <el-form label-position="top" :disabled="busy" @submit.prevent="submitWithValidation">
      <div class="grid-2">
        <el-form-item data-field="name" :error="validationTarget === 'name' ? validationMessage : ''" :label="t('baselineHub.submit.name')">
          <el-input v-model="form.name" placeholder="e.g. sglang cookbook" />
        </el-form-item>
        <el-form-item v-if="track" data-field="image" :error="validationTarget === 'image' ? validationMessage : ''">
          <template #label>
            <span>{{ t('baselineHub.submit.verifiedImage') }}</span>
            <InfoHint>{{ t('baselineHub.submit.verifiedHint') }}</InfoHint>
          </template>
          <el-select v-model="form.image" :placeholder="t('baselineHub.submit.pickImage')"
            style="width: 100%" @change="onUiEdit">
            <el-option v-for="i in track.images" :key="i.image"
              :label="i.label ? `${i.label} · ${i.image}` : i.image" :value="i.image" />
          </el-select>
          <div v-if="trackReport && !trackReport.image_verified" class="warn tiny">
            ⚠ {{ t('baselineHub.submit.notVerified') }}
            <template v-if="trackReport.image_suggestion">
              — <a class="link" @click="useSuggestedImage">{{
                t('baselineHub.submit.useSuggested', { image: trackReport.image_suggestion }) }}</a>
            </template>
          </div>
        </el-form-item>
        <el-form-item v-else data-field="image" :error="validationTarget === 'image' ? validationMessage : ''" :label="t('baselineHub.submit.dockerImage')">
          <el-input v-model="form.image" class="mono"
            placeholder="lmsysorg/sglang:v0.4.9" @input="onUiEdit" />
        </el-form-item>
      </div>
      <el-form-item v-if="!track" data-field="model_path" :error="validationTarget === 'model_path' ? validationMessage : ''" :label="t('baselineHub.submit.weightsPath')">
        <el-input v-model="form.model_path" class="mono"
          placeholder="/data/models/glm-5" @input="onUiEdit" />
      </el-form-item>

      <el-form-item data-field="command">
        <template #label>
          <span>{{ t('baselineHub.submit.wholeCommand') }}</span>
          <span v-if="parsing" class="muted tiny compiling">
            {{ t('baselineHub.submit.compiling') }}</span>
          <InfoHint :width="420">{{ t('baselineHub.submit.commandHint') }}</InfoHint>
        </template>
        <el-input v-model="form.command" type="textarea" :rows="8" class="mono"
          placeholder="docker run -d --gpus all -v /mnt/models/x:/model … image sglang serve --tp 2 …"
          @input="onCommandInput" @focus="commandFocused = true" @blur="onCommandBlur" />
        <div v-if="parseReport" class="parse-report">
          <div v-if="parseReport.platform_managed.length" class="muted tiny">
            {{ t('baselineHub.submit.dropped') }}
            <span class="mono">{{ parseReport.platform_managed.join('  ') }}</span>
          </div>
          <div v-if="parseReport.normalized.length" class="muted tiny">
            {{ t('baselineHub.submit.normalized') }}
            <span class="mono">{{ parseReport.normalized.join(', ') }}</span>
          </div>
          <div v-for="w in parseReport.warnings" :key="w" class="warn tiny">⚠ {{ w }}</div>
        </div>
      </el-form-item>

      <!-- What will run: the parsed config, and what the track fixes. -->
      <div class="summary">
        <div class="summary-row">
          <span class="summary-label muted">{{ t('baselineHub.submit.willRun') }}</span>
          <span>
            <ConfigChips v-if="parsedKeys.length" :config="parsedArgs" :keys="parsedKeys"
              :cards="cardsFromRows" />
            <span v-else class="muted">{{ t('baselineHub.submit.nothingParsed') }}</span>
            <span v-if="extrasLine" class="muted tiny">{{ extrasLine }}</span>
          </span>
        </div>
        <div v-if="track" class="summary-row">
          <span class="summary-label muted">{{ t('baselineHub.submit.fixedByTrack') }}</span>
          <span class="fixed-line">
            <span class="mono">{{ track.model_path }}</span>
            · {{ t('baselineHub.submit.servedAs') }}
            <span class="mono">{{ track.served_model_name }}</span>
            · {{ cardsFromRows }} × {{ track.gpu_type }}<template v-if="nodeCount > 1">
              ({{ perNodeCards }} × {{ nodeCount }} {{ t('baselineHub.submit.nodes') }})</template>
            <template v-if="track.precision"> · {{ track.precision }}</template>
            <template v-if="track.benchmark_slug"> · {{ t('baselineHub.detail.benchmark') }}
              <span class="mono">{{ track.benchmark_slug }}</span></template>
          </span>
        </div>
        <div v-for="r in trackReport?.replaced ?? []" :key="r" class="muted tiny replaced">
          ↺ {{ r }}
        </div>
      </div>

      <!-- The one setting an entrant on a contest track has to supply. -->
      <el-form-item v-if="contestBoard && !hasSavedToken">
        <template #label>
          <span>{{ t('baselineHub.submit.contestToken') }}</span>
          <InfoHint :width="380">{{ t('baselineHub.submit.contestTokenHint',
            { id: contestBoard }) }}</InfoHint>
        </template>
        <el-input v-model="form.contest_api_token" type="password" show-password
          autocomplete="off" :placeholder="t('baselineHub.submit.contestTokenBlank')" />
        <el-checkbox v-if="form.contest_api_token.trim()" v-model="form.remember_contest_token">
          {{ t('baselineHub.submit.rememberToken') }}
        </el-checkbox>
      </el-form-item>

      <el-collapse v-model="advanced" class="advanced">
        <el-collapse-item name="launch">
          <template #title><span>{{ t('baselineHub.submit.launchGroup') }}</span><span v-if="groupCounts.launch" class="group-count">{{ t('baselineHub.submit.configuredCount', { n: groupCounts.launch }) }}</span></template>

          <div class="pane-head">
            <span class="section-label">{{ t('baselineHub.submit.launchParams') }}</span>
            <el-radio-group v-model="asJson" size="small">
              <el-radio-button :value="false">{{ t('baselineHub.submit.asRows') }}</el-radio-button>
              <el-radio-button :value="true">JSON</el-radio-button>
            </el-radio-group>
          </div>

          <template v-if="!asJson">
            <el-form-item>
              <template #label>
                <span>{{ t('baselineHub.submit.flags') }}</span>
                <InfoHint :width="380">{{ t('baselineHub.submit.flagsHint') }}</InfoHint>
              </template>
              <div v-for="(r, i) in argRows" :key="i" class="kv-row">
                <el-input v-model="r.key" class="mono key" placeholder="flag_name"
                  @input="onUiEdit" @blur="onArgKeyBlur(r)" />
                <el-switch v-if="r.mode === 'bool'" v-model="r.on" class="val-switch"
                  @change="onUiEdit" />
                <el-input v-else v-model="r.value" class="mono val" placeholder="value"
                  @input="onUiEdit" />
                <el-tooltip v-if="r.key.trim() && !isKnown(r.key)"
                  :content="t('baselineHub.submit.unknownFlag')">
                  <span class="unknown-badge">?</span>
                </el-tooltip>
                <span v-else class="badge-spacer" />
                <el-button text class="row-remove" :title="t('baselineHub.submit.remove')" :aria-label="t('baselineHub.submit.remove')"
                  @click="argRows.splice(i, 1); onUiEdit()">−</el-button>
              </div>
              <el-button size="small"
                @click="argRows.push({ key: '', mode: 'text', value: '', on: true })">
                {{ t('baselineHub.submit.addParam') }}
              </el-button>
            </el-form-item>

            <div class="grid-2">
              <el-form-item :label="t('baselineHub.submit.envVars')">
                <div v-for="(r, i) in envRows" :key="i" class="kv-row">
                  <el-input v-model="r.key" class="mono key" placeholder="NAME" @input="onUiEdit" />
                  <el-input v-model="r.value" class="mono val" placeholder="value"
                    @input="onUiEdit" />
                  <el-button text class="row-remove" :title="t('baselineHub.submit.remove')" :aria-label="t('baselineHub.submit.remove')"
                    @click="envRows.splice(i, 1); onUiEdit()">−</el-button>
                </div>
                <el-button size="small" @click="envRows.push({ key: '', value: '' })">
                  {{ t('baselineHub.submit.addEnv') }}
                </el-button>
              </el-form-item>
              <el-form-item :label="t('baselineHub.submit.volumes')">
                <div v-for="(r, i) in volRows" :key="i" class="kv-row">
                  <el-input v-model="r.host" class="mono key" placeholder="/host/path"
                    @input="onUiEdit" />
                  <span class="arrow">→</span>
                  <el-input v-model="r.target" class="mono val" placeholder="/container/path"
                    @input="onUiEdit" />
                  <el-button text class="row-remove" :title="t('baselineHub.submit.remove')" :aria-label="t('baselineHub.submit.remove')"
                    @click="volRows.splice(i, 1); onUiEdit()">−</el-button>
                </div>
                <el-button size="small" @click="volRows.push({ host: '', target: '' })">
                  {{ t('baselineHub.submit.addVolume') }}
                </el-button>
              </el-form-item>
            </div>
          </template>

          <template v-else>
            <el-form-item data-field="args" :error="validationTarget === 'args' ? validationMessage : ''">
              <template #label>
                <span>{{ t('baselineHub.submit.argsJson') }}</span>
                <span v-if="jsonBad.args" class="warn tiny bad-note">
                  {{ t('baselineHub.submit.badJson') }}</span>
              </template>
              <el-input v-model="form.argsText" type="textarea" :rows="6" class="mono"
                :class="jsonBad.args ? 'json-bad' : ''" @input="onArgsText"
                @focus="focusedPane = 'args'" @blur="onPaneBlur('args')" />
            </el-form-item>
            <div class="grid-2">
              <el-form-item data-field="env" :error="validationTarget === 'env' ? validationMessage : ''">
                <template #label>
                  <span>{{ t('baselineHub.submit.envJson') }}</span>
                  <span v-if="jsonBad.env" class="warn tiny bad-note">
                    {{ t('baselineHub.submit.badJsonShort') }}</span>
                </template>
                <el-input v-model="form.envText" type="textarea" :rows="3" class="mono"
                  :class="jsonBad.env ? 'json-bad' : ''" placeholder='{"SGLANG_XXX": "1"}'
                  @input="onEnvText" @focus="focusedPane = 'env'" @blur="onPaneBlur('env')" />
              </el-form-item>
              <el-form-item data-field="vols" :error="validationTarget === 'vols' ? validationMessage : ''">
                <template #label>
                  <span>{{ t('baselineHub.submit.volumesJson') }}</span>
                  <span v-if="jsonBad.vols" class="warn tiny bad-note">
                    {{ t('baselineHub.submit.badJsonShort') }}</span>
                </template>
                <el-input v-model="form.volumesText" type="textarea" :rows="3" class="mono"
                  :class="jsonBad.vols ? 'json-bad' : ''"
                  placeholder='{"/data/moe-config": "/config:ro"}' @input="onVolumesText"
                  @focus="focusedPane = 'vols'" @blur="onPaneBlur('vols')" />
              </el-form-item>
            </div>
          </template>

        </el-collapse-item>
        <el-collapse-item name="placement" data-field="placement">
          <template #title><span>{{ t('baselineHub.submit.placement') }}</span><span v-if="groupCounts.placement" class="group-count">{{ t('baselineHub.submit.configuredCount', { n: groupCounts.placement }) }}</span></template>
          <template v-if="!track">
            <div class="grid-3">
              <el-form-item :label="t('baselineHub.submit.engine')">
                <el-select v-model="form.engine" style="width: 100%" :disabled="!!resubmitOf"
                  @change="onUiEdit">
                  <el-option value="sglang" label="sglang" />
                  <el-option value="vllm" label="vllm" />
                </el-select>
              </el-form-item>
              <el-form-item :label="t('baselineHub.submit.servedModel')">
                <el-input v-model="form.served_model_name" placeholder="e.g. glm-5"
                  :disabled="!!resubmitOf" @input="onUiEdit" />
              </el-form-item>
              <el-form-item :label="t('baselineHub.submit.precisionOptional')">
                <el-input v-model="form.precision" placeholder="fp8, awq-int4, …" />
              </el-form-item>
            </div>
            <div class="grid-2">
              <el-form-item>
                <template #label>
                  <span>{{ t('baselineHub.submit.gpuType') }}</span>
                  <InfoHint>{{ t('baselineHub.submit.gpuTypeHint') }}</InfoHint>
                </template>
                <el-select v-model="form.gpu_type" clearable
                  :placeholder="t('baselineHub.submit.anyCard')" style="width: 100%">
                  <el-option v-for="g in gpuTypes" :key="g" :label="g" :value="g" />
                </el-select>
              </el-form-item>
              <el-form-item data-field="gpu" :error="validationTarget === 'gpu' ? validationMessage : ''">
                <template #label>
                  <span>{{ nodeCount > 1
                    ? t('baselineHub.submit.gpuCountPerNode')
                    : t('baselineHub.submit.gpuCount') }}</span>
                  <InfoHint>{{ nodeCount > 1
                    ? t('baselineHub.submit.gpuCountPerNodeHint')
                    : t('baselineHub.submit.gpuCountHint') }}</InfoHint>
                </template>
                <el-input-number v-model="form.gpu_count" :min="1" :max="16"
                  style="width: 100%" />
              </el-form-item>
            </div>
          </template>
          <div class="grid-2">
            <el-form-item>
              <template #label>
                <span>{{ t('baselineHub.submit.servicePort') }}</span>
                <InfoHint>{{ t('baselineHub.submit.servicePortHint') }}</InfoHint>
              </template>
              <el-input-number v-model="form.service_port" :min="1024" :max="65535"
                :controls="false" style="width: 100%" @change="onUiEdit" />
            </el-form-item>
            <el-form-item v-if="supportsNodes" data-field="nodes" :error="validationTarget === 'nodes' ? validationMessage : ''">
              <template #label>
                <span>{{ t('baselineHub.submit.nodes') }}</span>
                <InfoHint>{{ t('baselineHub.submit.nodesHint') }}</InfoHint>
              </template>
              <el-input-number v-model="form.machine_count" :min="1" :max="16"
                style="width: 100%" />
              <div v-if="nodesBad" class="warn tiny">{{ t('baselineHub.submit.nodesBad') }}</div>
              <div v-else-if="nodeCount > 1" class="muted tiny">
                {{ t('baselineHub.submit.perNodeCards', { n: perNodeCards }) }}
              </div>
            </el-form-item>
          </div>
          <el-form-item>
            <el-checkbox v-model="form.share_machine">
              {{ t('baselineHub.submit.allowSharing') }}
              <span class="muted tiny">{{ t('baselineHub.submit.allowSharingNote') }}</span>
            </el-checkbox>
          </el-form-item>

        </el-collapse-item>
        <el-collapse-item v-if="!track" name="measurement" data-field="measurement">
          <template #title><span>{{ t('baselineHub.submit.measurement') }}</span><span v-if="groupCounts.measurement" class="group-count">{{ t('baselineHub.submit.configuredCount', { n: groupCounts.measurement }) }}</span></template>
            <div class="grid-3">
              <el-form-item>
                <template #label>
                  <span>{{ t('baselineHub.submit.benchmark') }}</span>
                  <InfoHint>{{ t('baselineHub.submit.benchmarkHint') }}</InfoHint>
                </template>
                <el-input v-model="form.benchmark_slug" class="mono"
                  placeholder="(platform default)" />
              </el-form-item>
              <el-form-item>
                <template #label>
                  <span>{{ t('baselineHub.submit.rankingMetric') }}</span>
                  <InfoHint :width="360">{{ t('baselineHub.submit.rankingMetricHint') }}</InfoHint>
                </template>
                <el-input v-model="form.target_metric" class="mono"
                  placeholder="(benchmark score)" />
              </el-form-item>
              <el-form-item :label="t('baselineHub.submit.maxRunMinutes')">
                <el-input-number v-model="form.max_run_minutes" :min="5" :max="1440"
                  style="width: 100%" />
              </el-form-item>
            </div>
        </el-collapse-item>
        <el-collapse-item name="publishing" data-field="publishing">
          <template #title><span>{{ t('baselineHub.submit.publishingGroup') }}</span><span v-if="groupCounts.publishing" class="group-count">{{ t('baselineHub.submit.configuredCount', { n: groupCounts.publishing }) }}</span></template>
      <div v-if="track" class="baseline-flag">
        <el-checkbox v-model="form.is_baseline">{{ t('baselineHub.submit.isBaseline') }}</el-checkbox>
        <InfoHint :width="360">{{ t('baselineHub.submit.isBaselineHint') }}</InfoHint>
      </div>

          <template v-if="track">
            <el-form-item>
              <template #label>
                <span>{{ t('baselineHub.submit.contributor') }}</span>
                <InfoHint :width="380">{{ t('baselineHub.submit.contributorHint') }}</InfoHint>
              </template>
              <el-input v-model="form.contributor" maxlength="255"
                :placeholder="publishedAs" />
              <el-checkbox v-if="form.contributor.trim()" v-model="form.remember_contributor">
                {{ t('baselineHub.submit.rememberContributor') }}
              </el-checkbox>
            </el-form-item>
          </template>
          <el-form-item v-if="contestBoard && hasSavedToken">
            <template #label>
              <span>{{ t('baselineHub.submit.contestToken') }}</span>
              <InfoHint :width="380">{{ t('baselineHub.submit.contestTokenHint',
                { id: contestBoard }) }}</InfoHint>
            </template>
            <el-input v-model="form.contest_api_token" type="password" show-password
              autocomplete="off" :placeholder="t('baselineHub.submit.contestTokenSaved')" />
            <el-checkbox v-if="form.contest_api_token.trim()" v-model="form.remember_contest_token">
              {{ t('baselineHub.submit.rememberToken') }}
            </el-checkbox>
          </el-form-item>
          <el-form-item :label="t('baselineHub.submit.notes')">
            <el-input v-model="form.notes" :placeholder="t('baselineHub.submit.notesPlaceholder')" />
          </el-form-item>
        </el-collapse-item>
      </el-collapse>
    </el-form>
    </div>

    <template #footer>
      <div class="submit-footer">
      <span class="submit-summary">{{ cardsFromRows }} × {{ track?.gpu_type || form.gpu_type || 'GPU' }}</span>
      <el-button :disabled="busy" @click="open = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="busy" :disabled="uncertain" @click="submitWithValidation">
        {{ resubmitOf ? t('baselineHub.submit.submitAsVersion', { n: resubmitOf.version + 1 })
          : track ? t('baselineHub.enterTrack') : t('baselineHub.submit.submit') }}
      </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.group-count { color: var(--autotune-muted); font-size: 13px; font-weight: 400; margin-left: 12px; }
.validation-message { margin-bottom: 16px; }
.submission-content { min-width: 0; }
.submission-content :deep(input), .submission-content :deep(textarea) { scroll-margin-block: 12px; }

.submit-summary { margin-right: auto; color: var(--autotune-muted); font-size: 13px; }
.submit-footer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.head-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.track-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.gate {
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  color: var(--el-color-primary);
  font-weight: 600;
}
.start-from {
  width: 280px;
  flex: none;
}
.copied-from {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: -4px 0 10px;
}
.summary {
  margin: -4px 0 16px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--el-fill-color-light, #f5f7fa);
  font-size: 13px;
}
.summary-row {
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: 12px;
  align-items: baseline;
  padding: 3px 0;
}
.summary-label {
  font-size: 12px;
}
.fixed-line {
  word-break: break-all;
}
.replaced {
  padding-left: 142px;
}
.advanced {
  margin-top: 4px;
}
.advanced-note {
  margin-left: 10px;
}
.pane-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.section-label {
  margin: 6px 0 8px;
  font-size: 12px;
  font-weight: 600;

  color: var(--autotune-muted, #909399);
}
.baseline-flag {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
}
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
@media (max-width: 760px) {
  .grid-2,
  .grid-3 {
    grid-template-columns: 1fr;
  }
  .head-row { flex-wrap: wrap; }
  .start-from { width: 100%; }
  .summary-row { grid-template-columns: minmax(0, 1fr); gap: 4px; }
  .replaced { padding-left: 0; }
  .kv-row .el-input { min-width: 0; }
  .submit-summary { flex-basis: 100%; text-align: left; }

}
.kv-row {
  display: flex;
  gap: 6px;
  width: 100%;
  margin-bottom: 6px;
  align-items: center;
}
.kv-row .key {
  flex: 1.2;
}
.kv-row .val {
  flex: 1;
}
.kv-row .arrow {
  color: var(--autotune-muted, #909399);
}
.kv-row .val-switch {
  flex: 1;
}
.kv-row .row-remove {
  padding: 2px 6px;
  font-size: 16px;
  font-weight: 700;
  color: var(--autotune-muted, #909399);
}
.kv-row .row-remove:hover {
  color: var(--el-color-danger);
}
.unknown-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  background: var(--el-color-warning-light-8, #f8e3c5);
  color: var(--el-color-warning);
  font-size: 12px;
  font-weight: 700;
  cursor: help;
}
.badge-spacer {
  width: 18px;
  flex: none;
}
.parse-report {
  margin-top: 6px;
  width: 100%;
}
.warn {
  color: var(--el-color-warning);
}
.bad-note {
  margin-left: 8px;
}
.compiling {
  margin-left: 8px;
}
:deep(.json-bad .el-textarea__inner) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}
.tiny {
  font-size: 13px;
  margin-left: 6px;
}
.link {
  color: var(--el-color-primary);
  cursor: pointer;
}
</style>

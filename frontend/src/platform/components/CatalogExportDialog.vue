<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import CatalogPublicationReview from './CatalogPublicationReview.vue'
import { api as publicationApi } from '../../client'
import { api } from '../api/client'
import { apiErrorText } from '../utils/apiError'
import { useI18n } from '../i18n'
import { useCatalogExport, type CatalogExportProps } from '../catalog/useCatalogExport'
import { useCatalogPublication } from '../catalog/useCatalogPublication'
export type { CatalogCandidate } from '../catalog/useCatalogExport'

const props = defineProps<CatalogExportProps>()
const emit = defineEmits<{ (e: 'update:modelValue', open: boolean): void }>()
const { t, locale } = useI18n()
const text = (zh: string, en: string) => locale.value === 'zh' ? zh : en
const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})
const draft = useCatalogExport(props, { open, api, t, text, errorText: apiErrorText })
const {
  baselineRun, optimizedRun, reportStatus, rows, name, hf,
  displayName, description, family, tags, license, homepage, defaultVariant,
  includeEnv, includeVolumes, startupMinutes, gpuProduct, advanced,
  rememberCalls, loading, error, preview, picked, ready, changePair, isNew, onTick,
  variantIds, inputs, questions, kept, summary, decide, useSuggestion, hfSuggestion, catalogStatus,
} = draft
const publication = useCatalogPublication(publicationApi, open)
const { state, authorized, identity, checking, identityError, running, canSubmit, loadIdentity, submit } = publication
const step = ref<'configure' | 'review'>('configure')
const preparing = ref(false)
const busy = computed(() => preparing.value || loading.value || state.busy)
const submitted = computed(() => !!state.job && !['preview', 'unchanged', 'needs_review'].includes(state.job.status))
let revision = 0
watch(() => JSON.stringify(draft.body()), () => {
  publication.change(draft.body())
  if (!submitted.value) step.value = 'configure'
}, { flush: 'sync', immediate: true })
watch(open, isOpen => {
  revision++
  preparing.value = false
  if (isOpen && !state.job) draft.refreshCandidates()
})
function editConfiguration() {
  if (busy.value || running.value) return
  publication.change({})
  draft.refreshCandidates()
  step.value = 'configure'
}

async function preparePublication() {
  if (busy.value || !ready.value || !authorized.value || running.value) return
  const current = ++revision
  preparing.value = true
  try {
    const body = await draft.prepare()
    if (!body || !open.value || current !== revision) return
    await publication.preview(body)
    if (open.value && current === revision && state.job) step.value = 'review'
  } finally {
    if (current === revision) preparing.value = false
  }
}
</script>

<template>
  <el-dialog v-model="open" :title="t('catalogExport.title')" width="min(760px, calc(100vw - 24px))" top="4vh" class="hub-dialog catalog-dialog" :close-on-click-modal="false">
    <el-steps :active="step === 'configure' ? 0 : 1" finish-status="success" simple class="steps">
      <el-step :title="text('确认配置', 'Confirm configuration')" />
      <el-step :title="text('确认变更', 'Review changes')" />
    </el-steps>

    <template v-if="step === 'configure'">
      <el-form label-width="110px" size="small" :disabled="busy || running">
        <el-form-item :label="t('catalogExport.model')">
          <template v-if="!isNew"><strong>{{ name || modelName }}</strong></template>
          <el-input v-else v-model="name" :placeholder="modelName" class="mono" />
          <el-tag v-if="catalogStatus" size="small" class="hint"
            :type="catalogStatus === 'found' ? 'success' : 'info'">
            {{ t(`catalogExport.catalog.${catalogStatus}`) }}</el-tag>
        </el-form-item>
        <el-form-item v-if="isNew" :label="t('catalogExport.hf')" required>
          <el-input v-model="hf" class="mono" placeholder="org/name" />
          <el-button v-if="hfSuggestion && !hf" size="small" link class="hint" @click="useSuggestion">
            {{ t('catalogExport.useSuggestion', { hf: hfSuggestion }) }}</el-button>
        </el-form-item>
        <template v-if="trackSlug">
          <el-form-item label="Baseline">
            <template v-if="fixedBaselineRunId">
              <strong>{{ rows.find(r => r.run_id === fixedBaselineRunId)?.label || fixedBaselineRunId }}</strong>
              <span class="muted hint">{{ text('管理员指定', 'Fixed by the administrator') }}</span>
            </template>
            <el-select v-else v-model="baselineRun" :placeholder="text('选择 Baseline', 'Select Baseline')" @change="changePair">
              <el-option v-for="row in rows.filter(r => r.run_id !== optimizedRun)" :key="row.run_id" :value="row.run_id" :label="row.label" />
            </el-select>
          </el-form-item>
          <el-form-item label="Optimized">
            <el-select v-model="optimizedRun" :placeholder="text('选择 Optimized', 'Select Optimized')" @change="changePair">
              <el-option v-for="row in rows.filter(r => r.run_id !== baselineRun)" :key="row.run_id" :value="row.run_id" :label="row.label" />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item v-else-if="rows.length === 1" :label="text('配置', 'Configuration')">
          <strong>{{ rows[0]?.label }}</strong>
        </el-form-item>
        <el-form-item v-else :label="t('catalogExport.configs')">
          <div v-for="row in rows" :key="row.run_id" class="run-row">
            <el-checkbox v-model="row.picked" @change="onTick(row)">{{ row.label }}</el-checkbox>
            <el-select v-model="row.role" :disabled="!row.picked" size="small">
              <el-option value="baseline" label="Baseline" /><el-option value="optimized" label="Optimized" />
            </el-select>
          </div>
        </el-form-item>
      </el-form>
      <el-collapse v-model="advanced" class="advanced">
        <el-collapse-item :title="t('catalogExport.advanced')" name="advanced">
          <fieldset :disabled="busy || running">
            <template v-if="isNew">
              <h4>{{ t('catalogExport.modelDetails') }}</h4>
              <el-form label-width="150px" size="small" :disabled="busy || running">
                <el-form-item :label="t('catalogExport.displayName')">
                  <el-input v-model="displayName" maxlength="128" />
                </el-form-item>
                <el-form-item :label="t('catalogExport.descriptionField')">
                  <el-input v-model="description" type="textarea" :rows="3" maxlength="2048" />
                </el-form-item>
                <el-form-item :label="t('catalogExport.familyTags')">
                  <el-input v-model="family" placeholder="qwen" class="short" />
                  <el-input v-model="tags" placeholder="chat, moe" class="tags" />
                </el-form-item>
                <el-form-item :label="t('catalogExport.licenseHome')">
                  <el-input v-model="license" placeholder="apache-2.0" class="short" />
                  <el-input v-model="homepage" placeholder="https://huggingface.co/org/name" class="tags" />
                </el-form-item>
              </el-form>
            </template>
            <el-form label-width="150px" size="small" :disabled="busy || running">
              <el-form-item :label="t('catalogExport.variantIds')">
                <div class="ids">
                  <div v-for="row in picked" :key="row.run_id" class="id-row">
                    <span class="muted tiny id-label">{{ row.label }}</span>
                    <el-input v-model="row.id" size="small" class="mono" :placeholder="t('catalogExport.auto')" />
                    <el-input v-model="row.imageTag" size="small" class="mono tag-input"
                      :placeholder="t('catalogExport.imageTag')" />
                  </div>
                </div>
              </el-form-item>
              <el-form-item :label="t('catalogExport.defaultVariant')">
                <el-select v-model="defaultVariant" clearable :placeholder="t('catalogExport.auto')">
                  <el-option v-for="id in variantIds" :key="id" :value="id" :label="id" />
                </el-select>
              </el-form-item>
              <el-form-item :label="t('catalogExport.options')">
                <el-checkbox v-model="includeEnv">{{ t('catalogExport.includeEnv') }}</el-checkbox>
                <el-checkbox v-model="includeVolumes">{{ t('catalogExport.includeVolumes') }}</el-checkbox>
              </el-form-item>
              <el-form-item :label="t('catalogExport.startup')">
                <el-input-number v-model="startupMinutes" :min="1" :max="240" size="small"
                  :placeholder="t('catalogExport.auto')" :value-on-clear="null" />
              </el-form-item>
              <el-form-item label="gpuProduct">
                <el-input v-model="gpuProduct" class="mono" :placeholder="t('catalogExport.auto')" />
              </el-form-item>
            </el-form>
          </fieldset>
        </el-collapse-item>
      </el-collapse>
      <p class="muted tiny">{{ trackSlug ? text('预览时自动复用或生成对应报告，并检查发布内容。', 'Preview reuses or generates the comparison report and checks publication contents.') : text('预览时自动检查配置，并附带可用的对应报告。', 'Preview checks the configuration and includes an available matching report.') }}</p>
      <el-alert v-if="preview?.errors.length" type="error" :closable="false" :title="t('catalogExport.fixFirst')">
        <ul class="list"><li v-for="(item, i) in preview.errors" :key="i">{{ item }}</li></ul>
      </el-alert>
      <el-alert v-for="warning in preview?.warnings || []" :key="warning" type="warning" :closable="false" :title="warning" />
      <div v-if="questions.length" class="questions">
        <h4>{{ text('待确认：是否公开以下环境变量？', 'Decide whether to publish these environment variables') }}</h4>
        <div v-for="q in questions" :key="q.name" class="q-row">
          <code :title="q.value">{{ q.name }}={{ q.value }}</code>
          <el-button size="small" :disabled="busy" @click="decide(q.name, true)">{{ t('catalogExport.include') }}</el-button>
          <el-button size="small" :disabled="busy" @click="decide(q.name, false)">{{ t('catalogExport.leaveOut') }}</el-button>
        </div>
        <el-checkbox v-model="rememberCalls" :disabled="busy">{{ t('catalogExport.remember') }}</el-checkbox>
      </div>
    </template>

    <template v-else>
      <p><strong>{{ name || modelName }}</strong></p>
      <p v-for="row in picked" :key="row.run_id" class="configuration-summary">
        <span class="muted">{{ trackSlug ? row.role : text('配置', 'Configuration') }}</span> {{ row.label }}
      </p>
      <p v-if="reportStatus" class="muted">{{ reportStatus }}</p>
      <p v-else class="muted">{{ preview?.report_source ? text('将附带已有比较报告。', 'The matching comparison report will be included.') : text('未附带比较报告，本次仅发布配置。', 'No comparison report is attached; this publication contains configuration only.') }}</p>
      <CatalogPublicationReview v-if="state.job" :job="state.job" />
    </template>

    <details v-if="inputs.length" class="inputs">
      <summary>{{ text('查看环境变量与挂载', 'View environment variables and mounts') }} · {{ summary }}</summary>
      <el-table :data="inputs" size="small" max-height="280">
        <el-table-column :label="t('catalogExport.inputName')" min-width="240">
          <template #default="{ row }">
            <span class="mono">{{ row.name }}</span>
            <div class="muted tiny mono ellipsis" :title="row.value">
              {{ row.value }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('catalogExport.inExport')" width="150">
          <template #default="{ row }">
            <el-switch v-if="row.kind === 'env' && step === 'configure'" :model-value="kept(row)" size="small" :disabled="busy || running"
              :active-text="kept(row) ? t('catalogExport.actions.kept') : t('catalogExport.actions.left_out')"
              @change="(v: string | number | boolean) => decide(row.name, Boolean(v))" />
            <span v-else>{{ t(`catalogExport.actions.${row.action.replace(' ', '_')}`) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('catalogExport.why')" min-width="160">
          <template #default="{ row }"><span class="muted tiny">{{ row.reason }}</span></template>
        </el-table-column>
      </el-table>
    </details>
    <p v-if="preparing" role="status" class="muted">
      {{ state.busy ? text('正在准备 GitHub 变更预览…', 'Preparing GitHub changes…') : reportStatus || text('正在校验配置…', 'Validating configuration…') }}
    </p>
    <el-alert v-if="error || state.error" :title="error || state.error" type="error" :closable="false" />
    <div v-if="!authorized && !checking" class="authorization">
      <p>{{ identityError || text('创建 PR 需要授权 GitHub 发布。授权后返回此处将自动检查。', 'Authorize GitHub publishing to create a PR. Authorization is checked when you return.') }}</p>
      <RouterLink to="/account" target="_blank" rel="noopener">{{ text('授权 GitHub', 'Authorize GitHub') }}</RouterLink>
      <el-button link @click="loadIdentity">{{ text('重新检查', 'Check again') }}</el-button>
    </div>
    <p v-else class="muted tiny">{{ checking ? text('正在检查 GitHub 授权…', 'Checking GitHub authorization…') : text(`GitHub 已授权${identity?.login ? ' · ' + identity.login : ''}`, `GitHub authorized${identity?.login ? ' · ' + identity.login : ''}`) }}</p>

    <template #footer>
      <RouterLink v-if="state.job && step === 'review'" :to="`/publications/${state.job.id}`" target="_blank" rel="noopener" class="task-link">
        {{ text('查看发布任务', 'View publication task') }}</RouterLink>
      <el-button @click="open = false">{{ t('common.close') }}</el-button>
      <el-button v-if="step === 'review' && state.job?.status === 'succeeded'" :disabled="busy" @click="editConfiguration">
        {{ text('发布其他配置', 'Publish another configuration') }}</el-button>
      <el-button v-if="step === 'review' && !submitted" :disabled="busy" @click="editConfiguration">
        {{ text('返回修改', 'Back to edit') }}</el-button>
      <el-button v-if="step === 'configure' || state.job?.status === 'needs_review'" type="primary"
        :loading="busy" :disabled="!ready || !authorized || checking || running" @click="preparePublication">
        {{ text('预览发布', 'Preview publication') }}</el-button>
      <el-button v-else-if="canSubmit" type="primary" :loading="state.busy" @click="submit">
        {{ text(state.job?.status === 'preview' ? '创建 Pull Request' : '重试发布', state.job?.status === 'preview' ? 'Create Pull Request' : 'Retry publication') }}</el-button>
      <el-button v-else-if="state.job?.pr_url" type="primary" tag="a" :href="state.job.pr_url" target="_blank" rel="noopener noreferrer">
        {{ text('查看 PR', 'View PR') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.steps { margin-bottom: 24px; }
h4 { margin: 12px 0; font-size: 13px; }
fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
.hint { margin-left: 10px; }
.short { max-width: 140px; }
.tags { max-width: 280px; margin-left: 8px; }
.ids, .run-row { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.id-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.id-label { min-width: 140px; }
.tag-input { max-width: 170px; }
.questions { border: 1px solid var(--el-border-color-light); border-radius: 6px; padding: 8px 12px; margin: 12px 0; }
.q-row { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
.q-row code { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.list { padding-left: 18px; font-size: 13px; }
.configuration-summary span { display: inline-block; min-width: 90px; }
summary { cursor: pointer; padding: 12px 0; }
.inputs { margin: 12px 0; }
.ellipsis { max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tiny { font-size: 12px; }
.task-link { float: left; padding: 8px 0; }
.authorization { padding: 12px; background: var(--el-fill-color-light); margin-top: 12px; border-radius: 6px; }
.el-alert { margin-top: 12px; }
@media (max-width: 600px) {
  .catalog-dialog :deep(.el-form-item) { display: block; }
  .catalog-dialog :deep(.el-form-item__label) { float: none; display: block; width: auto !important; text-align: left; height: auto; padding-bottom: 6px; }
  .catalog-dialog :deep(.el-form-item__content) { margin-left: 0 !important; }
  .q-row { flex-wrap: wrap; }
  .q-row code { flex-basis: 100%; }
  .task-link { float: none; display: block; }
}
</style>

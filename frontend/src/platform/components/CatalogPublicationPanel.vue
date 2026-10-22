<script setup lang="ts">
import { toRef, watch } from 'vue'
import { api } from '../../client'
import { useCatalogPublication } from '../catalog/useCatalogPublication'
import CatalogPublicationReview from './CatalogPublicationReview.vue'
import { useI18n } from '../i18n'

const props = withDefaults(defineProps<{
  requestBody?: Record<string, unknown>; enabled?: boolean; active?: boolean; jobId?: string
}>(), { requestBody: () => ({}), enabled: false, active: true, jobId: '' })
const { locale } = useI18n()
const text = (zh: string, en: string) => locale.value === 'zh' ? zh : en
const { state, checking, identityError, authorized, running, canSubmit, loadIdentity, preview, submit, change } =
  useCatalogPublication(api, toRef(props, 'active'), toRef(props, 'jobId'))
watch(() => props.requestBody, body => { if (!props.jobId) change(body) }, { deep: true, immediate: true })
</script>

<template>
  <section class="publication-panel">
    <p v-if="!authorized && !checking">
      {{ identityError || text('创建 PR 需要授权 GitHub 发布。', 'Authorize GitHub publishing to create a PR.') }}
      <RouterLink to="/account?tab=connections" target="_blank" rel="noopener">{{ text('授权 GitHub', 'Authorize GitHub') }}</RouterLink>
      <el-button link :loading="checking" @click="loadIdentity">{{ text('重新检查', 'Check again') }}</el-button>
    </p>
    <el-alert v-if="state.error" :title="state.error" type="error" :closable="false" />
    <CatalogPublicationReview v-if="state.job" :job="state.job" />
    <div class="publication-actions">
      <el-button v-if="!jobId && (!state.job || state.job.status === 'needs_review')"
        :loading="state.busy" :disabled="!enabled || !authorized || running" @click="preview(requestBody)">
        {{ text('预览发布', 'Preview publication') }}</el-button>
      <el-button v-if="canSubmit" type="primary" :loading="state.busy" @click="submit">
        {{ text(state.job?.status === 'preview' ? '创建 Pull Request' : '重试发布', state.job?.status === 'preview' ? 'Create Pull Request' : 'Retry publication') }}</el-button>
      <a v-if="state.job?.pr_url" :href="state.job.pr_url" target="_blank" rel="noopener noreferrer">
        {{ text('查看 Pull Request', 'View Pull Request') }} #{{ state.job.pr_number }}</a>
      <RouterLink v-if="state.job && !jobId" :to="`/publications/${state.job.id}`" target="_blank" rel="noopener">
        {{ text('查看发布任务', 'View publication task') }}</RouterLink>
    </div>
  </section>
</template>

<style scoped>
.publication-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 12px 0; }
</style>

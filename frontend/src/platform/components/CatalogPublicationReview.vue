<script setup lang="ts">
import { computed } from 'vue'
import type { Publication } from '../../publication'
import { useI18n } from '../i18n'
const props = defineProps<{ job: Publication }>()
const { locale } = useI18n()
const text = (zh: string, en: string) => locale.value === 'zh' ? zh : en
const statuses = computed<Record<string, string>>(() => ({
  preview: text('请确认以下变更', 'Review the changes below'),
  unchanged: text('Catalog 已是最新，没有需要提交的变更', 'Catalog is up to date; no changes to publish'),
  queued: text('已排队，后台将自动创建 PR', 'Queued; PR will be created in the background'),
  forking: text('正在准备 GitHub fork', 'Preparing GitHub fork'),
  committing: text('正在提交文件', 'Committing files'), opening_pr: text('正在创建 PR', 'Opening PR'),
  succeeded: text('PR 已创建', 'PR created'), failed: text('发布失败，可以重试', 'Failed; retry available'),
  needs_authorization: text('需要重新授权 GitHub', 'GitHub authorization required'),
  needs_review: text('仓库或分支已变化，请重新预览', 'Repository or branch changed; preview again'),
}))
const reportFiles = computed(() => props.job.files.filter(f => f.kind === 'report' || f.path.endsWith('.html')))
const configFiles = computed(() => props.job.files.filter(f => !reportFiles.value.includes(f)))
</script>

<template>
  <section aria-live="polite">
    <p class="publication-status">{{ statuses[job.status] || job.status }}</p>
    <p>{{ text('目标仓库', 'Target repository') }}：<strong>{{ job.repository }}</strong> · {{ job.base_branch }}
      <span v-if="job.base_sha" class="muted tiny"> @ {{ job.base_sha.slice(0, 12) }}</span></p>
    <p>{{ text('变更', 'Changes') }}：{{ text(`${configFiles.length} 个配置文件，${reportFiles.length} 个报告文件`, `${configFiles.length} configuration files, ${reportFiles.length} report files`) }}</p>
    <el-alert v-if="job.error" :title="job.error" type="warning" :closable="false" />
    <el-alert v-for="warning in job.warnings" :key="warning" :title="warning" type="warning" :closable="false" />
    <details v-if="job.files.length">
      <summary>{{ text('查看文件变更', 'View file changes') }}</summary>
      <p v-for="file in reportFiles" :key="file.path" class="muted report-path">
        {{ file.path }} · {{ text('报告 HTML 将包含在 PR 中', 'Report HTML included in the PR') }}
      </p>
      <details v-for="file in configFiles" :key="file.path">
        <summary class="file-path">{{ file.path }}</summary><pre>{{ file.diff }}</pre>
      </details>
    </details>
    <details>
      <summary>{{ text('查看 PR 描述', 'View PR description') }}</summary>
      <h4>{{ job.title }}</h4><pre>{{ job.body }}</pre>
    </details>
    <p v-if="job.status === 'preview' && job.files.length" class="muted">
      {{ text('确认后，上述配置及报告将通过 PR 公开提交到目标仓库。', 'Confirming will publish these configurations and reports to the target repository through a PR.') }}
    </p>
  </section>
</template>

<style scoped>
.publication-status { font-weight: 600; }
summary { cursor: pointer; padding: 10px 0; }
details { border-top: 1px solid var(--el-border-color-light); }
details details { margin-left: 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 320px; overflow: auto; padding: 12px; background: var(--el-fill-color-light); font-size: 12px; }
.file-path, .report-path { overflow-wrap: anywhere; font-family: monospace; }
.el-alert { margin: 8px 0; }
.tiny { font-size: 12px; }
</style>

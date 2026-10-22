<script setup lang="ts">
/** Saved reports. Plugins may add actions to the header (`reports.actions`)
 *  and a section above the list (`reports.sections`), e.g. to write reports
 *  from here; both get `reload` to refresh the list. */
import { useUrlState, usePageScroll, queryString } from '../composables/pageState'
import { computed, onMounted, ref, toRefs } from 'vue'
import { api, type AgentReport } from '../api/client'
import InfoHint from '../components/InfoHint.vue'
import PageLoading from '../components/PageLoading.vue'
import PluginSlot from '../components/PluginSlot.vue'
import { useI18n } from '../i18n'
import { useFirstLoad } from '../utils/load'
import { useRowLink } from '../utils/nav'
import { exactTime, relativeTime } from '../utils/time'

const { t, locale } = useI18n()
const reportRows = useRowLink((row: AgentReport) => `/reports/${row.id}`)
const reports = ref<AgentReport[]>([])
const { q: search } = toRefs(useUrlState({ q: queryString() }))
const visibleReports = computed(() => reports.value.filter((item) =>
  [item.title, item.created_by_name, item.group?.name || ''].join(' ').toLowerCase()
    .includes(search.value.trim().toLowerCase())))
const emptyText = computed(() => search.value.trim()
  ? (locale.value === 'zh' ? '没有匹配的报告，请调整搜索条件。' : 'No matching reports. Try a different search.')
  : t('reports.empty'))

async function load() {
  reports.value = (await api.get('/agent/v1/reports')).data
}
const first = useFirstLoad(load)
usePageScroll(() => !first.loading && !first.error)

onMounted(first.run)
</script>

<template>
  <div class="page">
    <div class="header-row">
      <div>
        <h1 class="page-title">{{ t('reports.title') }}
          <InfoHint :width="380">{{ t('reports.how') }}</InfoHint>
        </h1>

      </div>
      <div class="actions"><PluginSlot name="reports.actions" :props="{ reload: load }" /></div>
    </div>
    <PluginSlot name="reports.sections" :props="{ reload: load }" />

    <el-input v-if="reports.length" v-model="search" clearable class="report-search" :aria-label="locale === 'zh' ? '搜索报告' : 'Search reports'" :placeholder="locale === 'zh' ? '搜索报告、赛道或作者' : 'Search reports, tracks or authors'" />
    <PageLoading v-if="first.loading || first.error" :error="first.error" @retry="first.run" />
    <template v-else>
    <el-table :data="visibleReports" :empty-text="emptyText"
      v-bind="reportRows" class="clickable report-list">
      <el-table-column :label="t('reports.title')" min-width="280">
        <template #default="{ row }">
          <router-link :to="`/reports/${row.id}`">{{ row.title }}</router-link>
          <el-tag v-if="!row.comparable" size="small" type="warning" class="tag"
            :title="t('reports.notComparableHint')">
            {{ t('reports.notComparable') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="reports.some((r) => r.group)" :label="t('reports.group')"
        min-width="140">
        <template #default="{ row }">
          <template v-if="row.group">
            <router-link v-if="row.group.page_path" :to="row.group.page_path">
              {{ row.group.name || row.group.slug }}
            </router-link>
            <span v-else>{{ row.group.name || row.group.slug }}</span>
          </template>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('reports.by')" width="140">
        <template #default="{ row }">{{ row.created_by_name || '—' }}</template>
      </el-table-column>
      <el-table-column :label="t('reports.when')" width="140">
        <template #default="{ row }">
          <span :title="exactTime(row.created_at)">{{ relativeTime(row.created_at) }}</span>
        </template>
      </el-table-column>
    </el-table>
    <div class="mobile-reports">
      <article v-for="item in visibleReports" :key="item.id" class="mobile-report">
        <router-link :to="`/reports/${item.id}`">{{ item.title }}</router-link>
        <p v-if="item.group" class="muted">{{ item.group.name || item.group.slug }}</p>
        <p v-if="!item.comparable" class="report-warning">{{ t('reports.notComparableHint') }}</p>
        <div class="muted">{{ item.created_by_name || '—' }} <span :title="exactTime(item.created_at)">{{ relativeTime(item.created_at) }}</span></div>
      </article>
      <p v-if="!visibleReports.length" class="muted">{{ emptyText }}</p>
    </div>
    </template>
  </div>
</template>

<style scoped>
a { color: var(--el-color-primary); text-decoration: none; }
a:hover { text-decoration: underline; }
.report-search { max-width: 380px; margin: 8px 0 20px; }
.mobile-reports { display: none; }
@media (max-width: 720px) {
  .report-list { display: none; }
  .mobile-reports { display: grid; gap: 12px; }
  .mobile-report { padding: 18px; border: 1px solid var(--autotune-border); border-radius: 8px; background: white; overflow-wrap: anywhere; }
  .mobile-report > a { color: var(--el-color-primary); font-weight: 600; text-decoration: none; }
  .mobile-report > div { display: flex; justify-content: space-between; gap: 12px; margin-top: 16px; }
  .report-warning { color: #94630d; font-size: 13px; }
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 12px;
}
.clickable :deep(.el-table__row) { cursor: pointer; }
.tag { margin-left: 8px; }
.actions { display: flex; gap: 8px; }
</style>

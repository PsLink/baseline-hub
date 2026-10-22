/** Only user-facing report extensions are installed in the standalone shell. */
import { defineAsyncComponent } from 'vue'
const actions = defineAsyncComponent(() => import('../../features/autoreport/components/ReportActions.vue'))
const jobs = defineAsyncComponent(() => import('../../features/autoreport/components/ReportJobs.vue'))
export function slotComponents(name: string) {
  if (name === 'reports.actions') return [actions]
  if (name === 'reports.sections') return [jobs]
  return []
}

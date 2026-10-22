/** The report jobs both slot components show: the header's buttons start
 *  them, the section above the list follows them. One list, one poll. */
import { reactive } from 'vue'
import { api } from '@/plugins/api'
import type { Job, JobDetail } from './types'

export const jobs = reactive({
  list: [] as Job[],
  loaded: false,
  /** The job whose steps the drawer shows. */
  open: null as JobDetail | null,
  async refresh() {
    jobs.list = (await api.get('/autoreport/jobs', { params: { limit: 20 } })).data
    jobs.loaded = true
  },
  async show(id: number) {
    jobs.open = (await api.get(`/autoreport/jobs/${id}`)).data
  },
})

export const running = (j: Job) => j.status === 'queued' || j.status === 'running'

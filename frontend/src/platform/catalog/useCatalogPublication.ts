import { computed, onScopeDispose, reactive, ref, watch, type Ref } from 'vue'
import {
  activePublication, canSubmitPublication, createPublicationFlow,
  type Publication, type PublicationState,
} from '../../publication.ts'

type Request = <T>(path: string, method?: string, body?: unknown) => Promise<T>

/** Shared authorization and immutable publication lifecycle for dialogs and job pages. */
export function useCatalogPublication(request: Request, active: Ref<boolean>, jobId?: Ref<string>) {
  const state = reactive<PublicationState>({
    job: null, error: '', busy: false, bodyKey: '', reviewedKey: '',
  })
  const flow = createPublicationFlow(request, state)
  const identity = ref<{ login: string | null; publishing_authorized: boolean } | null>(null)
  const checking = ref(false)
  const identityError = ref('')
  const authorized = computed(() => !!identity.value?.publishing_authorized)
  const running = computed(() => activePublication(state.job))
  const canSubmit = computed(() => authorized.value && canSubmitPublication(state.job)
    && state.reviewedKey === state.bodyKey)
  let alive = true
  let identitySequence = 0
  let jobSequence = 0
  let timer: ReturnType<typeof setTimeout> | undefined

  async function loadIdentity() {
    const current = ++identitySequence
    checking.value = true
    identityError.value = ''
    try {
      const data = await request<typeof identity.value>('/auth/github/identity')
      if (alive && current === identitySequence) identity.value = data
    } catch (e) {
      if (alive && current === identitySequence) {
        identity.value = null
        identityError.value = (e as Error).message
      }
    } finally {
      if (alive && current === identitySequence) checking.value = false
    }
  }

  function schedule() {
    clearTimeout(timer)
    if (!active.value || !running.value || !alive) return
    timer = setTimeout(async () => { await flow.poll(); schedule() }, 3000)
  }
  watch(() => [active.value, jobId?.value] as const, async ([isActive, id]) => {
    const current = ++jobSequence
    clearTimeout(timer)
    if (!isActive) return
    void loadIdentity()
    if (id) {
      try {
        const job = await request<Publication>(`/catalog-export/publications/${encodeURIComponent(id)}`)
        if (alive && current === jobSequence) {
          state.job = job
          state.reviewedKey = state.bodyKey
        }
      } catch (e) {
        if (alive && current === jobSequence) state.error = (e as Error).message
      }
    }
    schedule()
  }, { immediate: true })
  watch(() => state.job?.status, schedule)
  function onFocus() {
    if (active.value) void loadIdentity()
  }
  if (typeof window !== 'undefined') window.addEventListener('focus', onFocus)
  onScopeDispose(() => {
    alive = false
    identitySequence++
    jobSequence++
    clearTimeout(timer)
    if (typeof window !== 'undefined') window.removeEventListener('focus', onFocus)
  })

  async function preview(body: Record<string, unknown>) {
    if (!alive || !active.value || !authorized.value || state.busy || running.value) return
    flow.change(body)
    await flow.preview(body)
  }
  async function submit() {
    if (alive && active.value && canSubmit.value) await flow.submit()
  }
  return {
    state, identity, checking, identityError, authorized, running, canSubmit,
    loadIdentity, preview, submit, change: flow.change,
  }
}

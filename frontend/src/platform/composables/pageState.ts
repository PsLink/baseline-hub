import { nextTick, reactive, watch, onMounted } from 'vue'
import type { UnwrapNestedRefs } from 'vue'
import { onBeforeRouteLeave, useRouter, useRoute } from 'vue-router'
import type { Router, LocationQueryValue } from 'vue-router'

type QueryValue = LocationQueryValue | LocationQueryValue[] | undefined
interface QueryField<T> { default: T; read: (value: QueryValue) => T }
export const queryString = (value = ''): QueryField<string> => ({ default: value, read: v => typeof v === 'string' ? v : value })
export const queryBoolean = (value = false): QueryField<boolean> => ({ default: value, read: v => v === '1' ? true : v === '0' ? false : value })
export const queryPage = (): QueryField<number> => ({ default: 1, read: v => typeof v === 'string' && /^[1-9]\d*$/.test(v) && Number.isSafeInteger(Number(v)) ? Number(v) : 1 })
export const queryChoice = <T extends string>(value: T, choices: readonly T[]): QueryField<T> => ({ default: value, read: v => typeof v === 'string' && choices.includes(v as T) ? v as T : value })

/** Route query is the source of truth; edits are batched to keep simultaneous updates together. */
export function createUrlState<S extends Record<string, QueryField<any>>>(router: Router, schema: S) {
  type State = { [K in keyof S]: S[K]['default'] }
  const read = () => Object.fromEntries(Object.entries(schema).map(([key, field]) => [key, field.read(router.currentRoute.value.query[key])])) as State
  const state = reactive(read()) as UnwrapNestedRefs<State>
  const path = router.currentRoute.value.path
  watch(() => router.currentRoute.value.query, () => {
    if (router.currentRoute.value.path === path) Object.assign(state, read())
  }, { flush: 'sync' })
  watch(state, () => {
    if (router.currentRoute.value.path !== path) return
    const query = { ...router.currentRoute.value.query }
    for (const [key, field] of Object.entries(schema)) {
      const value = state[key as keyof typeof state]
      if (value === field.default) delete query[key]
      else query[key] = typeof value === 'boolean' ? (value ? '1' : '0') : String(value)
    }
    if (JSON.stringify(query) !== JSON.stringify(router.currentRoute.value.query)) {
      void router.replace({ query, hash: router.currentRoute.value.hash })
    }
  }, { flush: 'post' })
  return state
}

export function useUrlState<S extends Record<string, QueryField<any>>>(schema: S) {
  return createUrlState(useRouter(), schema)
}

// Memory only: no search contents or credentials are written to persistent storage.
const pages = new Map<string, { url: string; y: number }>()
export function rememberedPage(path: string): string { return pages.get(path)?.url ?? path }
export function usePageScroll(ready: () => boolean) {
  const route = useRoute()
  const path = route.path
  const previous = pages.get(path)
  let restored = false
  const restore = async () => {
    if (restored || !ready() || route.path !== path) return
    restored = true
    await nextTick()
    window.scrollTo({ top: previous?.url === route.fullPath ? previous.y : 0, behavior: 'instant' })
  }
  onMounted(restore)
  watch(ready, restore, { flush: 'post' })
  onBeforeRouteLeave(() => {
    pages.set(path, { url: route.fullPath, y: window.scrollY })
    // Bound memory even when browsing many tracks.
    if (pages.size > 100) pages.delete(pages.keys().next().value!)
  })
}

/** Query edits happen in place; browser history retains its explicit scroll target. */
export function navigationScroll(toPath: string, fromPath: string, saved: { left: number; top: number } | null) {
  return saved ?? (toPath === fromPath ? false : { top: 0 })
}

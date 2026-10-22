/** Compatibility seam for the original Hub pages; no platform auth store is imported. */
export { api, downloadText, fetchRunLog } from '../platform/api/client'
export type * from '../platform/api/client'
export { useI18n } from '../platform/i18n'
export { usePoll } from '../platform/utils/poll'
export { ownsSubmission } from '../platform/utils/ownership'
export { useFirstLoad } from '../platform/utils/load'
export { useLinkTo, useRowLink } from '../platform/utils/nav'
export { apiErrorText } from '../platform/utils/apiError'
export { copyText } from '../platform/utils/clipboard'
export { exactTime, relativeTime } from '../platform/utils/time'
export { default as BackLink } from '../platform/components/BackLink.vue'
export { default as CampaignRuns } from '../platform/components/CampaignRuns.vue'
export { default as CatalogExportDialog } from '../platform/components/CatalogExportDialog.vue'
export type { CatalogCandidate } from '../platform/components/CatalogExportDialog.vue'
export { default as ConfigChips } from '../platform/components/ConfigChips.vue'
export { default as CopyButton } from '../platform/components/CopyButton.vue'
export { default as FactTile } from '../platform/components/FactTile.vue'
export { default as FlagChips } from '../platform/components/FlagChips.vue'
export { default as InfoHint } from '../platform/components/InfoHint.vue'
export { default as PageLoading } from '../platform/components/PageLoading.vue'
export { default as PluginSlot } from '../platform/components/PluginSlot.vue'
import { auth, refreshAuth } from '../client'
const store = {
  get user() { return auth.user },
  get isAuthenticated() { return !!auth.user },
  fetchMe: refreshAuth,
}
export const useAuthStore = () => store

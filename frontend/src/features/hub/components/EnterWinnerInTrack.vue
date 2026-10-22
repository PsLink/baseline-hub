<script setup lang="ts">
/** On a tuning campaign's page: put its winner on a Hub track's board, with
 *  the exact config it ran, when an open track has the same weights on the
 *  same card type. Shows nothing otherwise. */
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { api, apiErrorText, useI18n, type Campaign, type LeaderboardEntry } from '@/plugins/api'

type Option = { slug: string; name: string; image: string; image_verified: boolean }

const props = defineProps<{ campaign: Campaign | null; reload: () => unknown }>()
const { t } = useI18n()

const runId = ref<number | null>(null)
const options = ref<Option[]>([])
const track = ref('')
const name = ref('')
const busy = ref(false)
const entered = ref<number | null>(null)

const chosen = computed(() => options.value.find((o) => o.slug === track.value))

async function load(campaign: Campaign | null) {
  options.value = []
  entered.value = null
  // The Hub's own entries and other plugins' campaigns are not tuning results.
  if (!campaign || campaign.managed_by) return
  try {
    const board: LeaderboardEntry[] = (await api.get(`/campaigns/${campaign.id}/leaderboard`)).data
    const verified = board.filter((e) => e.stage === 'verify')
    const rows = verified.length ? verified : board.filter((e) => e.stage !== 'verify')
    const winner = rows.find((e) => !e.is_baseline && e.holds_redlines)
    if (!winner) return
    runId.value = winner.run_id
    options.value = (await api.get(`/baseline-hub/runs/${winner.run_id}/tracks`)).data
    track.value = options.value[0]?.slug ?? ''
    name.value = `optimized · ${campaign.name}`.slice(0, 128)
  } catch {
    options.value = []
  }
}
watch(() => props.campaign?.id, () => void load(props.campaign), { immediate: true })

async function enter() {
  if (!runId.value || !track.value || !name.value.trim()) return
  busy.value = true
  try {
    const { data } = await api.post(`/baseline-hub/runs/${runId.value}/enter`, {
      track: track.value,
      name: name.value.trim(),
    })
    entered.value = data.id
    ElMessage.success(t('baselineHub.fromCampaign.entered'))
  } catch (e) {
    ElMessage.error(apiErrorText(e, t('baselineHub.fromCampaign.failed')))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="options.length" class="enter">
    <span class="lead">{{ t('baselineHub.fromCampaign.lead', { run: runId }) }}</span>
    <el-select v-if="options.length > 1" v-model="track" size="small" style="width: 240px">
      <el-option v-for="o in options" :key="o.slug" :value="o.slug" :label="o.name" />
    </el-select>
    <router-link v-else :to="`/baseline-hub/tracks/${track}`">{{ chosen?.name }}</router-link>
    <el-input v-model="name" size="small" style="width: 260px" maxlength="128" />
    <el-button v-if="entered === null" size="small" type="primary" :loading="busy"
      :disabled="!name.trim()" @click="enter">
      {{ t('baselineHub.fromCampaign.enter') }}
    </el-button>
    <router-link v-else :to="`/baseline-hub/${entered}`">{{ t('baselineHub.fromCampaign.open', { id: entered }) }}</router-link>
    <span v-if="chosen && !chosen.image_verified" class="muted tiny">
      {{ t('baselineHub.fromCampaign.newImage') }}
    </span>
  </div>
</template>

<style scoped>
.enter {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border: 1px solid var(--autotune-border);
  border-radius: 8px;
  font-size: 13px;
}
.tiny {
  font-size: 12px;
}
</style>

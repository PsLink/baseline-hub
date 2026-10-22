<script setup lang="ts">
/** The account page's contest token card. The token is write-only: the field
 *  is always blank on load, because the server reports only whether one is
 *  saved. The name entries are published under is the platform's own
 *  contributor card. */
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'

import { api, apiErrorText, useI18n } from '@/plugins/api'

defineProps<{ me: unknown; reload: () => unknown }>()
const { t } = useI18n()

const hasToken = ref(false)
const token = ref('')
const busy = ref(false)

async function call(method: 'get' | 'put' | 'delete', done?: string) {
  busy.value = true
  try {
    const body = method === 'put' ? { token: token.value.trim() } : undefined
    const { data } = await api.request({ url: '/auth/me/contest-token', method, data: body })
    hasToken.value = Boolean(data?.has_contest_token)
    if (done) {
      token.value = ''
      ElMessage.success(t(done))
    }
  } catch (error) {
    if (done) ElMessage.error(apiErrorText(error, t('account.contest.saveFailed')))
  } finally {
    busy.value = false
  }
}

onMounted(() => call('get'))
</script>

<template>
  <el-card class="contest">
    <h3>{{ t('account.contest.title') }}</h3>
    <p class="muted tiny">{{ t('account.contest.note') }}</p>
    <p v-if="hasToken" class="muted tiny saved">✓ {{ t('account.contest.onFile') }}</p>
    <el-form label-position="top" @submit.prevent="token.trim() && call('put', 'account.contest.saved')">
      <el-form-item :label="t('account.contest.field')">
        <el-input v-model="token" type="password" show-password autocomplete="off"
          :placeholder="hasToken ? t('account.contest.replace') : t('account.contest.placeholder')" />
      </el-form-item>
      <div class="row">
        <el-button type="primary" :loading="busy" :disabled="!token.trim()"
          @click="call('put', 'account.contest.saved')">
          {{ t('account.contest.save') }}
        </el-button>
        <el-button v-if="hasToken" text :loading="busy"
          @click="call('delete', 'account.contest.cleared')">
          {{ t('account.contest.clear') }}
        </el-button>
      </div>
    </el-form>
  </el-card>
</template>

<style scoped>
/* As the account page's own cards: a 14px heading, small notes. */
h3 { margin: 0 0 10px; font-size: 14px; }
.tiny { font-size: 12px; line-height: 1.5; margin: 0 0 10px; }
.saved { color: var(--el-color-success); }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
</style>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiBase, setSession, type Session } from './client'
const username = ref(''), password = ref(''), error = ref(''), busy = ref(false)
const optionsReady = ref(false), signupError = ref('')
const github = ref(false), signup = ref(false), registering = ref(false), confirmation = ref('')
function switchMode() {
  registering.value = !registering.value
  password.value = ''; confirmation.value = ''; error.value = ''
}
const router = useRouter(), route = useRoute()
onMounted(async () => {
  if (typeof route.query.github_error === 'string') error.value = route.query.github_error
  try {
    const options = await api<{ github: boolean; signup: boolean; signup_error?: string }>('/auth/options')
    github.value = options.github; signup.value = options.signup
    signupError.value = options.signup_error || ''; optionsReady.value = true
  }
  catch (e) { error.value = (e as Error).message }
})
async function submit() {
  if (busy.value) return
  if (registering.value && password.value !== confirmation.value) {
    error.value = '两次输入的密码不一致'; return
  }
  busy.value = true; error.value = ''
  try {
    setSession(await api<Session>(registering.value ? '/auth/register' : '/auth/login', 'POST', { username: username.value, password: password.value }))
    await router.push('/')
  } catch (e) { error.value = (e as Error).message }
  finally { password.value = ''; confirmation.value = ''; busy.value = false }
}
</script>
<template>
  <section class="auth-panel narrow"><p class="eyebrow">BASELINE HUB</p><h1>{{ registering ? '注册 AutoTune 账号' : '登录 AutoTune 账号' }}</h1>
    <p class="muted">{{ registering ? '创建账号后即可选择赛道并提交配置。' : '连接已有账号，选择赛道并提交你的推理配置。' }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <form @submit.prevent="submit"><label>用户名<input v-model="username" :disabled="busy" required :minlength="registering ? 2 : 1" :maxlength="registering ? 64 : 128" autocomplete="username"></label>
      <label>密码<input v-model="password" type="password" :disabled="busy" required :minlength="registering ? 6 : 1" :maxlength="registering ? 128 : 1024" :autocomplete="registering ? 'new-password' : 'current-password'"></label>
      <label v-if="registering">确认密码<input v-model="confirmation" type="password" :disabled="busy" required minlength="6" maxlength="128" autocomplete="new-password"></label>
      <button :disabled="busy">{{ busy ? (registering ? '正在注册…' : '正在连接…') : (registering ? '注册并登录' : '登录并连接') }}</button></form>
    <p v-if="signup"><button type="button" class="quiet" :disabled="busy" @click="switchMode">{{ registering ? '已有账号？返回登录' : '没有账号？注册账号' }}</button></p>
    <p v-else-if="signupError" class="muted" role="status">{{ signupError }}</p>
    <p v-else-if="optionsReady" class="muted">当前平台未开放注册，请联系管理员创建账号。</p>
    <template v-if="github"><p class="muted">已绑定过 GitHub 的用户也可以使用 GitHub 登录。</p>
      <a class="button secondary" :href="`${apiBase}/auth/github/login`">使用 GitHub 登录</a></template>
    <p class="muted">首次使用请先登录 AutoTune 账号，再到账号页面绑定 GitHub。</p>
  </section>
</template>

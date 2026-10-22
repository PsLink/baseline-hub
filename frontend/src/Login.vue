<script setup lang="ts">
import { useI18n } from './platform/i18n'
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiBase, setSession, type Session } from './client'
const { locale } = useI18n()
const text = (zh: string, en: string) => locale.value === 'zh' ? zh : en
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
    error.value = text('两次输入的密码不一致', 'The passwords do not match'); return
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
  <section class="auth-panel narrow login-panel"><p class="eyebrow">BASELINE HUB</p><h1>{{ registering ? text('注册 AutoTune 账号', 'Create an AutoTune account') : text('登录 AutoTune 账号', 'Sign in to AutoTune') }}</h1>
    <p class="muted">{{ registering ? text('创建账号后即可选择赛道并提交配置。', 'Create an account to enter tracks and submit configurations.') : text('连接已有账号，选择赛道并提交你的推理配置。', 'Connect your account to enter tracks and submit inference configurations.') }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <form @submit.prevent="submit"><label>{{ text('用户名', 'Username') }}<input name="username" spellcheck="false" v-model="username" :disabled="busy" required :minlength="registering ? 2 : 1" :maxlength="registering ? 64 : 128" autocomplete="username"></label>
      <label>{{ text('密码', 'Password') }}<input name="password" v-model="password" type="password" :disabled="busy" required :minlength="registering ? 6 : 1" :maxlength="registering ? 128 : 1024" :autocomplete="registering ? 'new-password' : 'current-password'"></label>
      <label v-if="registering">{{ text('确认密码', 'Confirm password') }}<input name="confirmation" v-model="confirmation" type="password" :disabled="busy" required minlength="6" maxlength="128" autocomplete="new-password"></label>
      <button :disabled="busy">{{ busy ? (registering ? text('正在注册…', 'Creating account…') : text('正在连接…', 'Connecting…')) : (registering ? text('注册并登录', 'Create account and sign in') : text('登录并连接', 'Sign in and connect')) }}</button></form>
    <p v-if="signup"><button type="button" class="quiet" :disabled="busy" @click="switchMode">{{ registering ? text('已有账号？返回登录', 'Already have an account? Sign in') : text('没有账号？注册账号', 'Need an account? Register') }}</button></p>
    <p v-else-if="signupError" class="muted" role="status">{{ signupError }}</p>
    <p v-else-if="optionsReady" class="muted">{{ text('当前平台未开放注册，请联系管理员创建账号。', 'Registration is closed. Ask an administrator to create your account.') }}</p>
    <template v-if="github"><p class="muted">{{ text('已绑定过 GitHub 的用户也可以使用 GitHub 登录。', 'If you have linked GitHub, you can also sign in with GitHub.') }}</p>
      <a class="button secondary" :href="`${apiBase}/auth/github/login`">{{ text('使用 GitHub 登录', 'Sign in with GitHub') }}</a></template>
    <p class="muted">{{ text('首次使用请先登录 AutoTune 账号，再到账号页面绑定 GitHub。', 'First sign in with AutoTune, then link GitHub from your account settings.') }}</p>
  </section>
</template>

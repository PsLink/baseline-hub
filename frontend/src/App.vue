<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, auth, refreshAuth } from './client'
import { useI18n } from './platform/i18n'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'
const { locale, setLocale } = useI18n()
const ready = ref(false)
const router = useRouter()
onMounted(async () => {
  try { await refreshAuth() } catch { /* Login page remains accessible. */ }
  finally { ready.value = true }
})
async function logout() {
  await api('/auth/logout', 'POST')
  auth.user = null; auth.csrf = ''
  auth.upstream = null
  await router.push('/login')
}
</script>

<template>
  <el-config-provider :locale="locale === 'zh' ? zhCn : en">
  <header class="hub-header"><RouterLink to="/" class="brand"><svg class="brand-mark" viewBox="0 0 32 32" fill="none" aria-hidden="true"><rect width="32" height="32" rx="9" fill="currentColor"/><path d="M9 22V17M16 22V10M23 22V14" stroke="white" stroke-width="3" stroke-linecap="round"/></svg><span>Baseline <span class="brand-suffix">Hub</span></span></RouterLink>
    <nav :aria-label="locale === 'zh' ? '主导航' : 'Main navigation'"><RouterLink to="/baseline-hub" :class="{ 'section-active': $route.path.startsWith('/baseline-hub') }">{{ locale === 'zh' ? '赛道' : 'Tracks' }}</RouterLink><RouterLink to="/reports" :class="{ 'section-active': $route.path.startsWith('/reports') }">{{ locale === 'zh' ? '报告' : 'Reports' }}</RouterLink>
      <el-dropdown v-if="auth.user" trigger="click" @command="(command: string) => command === 'logout' ? logout() : command === 'language' ? setLocale(locale === 'zh' ? 'en' : 'zh') : router.push('/account')">
        <button class="user-menu">{{ auth.user.username }} ▾</button>
        <template #dropdown><el-dropdown-menu>
          <el-dropdown-item command="account">{{ locale === 'zh' ? '账号与连接' : 'Account and connections' }}</el-dropdown-item>
          <el-dropdown-item command="language">{{ locale === 'zh' ? 'English' : '中文' }}</el-dropdown-item>
          <el-dropdown-item command="logout" divided>{{ locale === 'zh' ? '退出登录' : 'Log out' }}</el-dropdown-item>
        </el-dropdown-menu></template>
      </el-dropdown>
      <template v-else><button class="quiet" @click="setLocale(locale === 'zh' ? 'en' : 'zh')">{{ locale === 'zh' ? 'English' : '中文' }}</button><RouterLink to="/login">{{ locale === 'zh' ? '登录' : 'Sign in' }}</RouterLink></template></nav>
  </header>
  <main v-if="ready">
    <p v-if="auth.user && auth.upstream && !auth.upstream.connected" class="hub-notice" role="status">{{ locale === 'zh' ? 'AutoTune 连接已过期。' : 'Your AutoTune connection has expired. ' }}<RouterLink to="/account?tab=connections">{{ locale === 'zh' ? '重新连接账号' : 'Reconnect your account' }}</RouterLink>{{ locale === 'zh' ? '后可继续查看赛道和提交。' : ' to access tracks and submissions.' }}</p>
    <section v-if="!auth.user && $route.path !== '/login'" class="auth-panel narrow"><h1>{{ locale === 'zh' ? '请先登录' : 'Sign in to continue' }}</h1><p class="muted">{{ locale === 'zh' ? '使用 AutoTune 账号查看赛道、提交配置和生成报告。' : 'Use your AutoTune account to access tracks, submit configurations and generate reports.' }}</p><RouterLink class="button" to="/login">{{ locale === 'zh' ? '前往登录' : 'Sign in' }}</RouterLink></section>
    <RouterView v-else :key="$route.path" />
  </main><main v-else>{{ locale === 'zh' ? '正在加载…' : 'Loading…' }}</main>
  </el-config-provider>
</template>

import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import { navigationScroll } from './platform/composables/pageState'
import 'element-plus/dist/index.css'
import './platform/theme.css'
import './style.css'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  scrollBehavior: (to, from, savedPosition) => navigationScroll(to.path, from.path, savedPosition),
  routes: [
    { path: '/', redirect: '/baseline-hub' },
    { path: '/login', component: () => import('./Login.vue') },
    { path: '/tracks/:slug', redirect: to => `/baseline-hub/tracks/${encodeURIComponent(String(to.params.slug))}` },
    { path: '/publications/:id', component: () => import('./PublicationView.vue') },
    { path: '/account', component: () => import('./Account.vue') },
    { path: '/baseline-hub', component: () => import('./features/hub/views/BaselineHubView.vue') },
    { path: '/baseline-hub/tracks/:slug', component: () => import('./features/hub/views/BaselineTrackView.vue') },
    { path: '/baseline-hub/:id', component: () => import('./features/hub/views/BaselineHubDetailView.vue') },
    { path: '/reports', component: () => import('./platform/views/ReportsView.vue') },
    { path: '/reports/:id', component: () => import('./platform/views/ReportView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
createApp(App).use(router).mount('#app')

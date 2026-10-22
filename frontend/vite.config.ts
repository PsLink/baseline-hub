import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig(({ mode }) => ({
  base: loadEnv(mode, '.', 'VITE_').VITE_BASE_PATH || '/',
  plugins: [vue(), Components({ resolvers: [ElementPlusResolver({ importStyle: false })], dts: false })],
  resolve: { alias: { '@': new URL('./src', import.meta.url).pathname } },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (/node_modules\/(echarts|zrender)\//.test(id)) return 'charts'
          if (/node_modules\/(highlight\.js|markdown-it|mdurl|uc\.micro|linkify-it|punycode\.js|entities)\//.test(id)) return 'markdown'
          if (/node_modules\/(vue|@vue|vue-router|axios)\//.test(id)) return 'vue'
          return undefined
        },
      },
    },
  },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
}))

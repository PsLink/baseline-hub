<script setup lang="ts">
import { useRoute } from 'vue-router'
import CatalogPublicationPanel from './platform/components/CatalogPublicationPanel.vue'
const route = useRoute()
</script>
<template>
  <section class="publication-page"><RouterLink to="/account?tab=publications">← 发布历史</RouterLink><h1>Catalog 发布任务</h1>
    <CatalogPublicationPanel :job-id="String(route.params.id)" />
  </section>
</template>

<style scoped>
.publication-page { max-width: 1040px; margin: 0 auto; padding: 24px; }
</style>

<script setup lang="ts">
/** The "why" behind a control, one hover away.
 *
 *  Most of this app's explanations were correct and worth keeping, and were
 *  set as four-line paragraphs under every field — so the page read as a
 *  document and the controls got lost in it. The reasoning belongs here: still
 *  available, no longer in the way.
 */
import { ref } from 'vue'
import { useI18n } from '../i18n'
const { locale } = useI18n()
const visible = ref(false)
defineProps<{ width?: number }>()
</script>

<template>
  <el-tooltip v-model:visible="visible" :trigger="['hover', 'focus']" placement="top" :show-after="120" :max-width="width ?? 320" :raw-content="false">
    <template #content>
      <div class="body" :style="{ maxWidth: `min(${width ?? 320}px, calc(100vw - 48px))` }">
        <slot />
      </div>
    </template>
    <button type="button" class="mark" :aria-label="locale === 'zh' ? '查看说明' : 'More information'" @click.stop="visible = true" @keydown.esc.stop="visible = false"><span aria-hidden="true">?</span></button>
  </el-tooltip>
</template>

<style scoped>
.mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  padding: 0;
  font-family: inherit;
  margin-left: 5px;
  border-radius: 6px;
  border: 0;
  color: var(--autotune-muted);
  background: #fff;
  font-size: 10px;
  line-height: 1;
  cursor: help;
  vertical-align: middle;
  user-select: none;
}
.mark span { display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; border: 1px solid currentColor; border-radius: 50%; }
@media (pointer: coarse) { .mark { width: 44px; height: 44px; } }
.mark:hover,
.mark:focus {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);

}
.mark:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; }
.body {
  line-height: 1.55;
  font-size: 14px;
  overflow-wrap: anywhere;
  max-width: calc(100vw - 48px);
}
</style>

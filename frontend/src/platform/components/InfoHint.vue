<script setup lang="ts">
/** The "why" behind a control, one hover away.
 *
 *  Most of this app's explanations were correct and worth keeping, and were
 *  set as four-line paragraphs under every field — so the page read as a
 *  document and the controls got lost in it. The reasoning belongs here: still
 *  available, no longer in the way.
 */
import { useI18n } from '../i18n'
const { locale } = useI18n()
defineProps<{ width?: number }>()
</script>

<template>
  <el-tooltip placement="top" :show-after="120" :max-width="width ?? 320" :raw-content="false">
    <template #content>
      <div class="body" :style="{ maxWidth: `${width ?? 320}px` }">
        <slot />
      </div>
    </template>
    <button type="button" class="mark" :aria-label="locale === 'zh' ? '查看说明' : 'More information'">?</button>
  </el-tooltip>
</template>

<style scoped>
.mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  font-family: inherit;
  margin-left: 5px;
  border-radius: 50%;
  border: 1px solid var(--autotune-border);
  color: var(--autotune-muted);
  background: #fff;
  font-size: 10px;
  line-height: 1;
  cursor: help;
  vertical-align: middle;
  user-select: none;
}
.mark:hover,
.mark:focus {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);

}
.mark:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; }
.body {
  line-height: 1.55;
  font-size: 12.5px;
}
</style>

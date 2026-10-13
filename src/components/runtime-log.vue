<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue';
import type { RuntimeLogEntry } from './runtime-log';

const props = withDefaults(
  defineProps<{
    entries: RuntimeLogEntry[];
    loading?: boolean;
    active?: boolean;
    limit?: number;
    height?: number;
    title?: string;
    emptyText?: string;
  }>(),
  { loading: false, active: true, limit: 500, height: 300, title: '', emptyText: '暂无运行日志' }
);
const emit = defineEmits<{ refresh: [] }>();
const viewport = ref<HTMLElement | null>(null);
const atLatest = ref(true);
let scrollPosition = 0;
const toneClasses = {
  info: 'text-blue-500',
  success: 'text-green-600',
  warning: 'text-orange-500',
  error: 'text-red-500'
};

function handleScroll() {
  if (!props.active || !viewport.value) return;
  scrollPosition = viewport.value.scrollTop;
  atLatest.value = viewport.value.scrollHeight - scrollPosition - viewport.value.clientHeight < 24;
}

function scrollToLatest() {
  atLatest.value = true;
  if (!props.active || !viewport.value) return;
  viewport.value.scrollTop = viewport.value.scrollHeight;
  scrollPosition = viewport.value.scrollTop;
}

async function restoreScroll() {
  await nextTick();
  if (!props.active) return;
  if (atLatest.value) scrollToLatest();
  else if (viewport.value) viewport.value.scrollTop = scrollPosition;
}

// 数据刷新和标签切换共用定位逻辑；隐藏标签不改变跟随状态。
watch(() => props.entries, restoreScroll);
watch(() => props.active, restoreScroll);
onMounted(restoreScroll);

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('zh-CN', { hour12: false });
}
</script>

<template>
  <div class="runtime-log">
    <slot name="notice" />
    <div class="mb-10px flex flex-wrap items-center justify-between gap-8px">
      <div class="flex items-center gap-8px">
        <span v-if="title">{{ title }}</span>
        <ElTag type="info">最新 {{ entries.length }} / {{ limit }}</ElTag>
        <ElTag v-if="!atLatest" type="warning">已暂停跟随</ElTag>
      </div>
      <div class="flex items-center gap-8px">
        <ElButton v-if="!atLatest" text type="primary" @click="scrollToLatest">跳到最新</ElButton>
        <ElButton text :loading="loading" @click="emit('refresh')">刷新</ElButton>
      </div>
    </div>
    <div
      ref="viewport"
      class="runtime-log-viewport overflow-auto overscroll-contain border border-gray-200 rounded-4px dark:border-gray-700"
      :style="{ height: `${height}px` }"
      @scroll="handleScroll"
    >
      <div
        class="runtime-log-row sticky top-0 z-1 border-b border-gray-200 bg-gray-50 py-8px text-gray-500 dark:border-gray-700 dark:bg-gray-800"
      >
        <span>时间</span>
        <span>域名</span>
        <span>环节</span>
        <span>状态</span>
        <span>详细信息</span>
      </div>
      <div v-if="!entries.length" class="px-12px py-30px text-center text-13px text-gray-400">{{ emptyText }}</div>
      <div
        v-for="entry in entries"
        :key="entry.id"
        class="runtime-log-row runtime-log-entry border-b border-gray-100 py-7px dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50"
      >
        <span class="whitespace-nowrap text-gray-500" :title="entry.time">{{ formatTime(entry.time) }}</span>
        <span class="truncate" :title="entry.domain">{{ entry.domain || '--' }}</span>
        <span class="truncate" :title="entry.stage">{{ entry.stage || '--' }}</span>
        <span class="truncate" :class="toneClasses[entry.tone]" :title="entry.status">{{ entry.status }}</span>
        <span class="truncate text-gray-500" :title="entry.detail">{{ entry.detail || '--' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.runtime-log-row {
  display: grid;
  grid-template-columns: 82px 170px 100px 180px minmax(240px, 1fr);
  gap: 12px;
  min-width: 844px;
  padding-right: 12px;
  padding-left: 12px;
  font-size: 12px;
}
</style>

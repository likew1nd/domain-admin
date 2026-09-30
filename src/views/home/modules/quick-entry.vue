<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import type { RouteKey } from '@elegant-router/types';
import { useRouterPush } from '@/hooks/common/router';

defineOptions({ name: 'QuickEntry' });

export interface EntryItem {
  key: RouteKey;
  /** 菜单未改名时使用的默认名称 */
  title: string;
  desc: string;
  icon: string;
  color: string;
  badge?: string;
  badgeType?: 'primary' | 'success' | 'warning' | 'danger' | 'info';
}

const props = defineProps<{ items: EntryItem[] }>();

const router = useRouter();
const { routerPushByKey } = useRouterPush();

/** 只展示当前用户有权限的入口，名称跟随菜单管理中的菜单名 */
const visibleItems = computed(() =>
  props.items
    .filter(item => router.hasRoute(item.key))
    .map(item => {
      const title = router.getRoutes().find(route => route.name === item.key)?.meta.title;
      return { ...item, title: typeof title === 'string' && title ? title : item.title };
    })
);
</script>

<template>
  <ElCard class="card-wrapper" shadow="never">
    <template #header>
      <div class="flex items-center gap-8px">
        <SvgIcon icon="mdi:lightning-bolt-outline" class="text-18px text-primary" />
        <span>快速入口</span>
      </div>
    </template>
    <div class="entry-grid">
      <button
        v-for="item in visibleItems"
        :key="item.key"
        type="button"
        class="entry"
        @click="routerPushByKey(item.key)"
      >
        <span class="entry-icon" :style="{ color: item.color, backgroundColor: `${item.color}1a` }">
          <SvgIcon :icon="item.icon" />
        </span>
        <span class="entry-body">
          <span class="entry-title">
            {{ item.title }}
            <ElTag v-if="item.badge" :type="item.badgeType || 'info'" size="small" round disable-transitions>
              {{ item.badge }}
            </ElTag>
          </span>
          <span class="entry-desc">{{ item.desc }}</span>
        </span>
        <icon-ic-round-chevron-right class="entry-arrow" />
      </button>
    </div>
  </ElCard>
</template>

<style scoped>
.entry-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 12px;
}

.entry {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.2s,
    box-shadow 0.2s,
    transform 0.2s;
}

.entry:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: var(--el-box-shadow-light);
  transform: translateY(-2px);
}

.entry-icon {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 10px;
  font-size: 22px;
}

.entry-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.entry-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
}

.entry-desc {
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.entry-arrow {
  flex-shrink: 0;
  color: var(--el-text-color-placeholder);
  font-size: 18px;
}

.entry:hover .entry-arrow {
  color: var(--el-color-primary);
}
</style>

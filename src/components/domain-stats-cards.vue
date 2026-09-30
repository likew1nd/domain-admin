<script setup lang="ts">
import { computed } from 'vue';
import type { DomainListStats, DomainStatKey } from '@/service/api';

defineOptions({ name: 'DomainStatsCards' });

interface Props {
  stats?: DomainListStats | null;
  loading?: boolean;
  /** 当前筛选值，用于高亮已选中的项 */
  active?: Partial<Record<DomainStatKey, string>>;
}

const props = withDefaults(defineProps<Props>(), { stats: null, loading: false, active: () => ({}) });

/** 点击统计项时按该值筛选；再次点击同一项取消筛选 */
const emit = defineEmits<{ filter: [key: DomainStatKey, value: string] }>();

type Tone = 'success' | 'danger' | 'warning' | 'primary' | 'info';

interface GroupConfig {
  key: DomainStatKey;
  title: string;
  /** 风险类只展示命中（是）的数量 */
  risk?: boolean;
  /** 数据来源在筛选面板中没有对应的精确筛选项 */
  filterable?: boolean;
}

const groups: GroupConfig[] = [
  { key: 'deletion_status', title: '删除状态', filterable: true },
  { key: 'wechat_status', title: '微信', risk: true, filterable: true },
  { key: 'qq_status', title: 'QQ', risk: true, filterable: true },
  { key: 'pollution_status', title: '污染', risk: true, filterable: true },
  { key: 'blocked_status', title: '拦截', risk: true, filterable: true },
  { key: 'blacklist_status', title: '黑名单', risk: true, filterable: true },
  { key: 'filing_nature', title: '备案性质', filterable: true },
  { key: 'source', title: '数据来源' }
];

const toneMap: Record<string, Tone> = {
  可注册: 'success',
  待删除: 'danger',
  赎回期: 'warning',
  已过期: 'primary',
  检测失败: 'warning',
  企业: 'primary',
  个人: 'success',
  未备案: 'warning',
  查询失败: 'danger'
};

function formatCount(count: number) {
  if (count >= 10000) return `${Math.round(count / 1000) / 10}万`;
  return count.toLocaleString();
}

interface StatItem {
  label: string;
  text: string;
  count: number;
  tone: Tone;
  clickable: boolean;
}

const rows = computed(() =>
  groups.map(group => {
    const source = props.stats?.[group.key] || [];
    let items: StatItem[];
    if (group.risk) {
      const hit = source.find(item => item.label === '是')?.count || 0;
      items = [{ label: '是', text: '命中', count: hit, tone: hit ? 'danger' : 'info', clickable: true }];
    } else {
      items = source.map(item => ({
        label: item.label,
        text: item.label || '未查询',
        count: item.count,
        tone: toneMap[item.label] || 'info',
        // 空值代表未查询，筛选接口无法表达
        clickable: Boolean(group.filterable && item.label)
      }));
    }
    return { ...group, items };
  })
);

function handleClick(group: GroupConfig, item: StatItem) {
  if (!item.clickable) return;
  emit('filter', group.key, props.active[group.key] === item.label ? '' : item.label);
}
</script>

<template>
  <div v-loading="loading" class="stats-strip card-wrapper">
    <ElScrollbar>
      <div class="stats-row">
        <div v-for="group in rows" :key="group.key" class="stat-group" :class="{ risk: group.risk }">
          <span class="group-title">{{ group.title }}</span>
          <template v-if="stats && group.items.length">
            <button
              v-for="item in group.items"
              :key="item.label"
              type="button"
              class="stat-chip"
              :class="[`tone-${item.tone}`, { active: active[group.key] === item.label, clickable: item.clickable }]"
              :title="`${group.title} · ${item.text}：${item.count.toLocaleString()}${item.clickable ? '（点击筛选）' : ''}`"
              @click="handleClick(group, item)"
            >
              <i class="dot" />
              <span v-if="!group.risk">{{ item.text }}</span>
              <strong>{{ formatCount(item.count) }}</strong>
            </button>
          </template>
          <span v-else class="stat-empty">-</span>
        </div>
      </div>
    </ElScrollbar>
  </div>
</template>

<style scoped>
.stats-strip {
  flex-shrink: 0;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}

.stats-row {
  display: flex;
  align-items: center;
  width: max-content;
  min-width: 100%;
  height: 44px;
  padding: 0 4px;
}

.stat-group {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
  height: 100%;
  padding: 0 12px;
  white-space: nowrap;
}

.stat-group + .stat-group {
  border-left: 1px solid var(--el-border-color-lighter);
}

.group-title {
  margin-right: 2px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.stat-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 12px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-regular);
  font: inherit;
  font-size: 12px;
  cursor: default;
  transition: all 0.15s;
}

.stat-chip strong {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
}

.stat-chip.clickable {
  cursor: pointer;
}

.stat-chip.clickable:hover,
.stat-chip.active {
  border-color: var(--tone);
}

.stat-chip.active {
  background: var(--tone-bg);
}

.risk .stat-chip.tone-danger {
  background: var(--el-color-danger-light-9);
}

.risk .stat-chip.tone-danger strong {
  color: var(--el-color-danger);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--tone);
}

.stat-empty {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.tone-success {
  --tone: var(--el-color-success);
  --tone-bg: var(--el-color-success-light-9);
}

.tone-danger {
  --tone: var(--el-color-danger);
  --tone-bg: var(--el-color-danger-light-9);
}

.tone-warning {
  --tone: var(--el-color-warning);
  --tone-bg: var(--el-color-warning-light-9);
}

.tone-primary {
  --tone: var(--el-color-primary);
  --tone-bg: var(--el-color-primary-light-9);
}

.tone-info {
  --tone: var(--el-text-color-placeholder);
  --tone-bg: var(--el-fill-color);
}
</style>

<script setup lang="ts">
import { computed, ref } from 'vue';
import {
  type DateField,
  type DomainFilterModel,
  compositionOptions,
  compositionText,
  createDomainFilter
} from './domain-filter';

defineOptions({ name: 'DomainFilterPanel' });

interface Props {
  /** 高级筛选中的时间范围字段 */
  dateFields: readonly DateField[];
  loading?: boolean;
  keywordPlaceholder?: string;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  keywordPlaceholder: '搜索域名 / 备案信息 / 来源'
});

const emit = defineEmits<{
  (e: 'search'): void;
  (e: 'reset'): void;
}>();

const model = defineModel<DomainFilterModel>('model', { required: true });

const advancedVisible = ref(false);

const deletionOptions = ['可注册', '已过期', '待删除', '赎回期', '未过期'];
const filingNatureOptions = ['个人', '企业', '其他'];
const statusFields = [
  ['wechat_status', '微信'],
  ['qq_status', 'QQ'],
  ['pollution_status', '污染'],
  ['blocked_status', '拦截'],
  ['blacklist_status', '黑名单']
] as const;
const statusSegments = [
  { label: '全部', value: '' },
  { label: '是', value: '是' },
  { label: '否', value: '否' }
];

function daysAgo(days: number) {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date;
}

const dateShortcuts = [
  { text: '今天', value: () => [daysAgo(0), daysAgo(0)] },
  { text: '近 7 天', value: () => [daysAgo(6), daysAgo(0)] },
  { text: '近 30 天', value: () => [daysAgo(29), daysAgo(0)] },
  { text: '近 90 天', value: () => [daysAgo(89), daysAgo(0)] }
];

type FieldKey = Exclude<keyof DomainFilterModel, 'ranges'>;

const basicLabels: (readonly [FieldKey, string])[] = [
  ['keyword', '关键词'],
  ['suffix', '域名后缀'],
  ['length', '域名长度'],
  ['domain_composition', '域名组成'],
  ['deletion_status', '删除状态']
];
const advancedLabels: (readonly [FieldKey, string])[] = [['filing_nature', '备案性质'], ...statusFields];

interface ActiveFilter {
  key: string;
  label: string;
  text: string;
  advanced: boolean;
}

/** 当前已设置的筛选条件，以可移除标签的形式展示 */
const activeFilters = computed<ActiveFilter[]>(() => {
  const fieldItems = [
    ...basicLabels.map(item => [...item, false] as const),
    ...advancedLabels.map(item => [...item, true] as const)
  ].flatMap(([key, label, advanced]) => {
    const value = model.value[key];
    const text = Array.isArray(value) ? compositionText(value) : value;
    return text ? [{ key, label, text, advanced }] : [];
  });
  const rangeItems = props.dateFields.flatMap(({ key, label }) => {
    const range = model.value.ranges[key];
    return range?.some(Boolean)
      ? [{ key: `ranges.${key}`, label, text: `${range[0] || '不限'} ~ ${range[1] || '不限'}`, advanced: true }]
      : [];
  });
  return [...fieldItems, ...rangeItems];
});

const advancedActiveCount = computed(() => activeFilters.value.filter(item => item.advanced).length);

function removeFilter(key: string) {
  const defaults = createDomainFilter(props.dateFields);
  if (key.startsWith('ranges.')) {
    model.value.ranges[key.slice(7)] = null;
  } else {
    Object.assign(model.value, { [key]: defaults[key as FieldKey] });
  }
  emit('search');
}
</script>

<template>
  <ElCard class="filter-card card-wrapper" shadow="never">
    <ElForm :model="model" @submit.prevent="emit('search')">
      <!-- 常用筛选：始终展示 -->
      <div class="filter-bar">
        <div class="filter-fields">
          <ElInput v-model="model.keyword" class="w-280px" clearable :placeholder="keywordPlaceholder">
            <template #prefix><icon-ic-round-search /></template>
          </ElInput>
          <label class="field">
            <span class="field-label">后缀</span>
            <ElInput v-model="model.suffix" class="w-180px" clearable placeholder="cn,com" />
          </label>
          <label class="field">
            <span class="field-label">长度</span>
            <ElInput v-model="model.length" class="w-120px" clearable placeholder="1-5 或 3,5" />
          </label>
          <label class="field">
            <span class="field-label">组成</span>
            <ElSelect
              v-model="model.domain_composition"
              class="w-200px"
              multiple
              collapse-tags
              collapse-tags-tooltip
              :max-collapse-tags="2"
              clearable
              placeholder="全部"
            >
              <ElOption v-for="item in compositionOptions" :key="item.value" :label="item.label" :value="item.value" />
            </ElSelect>
          </label>
          <label class="field">
            <span class="field-label">删除状态</span>
            <ElSelect v-model="model.deletion_status" class="w-110px" clearable placeholder="全部">
              <ElOption v-for="item in deletionOptions" :key="item" :label="item" :value="item" />
            </ElSelect>
          </label>
        </div>
        <div class="filter-actions">
          <ElButton text :type="advancedVisible ? 'primary' : undefined" @click="advancedVisible = !advancedVisible">
            高级筛选
            <span v-if="advancedActiveCount" class="count-badge">{{ advancedActiveCount }}</span>
            <icon-ic-round-keyboard-arrow-down class="arrow" :class="{ 'arrow-up': advancedVisible }" />
          </ElButton>
          <ElButton @click="emit('reset')">重置</ElButton>
          <ElButton type="primary" native-type="submit" :loading="loading">
            <template #icon><icon-ic-round-search /></template>
            搜索
          </ElButton>
        </div>
      </div>

      <!-- 高级筛选：默认收起 -->
      <ElCollapseTransition>
        <div v-show="advancedVisible">
          <div class="advanced-panel">
            <div class="advanced-group">
              <div class="group-title">时间范围</div>
              <div class="date-grid">
                <div v-for="field in dateFields" :key="field.key" class="field">
                  <span class="field-label">{{ field.label }}</span>
                  <ElDatePicker
                    v-model="model.ranges[field.key]"
                    type="daterange"
                    value-format="YYYY-MM-DD"
                    range-separator="~"
                    start-placeholder="开始日期"
                    end-placeholder="结束日期"
                    unlink-panels
                    :shortcuts="dateShortcuts"
                    class="date-range"
                  />
                </div>
              </div>
            </div>
            <div class="advanced-group">
              <div class="group-title">状态标签</div>
              <div class="status-grid">
                <div v-for="[key, label] in statusFields" :key="key" class="field">
                  <span class="field-label">{{ label }}</span>
                  <ElSegmented v-model="model[key]" :options="statusSegments" size="small" />
                </div>
                <div class="field">
                  <span class="field-label">备案性质</span>
                  <ElSelect v-model="model.filing_nature" class="w-110px" size="small" clearable placeholder="全部">
                    <ElOption v-for="item in filingNatureOptions" :key="item" :label="item" :value="item" />
                  </ElSelect>
                </div>
              </div>
            </div>
          </div>
        </div>
      </ElCollapseTransition>

      <!-- 已设置的筛选条件，可单独移除 -->
      <div v-if="activeFilters.length" class="active-filters">
        <span class="active-title">已筛选</span>
        <ElTag
          v-for="item in activeFilters"
          :key="item.key"
          closable
          size="small"
          disable-transitions
          @close="removeFilter(item.key)"
        >
          {{ item.label }}：{{ item.text }}
        </ElTag>
        <ElButton link type="primary" size="small" @click="emit('reset')">清空全部</ElButton>
      </div>
    </ElForm>
  </ElCard>
</template>

<style scoped>
.filter-card {
  flex-shrink: 0;
}

.filter-card :deep(.el-card__body) {
  padding: 14px 16px;
}

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.filter-fields {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 16px;
}

.filter-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.filter-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.field {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.field-label {
  flex-shrink: 0;
  color: var(--el-text-color-regular);
  font-size: 13px;
  white-space: nowrap;
}

.count-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  margin-left: 4px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--el-color-primary);
  color: #fff;
  font-size: 11px;
  line-height: 1;
}

.arrow {
  margin-left: 2px;
  font-size: 16px;
  transition: transform 0.2s;
}

.arrow-up {
  transform: rotate(180deg);
}

.advanced-panel {
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
}

.advanced-group + .advanced-group {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--el-border-color);
}

.group-title {
  margin-bottom: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 600;
}

.date-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 10px 24px;
}

.date-grid .field-label {
  width: 56px;
}

.date-grid :deep(.date-range) {
  flex: 1;
  max-width: 280px;
}

.status-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 28px;
}

.active-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.active-title {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

@media (max-width: 768px) {
  .filter-fields,
  .filter-actions {
    width: 100%;
  }

  .filter-fields > * {
    width: 100% !important;
  }

  .field :deep(.el-input),
  .field :deep(.el-select) {
    flex: 1;
    width: auto !important;
  }

  .date-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .date-grid :deep(.date-range) {
    max-width: none;
  }

  .filter-actions {
    justify-content: flex-end;
  }
}
</style>

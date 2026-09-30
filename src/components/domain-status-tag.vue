<script setup lang="ts">
import { computed } from 'vue';

defineOptions({ name: 'DomainStatusTag' });

/** neutral：灰色，用于未过期、未查询等无需关注的状态 */
type TagType = 'primary' | 'success' | 'warning' | 'danger' | 'neutral';

interface Props {
  /** deletion：删除状态；risk：微信/QQ/污染/拦截/黑名单；filing：备案性质 */
  kind: 'deletion' | 'risk' | 'filing';
  value?: string | null;
}

const props = defineProps<Props>();

const deletionTypes: Record<string, TagType> = {
  可注册: 'success',
  待删除: 'danger',
  赎回期: 'warning',
  已过期: 'primary',
  未过期: 'neutral'
};

/** 风险标签命中（是）为危险，未命中（否）为安全 */
const riskTypes: Record<string, TagType> = {
  是: 'danger',
  否: 'success',
  未检测: 'neutral',
  检测失败: 'warning'
};

const filingTypes: Record<string, TagType> = {
  企业: 'primary',
  个人: 'success',
  已备案: 'primary',
  未备案: 'warning',
  查询失败: 'danger',
  未查询: 'neutral'
};

const typeMaps = { deletion: deletionTypes, risk: riskTypes, filing: filingTypes };

/** 未列出的取值（如政府机关、事业单位等备案性质）默认使用主色 */
const tagType = computed<TagType>(() => typeMaps[props.kind][props.value || ''] || 'primary');
</script>

<template>
  <ElTag
    v-if="value"
    :type="tagType === 'neutral' ? 'info' : tagType"
    :class="{ 'tag-neutral': tagType === 'neutral' }"
    size="small"
    effect="light"
    disable-transitions
  >
    {{ value }}
  </ElTag>
  <span v-else class="text-[var(--el-text-color-placeholder)]">-</span>
</template>

<style scoped>
.tag-neutral {
  --el-tag-bg-color: var(--el-fill-color-light);
  --el-tag-border-color: var(--el-border-color);
  --el-tag-text-color: var(--el-text-color-secondary);
}
</style>

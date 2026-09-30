<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import {
  type DomainListStats,
  type DomainStatKey,
  type QueryResult,
  clearQueryResults,
  fetchQueryResults
} from '@/service/api';
import { formatDateTime } from '@/utils/common';
import DomainStatusTag from './domain-status-tag.vue';
import DomainFilterPanel from './domain-filter-panel.vue';
import DomainStatsCards from './domain-stats-cards.vue';
import { type DateField, createDomainFilter, toFilterParams } from './domain-filter';

const props = defineProps<{ result: 'qualified' | 'unqualified' }>();

const dateFields: DateField[] = [
  { key: 'query', label: '查询时间', params: ['query_start', 'query_end'] },
  { key: 'joined', label: '加入时间', params: ['joined_start', 'joined_end'] },
  { key: 'registration', label: '注册时间', params: ['registration_start', 'registration_end'] },
  { key: 'expiration', label: '到期时间', params: ['expiration_start', 'expiration_end'] }
];

const rows = ref<QueryResult[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const loading = ref(false);
const clearing = ref(false);
const stats = ref<DomainListStats | null>(null);
const statsLoading = ref(false);
const filters = ref(createDomainFilter(dateFields));

/**
 * 统计基于体量较小的 domain_checks，开销低，每次加载都一并刷新；
 * showStatsLoading 仅在用户主动搜索时显示统计区加载状态，定时刷新时不闪烁
 */
async function loadData(showStatsLoading = false) {
  loading.value = true;
  statsLoading.value = showStatsLoading;
  try {
    const data = await fetchQueryResults(props.result, {
      page: page.value,
      pageSize: pageSize.value,
      withStats: true,
      filters: toFilterParams(filters.value, dateFields)
    });
    rows.value = data.records;
    total.value = data.total;
    stats.value = data.stats || null;
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加载查询结果失败');
  } finally {
    loading.value = false;
    statsLoading.value = false;
  }
}

function changePage(value: number) {
  page.value = value;
  loadData();
}

function changePageSize(value: number) {
  pageSize.value = value;
  page.value = 1;
  loadData();
}

function search() {
  page.value = 1;
  loadData(true);
}

function handleStatFilter(key: DomainStatKey, value: string) {
  if (key === 'source') return;
  filters.value[key] = value;
  search();
}

function reset() {
  filters.value = createDomainFilter(dateFields);
  search();
}

async function clearData() {
  try {
    await window.$messageBox?.confirm('确认清空当前列表数据吗？', '清空数据', {
      confirmButtonText: '确认清空',
      cancelButtonText: '取消',
      type: 'warning'
    });
  } catch {
    return;
  }
  clearing.value = true;
  try {
    const result = await clearQueryResults(props.result);
    window.$message?.success(`已清空 ${result.deleted} 条数据`);
    page.value = 1;
    await loadData(true);
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '清空数据失败');
  } finally {
    clearing.value = false;
  }
}

watch(
  () => props.result,
  () => loadData(true),
  { immediate: true }
);
const refreshTimer = setInterval(() => loadData(), 10_000);
onBeforeUnmount(() => clearInterval(refreshTimer));
</script>

<template>
  <div class="domain-page">
    <DomainFilterPanel v-model:model="filters" :date-fields="dateFields" @search="search" @reset="reset" />

    <DomainStatsCards :stats="stats" :loading="statsLoading" :active="filters" @filter="handleStatFilter" />

    <ElCard class="table-card card-wrapper" shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-12px">
          <div class="flex flex-wrap items-center gap-8px">
            <span>{{ $t(`page.domain.queryResults.${result}.title`) }}</span>
            <ElTag type="info">{{ $t('page.domain.queryResults.total', { total }) }}</ElTag>
          </div>
          <div class="flex flex-wrap items-center justify-end gap-8px">
            <ElButton :loading="loading" @click="loadData(true)">
              <template #icon><icon-ic-round-refresh /></template>
              {{ $t('common.refresh') }}
            </ElButton>
            <ElButton type="danger" plain :loading="clearing" @click="clearData">清空数据</ElButton>
          </div>
        </div>
      </template>
      <div class="table-wrapper">
        <ElTable v-loading="loading" height="100%" :data="rows" border row-key="domain">
          <ElTableColumn prop="domain" :label="$t('page.domain.queryResults.domain')" min-width="190" fixed="left" />
          <ElTableColumn prop="deletion_status" :label="$t('page.domain.queryResults.deletionStatus')" width="100">
            <template #default="{ row }"><DomainStatusTag kind="deletion" :value="row.deletion_status" /></template>
          </ElTableColumn>
          <ElTableColumn prop="creation_date" :label="$t('page.domain.queryResults.creationDate')" width="130" />
          <ElTableColumn prop="expiration_date" :label="$t('page.domain.queryResults.expirationDate')" width="130" />
          <ElTableColumn prop="wechat_status" :label="$t('page.domain.queryResults.wechat')" width="70">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.wechat_status" /></template>
          </ElTableColumn>
          <ElTableColumn prop="qq_status" :label="$t('page.domain.queryResults.qq')" width="70">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.qq_status" /></template>
          </ElTableColumn>
          <ElTableColumn prop="pollution_status" :label="$t('page.domain.queryResults.pollution')" width="80">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.pollution_status" /></template>
          </ElTableColumn>
          <ElTableColumn prop="blocked_status" :label="$t('page.domain.queryResults.blocked')" width="70">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.blocked_status" /></template>
          </ElTableColumn>
          <ElTableColumn prop="blacklist_status" :label="$t('page.domain.queryResults.blacklist')" width="90">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.blacklist_status" /></template>
          </ElTableColumn>
          <ElTableColumn prop="filing_nature" :label="$t('page.domain.queryResults.filingNature')" width="100">
            <template #default="{ row }"><DomainStatusTag kind="filing" :value="row.filing_nature" /></template>
          </ElTableColumn>
          <ElTableColumn
            prop="filing_info"
            :label="$t('page.domain.queryResults.filingInfo')"
            min-width="190"
            show-overflow-tooltip
          />
          <ElTableColumn prop="source" :label="$t('page.domain.queryResults.source')" width="130" />
          <ElTableColumn prop="query_time" :label="$t('page.domain.queryResults.queryTime')" width="170">
            <template #default="{ row }">{{ formatDateTime(row.checked_at || row.query_time) }}</template>
          </ElTableColumn>
        </ElTable>
      </div>
      <div class="pagination-bar">
        <ElPagination
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[20, 50, 100]"
          :total="total"
          layout="total, prev, pager, next, sizes, jumper"
          @current-change="changePage"
          @size-change="changePageSize"
        />
      </div>
    </ElCard>
  </div>
</template>

<style scoped>
/* 表格卡片保留最小高度：筛选区展开时由外层内容区滚动，而不是把表格挤没 */
.domain-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.table-card {
  flex: 1;
  min-height: 480px;
}

.table-card :deep(.el-card__body) {
  display: flex;
  flex: 1;
  flex-direction: column;
  height: auto;
  min-height: 0;
}

.table-wrapper {
  position: relative;
  flex: 1;
  min-height: 0;
}

.table-wrapper > :deep(.el-table) {
  position: absolute;
  inset: 0;
}

.pagination-bar {
  display: flex;
  flex-shrink: 0;
  justify-content: flex-end;
  padding-top: 12px;
}
</style>

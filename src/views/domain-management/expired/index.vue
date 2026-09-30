<script setup lang="ts">
import { reactive, ref } from 'vue';
import {
  type DomainListStats,
  type DomainRecord,
  type DomainStatKey,
  fetchDomainPage,
  resetDomainQueryTime
} from '@/service/api';
import { formatDateTime } from '@/utils/common';
import { $t } from '@/locales';
import DomainStatusTag from '@/components/domain-status-tag.vue';
import DomainFilterPanel from '@/components/domain-filter-panel.vue';
import DomainStatsCards from '@/components/domain-stats-cards.vue';
import { type DateField, createDomainFilter, toFilterParams } from '@/components/domain-filter';

const dateFields: DateField[] = [
  { key: 'joined', label: '加入时间', params: ['start_date', 'end_date'] },
  { key: 'registration', label: '注册时间', params: ['registration_start', 'registration_end'] },
  { key: 'expiration', label: '到期时间', params: ['expiration_start', 'expiration_end'] },
  { key: 'query', label: '查询时间', params: ['query_start', 'query_end'] }
];

const rows = ref<DomainRecord[]>([]);
const loading = ref(false);
const resetting = ref(false);
const total = ref(0);
const stats = ref<DomainListStats | null>(null);
const statsLoading = ref(false);

const searchForm = ref(createDomainFilter(dateFields));

const currentPage = ref(1);
const pageSize = ref(20);

const columnOptions = [
  ['domain', '域名'],
  ['deletion_status', '删除状态'],
  ['creation_date', '注册时间'],
  ['expiration_date', '到期时间'],
  ['wechat_status', '微信'],
  ['qq_status', 'QQ'],
  ['pollution_status', '污染'],
  ['blocked_status', '拦截'],
  ['blacklist_status', '黑名单'],
  ['filing_nature', '备案性质'],
  ['filing_info', '备案信息'],
  ['source', '数据来源'],
  ['joined_at', '加入时间'],
  ['query_time', '查询时间']
] as const;
const visibleColumns = reactive<string[]>(columnOptions.map(([key]) => key));

function hasColumn(key: string) {
  return visibleColumns.includes(key);
}

/** withStats 为 true 时同时刷新统计卡片；翻页只换数据，不重复统计 */
async function loadDomains(withStats = false) {
  loading.value = true;
  statsLoading.value = withStats;
  try {
    const result = await fetchDomainPage({
      page: currentPage.value,
      page_size: pageSize.value,
      with_stats: withStats ? 'true' : undefined,
      ...toFilterParams(searchForm.value, dateFields)
    });
    rows.value = result.records;
    total.value = result.total;
    if (withStats) stats.value = result.stats || null;
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加载过期域名失败');
  } finally {
    loading.value = false;
    statsLoading.value = false;
  }
}

function handleSearch() {
  currentPage.value = 1;
  loadDomains(true);
}

function handleStatFilter(key: DomainStatKey, value: string) {
  if (key === 'source') return;
  searchForm.value[key] = value;
  handleSearch();
}

function handleReset() {
  searchForm.value = createDomainFilter(dateFields);
  handleSearch();
}

function handleCurrentChange(page: number) {
  currentPage.value = page;
  loadDomains();
}

function handleSizeChange(size: number) {
  pageSize.value = size;
  currentPage.value = 1;
  loadDomains();
}

async function handleResetQueryTime() {
  try {
    await window.$messageBox?.confirm(
      '将清空查询结果并重置查询时间，域名、来源、加入时间会保留。确认继续吗？',
      '重置查询数据',
      {
        confirmButtonText: '确认重置',
        cancelButtonText: '取消',
        type: 'warning'
      }
    );
  } catch {
    return;
  }
  resetting.value = true;
  try {
    const result = await resetDomainQueryTime();
    window.$message?.success(`已重置 ${result.reset} 条查询时间，清空 ${result.cleared_results || 0} 条查询结果`);
    await loadDomains(true);
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '重置查询时间失败');
  } finally {
    resetting.value = false;
  }
}

loadDomains(true);
</script>

<template>
  <div class="domain-page">
    <DomainFilterPanel
      v-model:model="searchForm"
      :date-fields="dateFields"
      :loading="loading"
      @search="handleSearch"
      @reset="handleReset"
    />

    <DomainStatsCards :stats="stats" :loading="statsLoading" :active="searchForm" @filter="handleStatFilter" />

    <ElCard class="table-card card-wrapper" shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-12px">
          <div class="flex items-center gap-8px">
            <p>{{ $t('page.domain.expired.title') }}</p>
            <ElTag type="info">{{ $t('page.domain.expired.total', { total }) }}</ElTag>
          </div>
          <div class="flex flex-wrap items-center justify-end gap-8px">
            <ElButton @click="loadDomains(true)">
              <template #icon><icon-ic-round-refresh /></template>
              刷新
            </ElButton>
            <ElButton type="warning" plain :loading="resetting" @click="handleResetQueryTime">重置数据</ElButton>
            <ElPopover placement="bottom-end" trigger="click" width="240">
              <template #reference>
                <ElButton>
                  <template #icon><icon-ic-round-view-column /></template>
                  列设置
                </ElButton>
              </template>
              <ElCheckboxGroup v-model="visibleColumns" class="grid grid-cols-2 gap-x-8px">
                <ElCheckbox
                  v-for="item in columnOptions"
                  :key="item[0]"
                  :label="item[0]"
                  :disabled="item[0] === 'domain'"
                >
                  {{ item[1] }}
                </ElCheckbox>
              </ElCheckboxGroup>
            </ElPopover>
          </div>
        </div>
      </template>
      <div class="table-wrapper">
        <ElTable v-loading="loading" height="100%" border :data="rows" row-key="domain">
          <ElTableColumn v-if="hasColumn('domain')" prop="domain" label="域名" min-width="180" fixed="left" />
          <ElTableColumn v-if="hasColumn('deletion_status')" prop="deletion_status" label="删除状态" width="100">
            <template #default="{ row }"><DomainStatusTag kind="deletion" :value="row.deletion_status" /></template>
          </ElTableColumn>
          <ElTableColumn v-if="hasColumn('creation_date')" prop="creation_date" label="注册时间" width="125" />
          <ElTableColumn v-if="hasColumn('expiration_date')" prop="expiration_date" label="到期时间" width="125" />
          <ElTableColumn v-if="hasColumn('wechat_status')" prop="wechat_status" label="微信" width="70">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.wechat_status" /></template>
          </ElTableColumn>
          <ElTableColumn v-if="hasColumn('qq_status')" prop="qq_status" label="QQ" width="70">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.qq_status" /></template>
          </ElTableColumn>
          <ElTableColumn v-if="hasColumn('pollution_status')" prop="pollution_status" label="污染" width="70">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.pollution_status" /></template>
          </ElTableColumn>
          <ElTableColumn v-if="hasColumn('blocked_status')" prop="blocked_status" label="拦截" width="70">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.blocked_status" /></template>
          </ElTableColumn>
          <ElTableColumn v-if="hasColumn('blacklist_status')" prop="blacklist_status" label="黑名单" width="85">
            <template #default="{ row }"><DomainStatusTag kind="risk" :value="row.blacklist_status" /></template>
          </ElTableColumn>
          <ElTableColumn v-if="hasColumn('filing_nature')" prop="filing_nature" label="备案性质" width="100">
            <template #default="{ row }"><DomainStatusTag kind="filing" :value="row.filing_nature" /></template>
          </ElTableColumn>
          <ElTableColumn
            v-if="hasColumn('filing_info')"
            prop="filing_info"
            label="备案信息"
            min-width="180"
            show-overflow-tooltip
          />
          <ElTableColumn v-if="hasColumn('source')" prop="source" label="数据来源" width="120" />
          <ElTableColumn v-if="hasColumn('joined_at')" prop="joined_at" label="加入时间" width="180" />
          <ElTableColumn v-if="hasColumn('query_time')" prop="query_time" label="查询时间" width="170">
            <template #default="{ row }">{{ formatDateTime(row.query_time) }}</template>
          </ElTableColumn>
        </ElTable>
      </div>
      <div class="pagination-bar">
        <ElPagination
          layout="total,prev,pager,next,sizes,jumper"
          :current-page="currentPage"
          :page-size="pageSize"
          :page-sizes="[20, 50, 100]"
          :total="total"
          @current-change="handleCurrentChange"
          @size-change="handleSizeChange"
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

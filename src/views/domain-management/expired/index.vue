<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { ElMessageBox } from 'element-plus';
import {
  type DomainListStats,
  type DomainRecord,
  type DomainStatKey,
  clearExpiredDomains,
  deleteFilteredDomains,
  deleteSelectedDomains,
  fetchDomainPage,
  qualifySelectedDomains,
  resetDomainQueryTime
} from '@/service/api';
import { formatDateTime, formatDomainDateTime } from '@/utils/common';
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
const qualifying = ref(false);
type DeleteScope = 'all' | 'selected' | 'filtered';
const deleting = ref<DeleteScope | ''>('');
const busy = computed(() => loading.value || resetting.value || qualifying.value || Boolean(deleting.value));
const selectedDomains = ref<string[]>([]);
const total = ref(0);
const stats = ref<DomainListStats | null>(null);
const statsLoading = ref(false);

const searchForm = ref(createDomainFilter(dateFields));
const appliedFilters = ref(toFilterParams(searchForm.value, dateFields));
const hasAppliedFilters = computed(() => Object.values(appliedFilters.value).some(value => value.trim()));
const filtersChanged = computed(
  () => JSON.stringify(toFilterParams(searchForm.value, dateFields)) !== JSON.stringify(appliedFilters.value)
);
let loadRequestId = 0;

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
  loadRequestId += 1;
  const requestId = loadRequestId;
  const filters = toFilterParams(searchForm.value, dateFields);
  loading.value = true;
  statsLoading.value = withStats;
  try {
    const result = await fetchDomainPage({
      page: currentPage.value,
      page_size: pageSize.value,
      with_stats: withStats ? 'true' : undefined,
      ...filters
    });
    if (requestId !== loadRequestId) return;
    rows.value = result.records;
    total.value = result.total;
    appliedFilters.value = filters;
    selectedDomains.value = [];
    if (withStats) stats.value = result.stats || null;
  } catch (error) {
    if (requestId === loadRequestId) {
      window.$message?.error(error instanceof Error ? error.message : '加载过期域名失败');
    }
  } finally {
    if (requestId === loadRequestId) {
      loading.value = false;
      statsLoading.value = false;
    }
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

function handleSelectionChange(selection: DomainRecord[]) {
  selectedDomains.value = selection.map(row => row.domain);
}

async function handleQualifySelected() {
  if (busy.value || !selectedDomains.value.length) return;
  const domains = [...selectedDomains.value];
  qualifying.value = true;
  try {
    const result = await qualifySelectedDomains(domains);
    const message = `已加入符合列表 ${result.added} 个域名${result.skipped ? `，跳过 ${result.skipped} 个（已加入、已注册或不存在）` : ''}`;
    if (result.added) window.$message?.success(`${message}，监控任务运行时将自动纳入监控`);
    else window.$message?.info(message);
    await loadDomains(true);
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加入符合列表失败');
  } finally {
    qualifying.value = false;
  }
}

async function handleDelete(scope: DeleteScope) {
  if (busy.value) return;
  if (scope === 'selected' && !selectedDomains.value.length) return;
  if (scope === 'filtered' && (!hasAppliedFilters.value || filtersChanged.value)) return;
  const domains = [...selectedDomains.value];
  const filters = { ...appliedFilters.value };
  const titles = { all: '清空数据', selected: '批量删除', filtered: '删除数据' };
  deleting.value = scope;
  try {
    const count =
      scope === 'selected'
        ? domains.length
        : (await fetchDomainPage({ ...(scope === 'filtered' ? filters : {}), page: 1, page_size: 1 })).total;
    if (!count) {
      window.$message?.info('没有可删除的数据');
      return;
    }
    const descriptions = {
      all: `将清空过期域名列表的全部 ${count} 条数据，不受当前筛选条件限制。`,
      selected: `将删除已勾选的 ${count} 条域名数据。`,
      filtered: `将删除当前已搜索筛选条件匹配的全部 ${count} 条数据，包含所有分页。`
    };
    try {
      await ElMessageBox.confirm(
        `${descriptions[scope]}对应查询结果也会一并删除，此操作不可恢复。确定继续吗？`,
        titles[scope],
        {
          confirmButtonText: scope === 'all' ? '确认清空' : '确认删除',
          cancelButtonText: '取消',
          type: 'warning',
          closeOnClickModal: false
        }
      );
    } catch {
      return;
    }
    let result: { deleted: number };
    if (scope === 'all') result = await clearExpiredDomains();
    else if (scope === 'selected') result = await deleteSelectedDomains(domains);
    else result = await deleteFilteredDomains(filters);
    window.$message?.success(`已删除 ${result.deleted} 条域名数据`);
    currentPage.value = 1;
    await loadDomains(true);
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '删除域名数据失败');
  } finally {
    deleting.value = '';
  }
}

async function handleResetQueryTime() {
  try {
    await ElMessageBox.confirm(
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
            <ElButton :disabled="Boolean(deleting) || resetting || qualifying" @click="loadDomains(true)">
              <template #icon><icon-ic-round-refresh /></template>
              刷新
            </ElButton>
            <ElButton
              type="success"
              :loading="qualifying"
              :disabled="!selectedDomains.length || busy"
              @click="handleQualifySelected"
            >
              加入符合{{ selectedDomains.length ? `（${selectedDomains.length}）` : '' }}
            </ElButton>
            <ElButton
              type="warning"
              plain
              :loading="resetting"
              :disabled="Boolean(deleting) || loading || qualifying"
              @click="handleResetQueryTime"
            >
              重置数据
            </ElButton>
            <ElButton
              type="danger"
              plain
              :loading="deleting === 'selected'"
              :disabled="!selectedDomains.length || busy"
              @click="handleDelete('selected')"
            >
              批量删除{{ selectedDomains.length ? `（${selectedDomains.length}）` : '' }}
            </ElButton>
            <ElButton
              type="danger"
              plain
              :loading="deleting === 'filtered'"
              :disabled="!total || !hasAppliedFilters || filtersChanged || busy"
              :title="filtersChanged ? '筛选条件已修改，请先搜索' : '删除当前筛选结果的全部数据（所有分页）'"
              @click="handleDelete('filtered')"
            >
              删除数据
            </ElButton>
            <ElButton type="danger" :loading="deleting === 'all'" :disabled="busy" @click="handleDelete('all')">
              清空数据
            </ElButton>
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
        <ElTable
          v-loading="loading"
          height="100%"
          border
          :data="rows"
          row-key="domain"
          @selection-change="handleSelectionChange"
        >
          <ElTableColumn type="selection" width="48" fixed="left" />
          <ElTableColumn v-if="hasColumn('domain')" prop="domain" label="域名" min-width="180" fixed="left" />
          <ElTableColumn v-if="hasColumn('deletion_status')" prop="deletion_status" label="删除状态" width="100">
            <template #default="{ row }"><DomainStatusTag kind="deletion" :value="row.deletion_status" /></template>
          </ElTableColumn>
          <ElTableColumn v-if="hasColumn('creation_date')" prop="creation_date" label="注册时间" width="190">
            <template #default="{ row }">{{ formatDomainDateTime(row.creation_date) }}</template>
          </ElTableColumn>
          <ElTableColumn v-if="hasColumn('expiration_date')" prop="expiration_date" label="到期时间" width="190">
            <template #default="{ row }">{{ formatDomainDateTime(row.expiration_date) }}</template>
          </ElTableColumn>
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

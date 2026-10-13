<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { type BookedDomain, type DomainListStats, type DomainStatKey, fetchBookedDomains } from '@/service/api';
import { formatDateTime } from '@/utils/common';
import DomainStatusTag from '@/components/domain-status-tag.vue';
import DomainFilterPanel from '@/components/domain-filter-panel.vue';
import DomainStatsCards from '@/components/domain-stats-cards.vue';
import { type DateField, createDomainFilter, toFilterParams } from '@/components/domain-filter';

const dateFields: DateField[] = [{ key: 'booked', label: '预订时间', params: ['booked_start', 'booked_end'] }];

const loading = ref(false);
const records = ref<BookedDomain[]>([]);
const total = ref(0);
const stats = ref<DomainListStats | null>(null);
const statsLoading = ref(false);
const filters = ref(createDomainFilter(dateFields));
const pagination = reactive({ page: 1, pageSize: 20 });

const columnOptions = [
  ['domain', '域名'],
  ['wechat_status', '微信'],
  ['qq_status', 'QQ'],
  ['pollution_status', '污染'],
  ['blocked_status', '拦截'],
  ['blacklist_status', '黑名单'],
  ['filing_nature', '备案性质'],
  ['filing_info', '备案信息'],
  ['source', '数据来源'],
  ['scheme_name', '命中方案'],
  ['registrar_name', '预订商'],
  ['channel', '预订通道'],
  ['booking_result', '预订结果'],
  ['booked_at', '预订时间']
] as const;
const visibleColumns = ref<string[]>(columnOptions.map(([key]) => key));

function hasColumn(key: string) {
  return visibleColumns.value.includes(key);
}

/** withStats 为 true 时同时刷新统计卡片；翻页只换数据，不重复统计 */
async function loadData(withStats = false) {
  loading.value = true;
  statsLoading.value = withStats;
  try {
    const data = await fetchBookedDomains({
      ...pagination,
      with_stats: withStats ? 'true' : undefined,
      ...toFilterParams(filters.value, dateFields)
    });
    records.value = data.records;
    total.value = data.total;
    if (withStats) stats.value = data.stats || null;
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加载预订域名失败');
  } finally {
    loading.value = false;
    statsLoading.value = false;
  }
}

function search() {
  pagination.page = 1;
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

onMounted(() => loadData(true));
</script>

<template>
  <div class="domain-page">
    <DomainFilterPanel
      v-model:model="filters"
      :date-fields="dateFields"
      :loading="loading"
      keyword-placeholder="搜索域名 / 预订商 / 命中方案 / 通道 / 备案信息 / 来源"
      @search="search"
      @reset="reset"
    />

    <DomainStatsCards :stats="stats" :loading="statsLoading" :active="filters" @filter="handleStatFilter" />

    <ElCard class="table-card card-wrapper" shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-12px">
          <div class="flex items-center gap-8px">
            <span class="font-16px font-medium">预订列表</span>
            <ElTag type="success">{{ total }} 条</ElTag>
          </div>
          <div class="flex flex-wrap items-center justify-end gap-8px">
            <ElButton @click="loadData(true)">
              <template #icon><icon-ic-round-refresh /></template>
              刷新
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
        <ElTable v-loading="loading" height="100%" :data="records" border row-key="id">
          <ElTableColumn v-if="hasColumn('domain')" prop="domain" label="域名" min-width="180" fixed="left" />
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
          <ElTableColumn
            v-if="hasColumn('scheme_name')"
            prop="scheme_name"
            label="命中方案"
            min-width="140"
            show-overflow-tooltip
          />
          <ElTableColumn
            v-if="hasColumn('registrar_name')"
            prop="registrar_name"
            label="预订商"
            min-width="140"
            show-overflow-tooltip
          />
          <ElTableColumn v-if="hasColumn('channel')" prop="channel" label="预订通道" width="120">
            <template #default="{ row }">{{ row.channel }}号通道</template>
          </ElTableColumn>
          <ElTableColumn
            v-if="hasColumn('booking_result')"
            prop="booking_result"
            label="预订结果"
            min-width="220"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <span
                :class="
                  row.status === 'success'
                    ? 'text-green-600'
                    : row.status === 'failure'
                      ? 'text-red-500'
                      : 'text-orange-500'
                "
              >
                {{ row.booking_result }}
              </span>
            </template>
          </ElTableColumn>
          <ElTableColumn v-if="hasColumn('booked_at')" prop="booked_at" label="预订时间" width="190">
            <template #default="{ row }">{{ formatDateTime(row.booked_at) }}</template>
          </ElTableColumn>
        </ElTable>
      </div>
      <div class="pagination-bar">
        <ElPagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, prev, pager, next, sizes, jumper"
          @current-change="loadData()"
          @size-change="search"
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

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useDocumentVisibility, useIntervalFn } from '@vueuse/core';
import type { RouteKey } from '@elegant-router/types';
import {
  type DashboardChecks,
  type DashboardData,
  type NameValue,
  fetchDashboard,
  fetchDashboardRuntime
} from '@/service/api';
import { useAuthStore } from '@/store/modules/auth';
import { useThemeStore } from '@/store/modules/theme';
import type { ECOption } from '@/hooks/common/echarts';
import { useRouterPush } from '@/hooks/common/router';
import { formatDateTime } from '@/utils/common';
import DomainStatusTag from '@/components/domain-status-tag.vue';
import DashChart from './modules/dash-chart.vue';
import QuickEntry, { type EntryItem } from './modules/quick-entry.vue';

defineOptions({ name: 'HomeDashboard' });

/** 运行状态自动刷新间隔 */
const LIVE_INTERVAL = 15_000;
const PALETTE = ['#5b8ff9', '#5ad8a6', '#f6bd16', '#e8684a', '#6dc8ec', '#9270ca', '#ff9d4d', '#269a99', '#ff99c3'];

const router = useRouter();
const { routerPushByKey } = useRouterPush();
const authStore = useAuthStore();
const themeStore = useThemeStore();

const data = ref<DashboardData | null>(null);
const loading = ref(false);
const liveAt = ref<Date | null>(null);

async function loadAll(refresh = false) {
  loading.value = true;
  try {
    data.value = await fetchDashboard(refresh);
    liveAt.value = new Date();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加载控制台数据失败');
  } finally {
    loading.value = false;
  }
}

function sameJson(a: unknown, b: unknown) {
  return JSON.stringify(a) === JSON.stringify(b);
}

async function loadLive() {
  if (!data.value || loading.value) return;
  try {
    const live = await fetchDashboardRuntime();
    // 内容未变化时不替换，避免图表每次刷新都重播动画
    if (!sameJson(live.checks, data.value.checks)) data.value.checks = live.checks;
    data.value.runtime = live.runtime;
    data.value.alerts = live.alerts;
    data.value.activities = live.activities;
    liveAt.value = new Date();
  } catch {
    // 定时刷新失败时保留上一次数据，下个周期重试
  }
}

const visibility = useDocumentVisibility();
useIntervalFn(() => {
  if (visibility.value === 'visible') loadLive();
}, LIVE_INTERVAL);

loadAll();

const stats = computed(() => data.value?.stats);
const checks = computed(() => data.value?.checks);
const runtime = computed(() => data.value?.runtime);

function fmt(value?: number | null) {
  return (value ?? 0).toLocaleString('zh-CN');
}

function pct(part: number, total: number) {
  return total ? Math.round((part * 1000) / total) / 10 : 0;
}

function goto(key: string) {
  if (router.hasRoute(key)) routerPushByKey(key as RouteKey);
}

const greeting = computed(() => {
  const hour = new Date().getHours();
  if (hour < 6) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 13) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
});

const todayText = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'long'
});

/* ---------------- 状态文案 ---------------- */

const monitorStatusMap: Record<string, [string, 'success' | 'info' | 'warning' | 'danger']> = {
  running: ['运行中', 'success'],
  stopping: ['停止中', 'warning'],
  stopped: ['已停止', 'info'],
  error: ['异常', 'danger']
};
const taskStatusMap: Record<string, [string, 'success' | 'info' | 'warning' | 'danger' | 'primary']> = {
  created: ['待启动', 'primary'],
  running: ['运行中', 'success'],
  completed: ['已完成', 'info'],
  stopped: ['已停止', 'info'],
  failed: ['失败', 'danger']
};
const runStatusMap: Record<string, [string, 'success' | 'info' | 'warning' | 'danger' | 'primary']> = {
  queued: ['排队中', 'primary'],
  running: ['采集中', 'warning'],
  success: ['成功', 'success'],
  failure: ['失败', 'danger']
};
const activityColor: Record<string, string> = {
  success: 'var(--el-color-success)',
  error: 'var(--el-color-danger)',
  warning: 'var(--el-color-warning)',
  info: 'var(--el-color-primary)'
};

const monitorStatus = computed(() => monitorStatusMap[runtime.value?.monitor.status || 'stopped'] || ['未知', 'info']);
const queryTask = computed(() => runtime.value?.queryTask);
const queryTaskStatus = computed(() => taskStatusMap[queryTask.value?.status || ''] || ['未知', 'info']);
const queryTaskPercent = computed(() => {
  const task = queryTask.value;
  if (!task?.total_count) return 0;
  return Math.min(100, pct(task.processed_count, task.total_count));
});
const latestRun = computed(() => runtime.value?.collect.latest);
const nextSchedule = computed(() => {
  const list = (runtime.value?.collect.schedules || []).filter(item => item.enabled && item.next_run);
  return [...list].sort((a, b) => a.next_run.localeCompare(b.next_run))[0];
});
const registration = computed(() => {
  const counts = runtime.value?.registration || {};
  const success = counts.success || 0;
  const total = Object.values(counts).reduce((sum, value) => sum + value, 0);
  return { success, failed: total - success, total };
});

/* ---------------- 指标卡片 ---------------- */

interface KpiCard {
  key: string;
  title: string;
  value: number;
  icon: string;
  color: string;
  route?: string;
  extra: string;
  /** 进度条百分比，未设置时不显示 */
  percent?: number;
}

const kpiCards = computed<KpiCard[]>(() => {
  const s = stats.value;
  const c = checks.value;
  if (!s || !c) return [];
  const diff = s.todayJoined - s.yesterdayJoined;
  const sourceText = s.bySource.map(item => `${item.name} ${fmt(item.value)}`).join(' · ');
  const reg = registration.value;
  return [
    {
      key: 'total',
      title: '过期域名总量',
      value: s.total,
      icon: 'mdi:database-outline',
      color: '#5b8ff9',
      route: 'domain-management_expired',
      extra: sourceText || `共 ${s.suffixCount} 种后缀`
    },
    {
      key: 'today',
      title: '今日新增',
      value: s.todayJoined,
      icon: 'mdi:tray-arrow-down',
      color: '#5ad8a6',
      route: 'runtime-control_expired-collection',
      extra: `较昨日 ${diff >= 0 ? '+' : ''}${fmt(diff)}，今日采集 ${runtime.value?.collect.todayRuns || 0} 次`
    },
    {
      key: 'queried',
      title: '已查询域名',
      value: s.queried,
      icon: 'mdi:text-search',
      color: '#6dc8ec',
      route: 'runtime-control_query-tasks',
      extra: `查询覆盖率 ${s.queryRate}%`,
      percent: s.queryRate
    },
    {
      key: 'pending',
      title: '待查询域名',
      value: s.pending,
      icon: 'mdi:timer-sand',
      color: '#f6bd16',
      route: 'runtime-control_query-tasks',
      extra: `占总量 ${pct(s.pending, s.total)}%`
    },
    {
      key: 'qualified',
      title: '符合域名',
      value: c.qualified,
      icon: 'mdi:check-decagram-outline',
      color: '#30bf78',
      route: 'domain-management_qualified',
      extra: `符合率 ${c.qualifiedRate}%，今日新增 ${fmt(c.todayQualified)}`,
      percent: c.qualifiedRate
    },
    {
      key: 'unqualified',
      title: '不符合域名',
      value: c.unqualified,
      icon: 'mdi:close-circle-outline',
      color: '#e8684a',
      route: 'domain-management_unqualified',
      extra: `今日检测 ${fmt(c.todayChecked)} 个`
    },
    {
      key: 'kicked',
      title: '踢出域名',
      value: c.kicked,
      icon: 'mdi:web-remove',
      color: '#9270ca',
      route: 'domain-management_kicked',
      extra: `监控中 ${fmt(runtime.value?.monitor.domains.monitoring)} 个`
    },
    {
      key: 'registered',
      title: '注册成功',
      value: reg.success,
      icon: 'mdi:trophy-outline',
      color: '#ff9d4d',
      route: 'domain-management_registered',
      extra: `共提交 ${fmt(reg.total)} 次，失败 ${fmt(reg.failed)} 次`
    }
  ];
});

/* ---------------- 快速入口 ---------------- */

const entryItems = computed<EntryItem[]>(() => {
  const s = stats.value;
  const c = checks.value;
  const r = runtime.value;
  return [
    {
      key: 'domain-management_expired',
      title: '过期域名列表',
      desc: `共 ${fmt(s?.total)} 个，待查询 ${fmt(s?.pending)} 个`,
      icon: 'mdi:calendar-clock',
      color: '#5b8ff9'
    },
    {
      key: 'domain-management_qualified',
      title: '符合域名列表',
      desc: '通过备案、拦截等条件筛选的域名',
      icon: 'mdi:check-decagram-outline',
      color: '#30bf78',
      badge: c?.qualified ? fmt(c.qualified) : undefined,
      badgeType: 'success'
    },
    {
      key: 'domain-management_unqualified',
      title: '不符合域名列表',
      desc: `${fmt(c?.unqualified)} 个，可按原因筛选`,
      icon: 'mdi:close-circle-outline',
      color: '#e8684a'
    },
    {
      key: 'domain-management_kicked',
      title: '踢出域名列表',
      desc: `${fmt(c?.kicked)} 个已进入删除监控`,
      icon: 'mdi:web-remove',
      color: '#9270ca'
    },
    {
      key: 'runtime-control_expired-collection',
      title: '数据采集',
      desc: nextSchedule.value
        ? `下次定时：${nextSchedule.value.next_run}`
        : `今日采集 ${r?.collect.todayRuns || 0} 次`,
      icon: 'mdi:database-import-outline',
      color: '#6dc8ec'
    },
    {
      key: 'runtime-control_query-tasks',
      title: '查询任务',
      desc: queryTask.value ? `#${queryTask.value.id} ${queryTask.value.name}` : '创建查询任务筛选域名',
      icon: 'mdi:text-search',
      color: '#f6bd16',
      badge: queryTask.value ? queryTaskStatus.value[0] : undefined,
      badgeType: queryTaskStatus.value[1]
    },
    {
      key: 'runtime-control_monitor-tasks',
      title: '监控任务',
      desc: `已检测 ${fmt(r?.monitor.checked)} 次，注册接口 ${r?.registrarApis.enabled || 0} 个`,
      icon: 'mdi:monitor-eye',
      color: '#ff9d4d',
      badge: monitorStatus.value[0],
      badgeType: monitorStatus.value[1]
    },
    {
      key: 'manage_user',
      title: '用户管理',
      desc: '账号、角色分配与状态',
      icon: 'ic:round-manage-accounts',
      color: '#269a99'
    },
    {
      key: 'manage_role',
      title: '角色管理',
      desc: '菜单、按钮权限与首页',
      icon: 'carbon:user-role',
      color: '#ff99c3'
    },
    {
      key: 'manage_menu',
      title: '菜单管理',
      desc: '菜单名称、图标与排序',
      icon: 'material-symbols:route',
      color: '#8c8c8c'
    }
  ];
});

/* ---------------- 图表 ---------------- */

const pieBorder = computed(() => (themeStore.darkMode ? '#18181c' : '#fff'));

function ringOption(items: NameValue[], name: string, colors = PALETTE): ECOption {
  const total = items.reduce((sum, item) => sum + item.value, 0);
  return {
    color: colors,
    tooltip: { trigger: 'item', formatter: '{b}：{c}（{d}%）' },
    title: {
      text: fmt(total),
      subtext: name,
      left: 'center',
      top: '32%',
      textStyle: { fontSize: 20, fontWeight: 600 },
      subtextStyle: { fontSize: 12 }
    },
    legend: { bottom: 0, left: 'center', type: 'scroll', itemWidth: 10, itemHeight: 10 },
    series: [
      {
        name,
        type: 'pie',
        radius: ['48%', '68%'],
        center: ['50%', '42%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 6, borderColor: pieBorder.value, borderWidth: 2 },
        label: { show: false },
        data: items
      }
    ]
  };
}

function barOption(items: NameValue[], color: string, horizontal = false): ECOption {
  const names = items.map(item => item.name);
  const category = { type: 'category' as const, data: names, axisTick: { show: false }, axisLabel: { interval: 0 } };
  const value = { type: 'value' as const, splitLine: { lineStyle: { type: 'dashed' as const } } };
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: horizontal ? 56 : 12, top: 16, bottom: 8, containLabel: true },
    xAxis: horizontal ? value : category,
    yAxis: horizontal ? { ...category, inverse: true } : value,
    series: [
      {
        name: '域名数',
        type: 'bar',
        barMaxWidth: 28,
        itemStyle: { color, borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] },
        label: horizontal ? { show: true, position: 'right' } : { show: false },
        data: items.map(item => item.value)
      }
    ]
  };
}

const trendOption = computed<ECOption>(() => {
  const trend = stats.value?.trend;
  const days = trend?.days || [];
  const series = trend?.series || [];
  return {
    color: PALETTE,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, right: 0 },
    grid: { left: 8, right: 12, top: 36, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: days.map(day => day.slice(5)), axisTick: { show: false } },
    yAxis: { type: 'value', splitLine: { lineStyle: { type: 'dashed' } } },
    series: series.map(item => ({
      name: item.name,
      type: 'bar',
      stack: 'total',
      barMaxWidth: 36,
      emphasis: { focus: 'series' },
      data: item.data
    }))
  };
});

function checkItems(c?: DashboardChecks): NameValue[] {
  if (!c) return [];
  return [
    { name: '符合', value: c.qualified },
    { name: '不符合', value: c.unqualified },
    { name: '踢出', value: c.kicked }
  ];
}

const resultOption = computed(() => ringOption(checkItems(checks.value), '已检测', ['#30bf78', '#e8684a', '#9270ca']));
const reasonOption = computed(() => barOption(checks.value?.reasons || [], '#e8684a', true));
const filingOption = computed(() => ringOption(checks.value?.filingNature || [], '备案性质'));
const suffixOption = computed(() =>
  barOption(
    (stats.value?.bySuffix || []).map(item => ({ ...item, name: item.name === '其他' ? item.name : `.${item.name}` })),
    '#5b8ff9'
  )
);
const lengthOption = computed(() => barOption(stats.value?.byLength || [], '#5ad8a6'));
const kindOption = computed(() => ringOption(stats.value?.byKind || [], '域名组成'));

const deletionList = computed(() => {
  const items = checks.value?.deletionStatus || [];
  const total = items.reduce((sum, item) => sum + item.value, 0);
  return items.map(item => ({ ...item, percent: pct(item.value, total) }));
});
const riskList = computed(() => {
  const total = checks.value?.total || 0;
  return (checks.value?.risks || []).map(item => ({ ...item, percent: pct(item.value, total) }));
});
</script>

<template>
  <div v-loading="loading && !data" class="dashboard">
    <!-- 顶部欢迎栏 -->
    <ElCard class="card-wrapper" shadow="never">
      <div class="welcome">
        <div class="flex items-center gap-14px">
          <div class="welcome-icon">
            <SvgIcon icon="mdi:view-dashboard-outline" />
          </div>
          <div>
            <h2 class="welcome-title">{{ greeting }}，{{ authStore.userInfo.userName || '管理员' }}</h2>
            <p class="welcome-desc">
              {{ todayText }}
              <template v-if="stats">
                · 统计更新于 {{ formatDateTime(stats.generatedAt) }}，运行状态每 15 秒自动刷新
              </template>
            </p>
          </div>
        </div>
        <div class="welcome-status">
          <div class="status-item">
            <span class="status-label">监控</span>
            <ElTag :type="monitorStatus[1]" size="small" disable-transitions>{{ monitorStatus[0] }}</ElTag>
          </div>
          <div class="status-item">
            <span class="status-label">查询任务</span>
            <ElTag v-if="queryTask" :type="queryTaskStatus[1]" size="small" disable-transitions>
              {{ queryTaskStatus[0] }}
            </ElTag>
            <span v-else class="text-12px text-gray">暂无</span>
          </div>
          <div class="status-item">
            <span class="status-label">下次采集</span>
            <span class="text-13px">{{ nextSchedule?.next_run || '未设置' }}</span>
          </div>
          <ElButton :loading="loading" @click="loadAll(true)">
            <template #icon><icon-ic-round-refresh /></template>
            刷新统计
          </ElButton>
        </div>
      </div>
    </ElCard>

    <template v-if="data">
      <!-- 待处理提醒 -->
      <div v-if="data.alerts.length" class="alerts">
        <ElAlert
          v-for="item in data.alerts"
          :key="item.title"
          :type="item.level"
          :closable="false"
          show-icon
          class="alert-item"
          @click="goto(item.route)"
        >
          <template #title>
            <span class="font-600">{{ item.title }}</span>
            <span class="ml-8px opacity-80">{{ item.desc }}</span>
            <span v-if="router.hasRoute(item.route)" class="alert-link">去处理 →</span>
          </template>
        </ElAlert>
      </div>

      <!-- 核心指标 -->
      <div class="kpi-grid">
        <div
          v-for="card in kpiCards"
          :key="card.key"
          class="kpi-card"
          :class="{ clickable: card.route && router.hasRoute(card.route) }"
          @click="card.route && goto(card.route)"
        >
          <div class="flex items-start justify-between">
            <div class="min-w-0">
              <div class="kpi-title">{{ card.title }}</div>
              <div class="kpi-value">{{ fmt(card.value) }}</div>
            </div>
            <span class="kpi-icon" :style="{ color: card.color, backgroundColor: `${card.color}1a` }">
              <SvgIcon :icon="card.icon" />
            </span>
          </div>
          <ElProgress
            v-if="card.percent !== undefined"
            :percentage="Math.min(100, card.percent)"
            :color="card.color"
            :show-text="false"
            :stroke-width="4"
            class="mt-8px"
          />
          <div class="kpi-extra" :title="card.extra">{{ card.extra }}</div>
        </div>
      </div>

      <!-- 快速入口 -->
      <QuickEntry :items="entryItems" />

      <!-- 采集趋势 + 运行状态 -->
      <ElRow :gutter="12">
        <ElCol :lg="15" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>近 14 天入库趋势</span>
                <span class="card-sub">按加入日期、数据来源统计</span>
              </div>
            </template>
            <DashChart :option="trendOption" height="390px" />
          </ElCard>
        </ElCol>
        <ElCol :lg="9" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>运行状态</span>
                <span v-if="liveAt" class="card-sub">{{ liveAt.toLocaleTimeString('zh-CN') }} 更新</span>
              </div>
            </template>
            <div class="runtime">
              <!-- 数据采集 -->
              <div class="runtime-block">
                <div class="runtime-head">
                  <span class="runtime-name">
                    <SvgIcon icon="mdi:database-import-outline" />
                    数据采集
                  </span>
                  <ElButton link type="primary" size="small" @click="goto('runtime-control_expired-collection')">
                    详情
                  </ElButton>
                </div>
                <div class="mini-stats">
                  <div>
                    <b>{{ runtime?.collect.todayRuns || 0 }}</b>
                    <span>今日采集</span>
                  </div>
                  <div>
                    <b>{{ fmt(runtime?.collect.todayDownloaded) }}</b>
                    <span>今日下载</span>
                  </div>
                  <div>
                    <b>{{ fmt(runtime?.collect.todayInserted) }}</b>
                    <span>今日新增</span>
                  </div>
                </div>
                <div v-for="item in runtime?.collect.schedules" :key="item.id" class="runtime-line">
                  <span class="truncate">{{ item.name }}（每日 {{ item.run_time }}）</span>
                  <span v-if="item.enabled" class="text-gray">下次 {{ item.next_run.slice(5) }}</span>
                  <ElTag v-else type="info" size="small" disable-transitions>已停用</ElTag>
                </div>
                <div v-if="latestRun" class="runtime-line">
                  <span class="truncate">
                    最近：{{ latestRun.source_name }} · {{ formatDateTime(latestRun.started_at) }}
                  </span>
                  <ElTag :type="runStatusMap[latestRun.status]?.[1] || 'info'" size="small" disable-transitions>
                    {{ runStatusMap[latestRun.status]?.[0] || latestRun.status }}
                  </ElTag>
                </div>
              </div>

              <!-- 查询任务 -->
              <div class="runtime-block">
                <div class="runtime-head">
                  <span class="runtime-name">
                    <SvgIcon icon="mdi:text-search" />
                    查询任务
                  </span>
                  <ElButton link type="primary" size="small" @click="goto('runtime-control_query-tasks')">
                    详情
                  </ElButton>
                </div>
                <template v-if="queryTask">
                  <div class="runtime-line">
                    <span class="truncate">#{{ queryTask.id }} {{ queryTask.name }}</span>
                    <ElTag :type="queryTaskStatus[1]" size="small" disable-transitions>{{ queryTaskStatus[0] }}</ElTag>
                  </div>
                  <ElProgress :percentage="queryTaskPercent" :stroke-width="8" class="my-6px" />
                  <div class="runtime-line text-gray">
                    <span>
                      已处理 {{ fmt(queryTask.processed_count) }} / {{ fmt(queryTask.total_count) }} · 符合
                      {{ fmt(queryTask.qualified_count) }} · 不符合 {{ fmt(queryTask.unqualified_count) }}
                    </span>
                  </div>
                  <div v-if="queryTask.status === 'running' && queryTask.current_domain" class="runtime-line text-gray">
                    <span class="truncate">正在查询：{{ queryTask.current_domain }}</span>
                  </div>
                </template>
                <div v-else class="runtime-line text-gray">暂无查询任务</div>
              </div>

              <!-- 监控任务 -->
              <div class="runtime-block">
                <div class="runtime-head">
                  <span class="runtime-name">
                    <SvgIcon icon="mdi:monitor-eye" />
                    监控任务
                    <ElTag :type="monitorStatus[1]" size="small" disable-transitions>{{ monitorStatus[0] }}</ElTag>
                  </span>
                  <ElButton link type="primary" size="small" @click="goto('runtime-control_monitor-tasks')">
                    详情
                  </ElButton>
                </div>
                <div class="mini-stats">
                  <div>
                    <b>{{ fmt(runtime?.monitor.checked) }}</b>
                    <span>已检测</span>
                  </div>
                  <div>
                    <b>{{ fmt(runtime?.monitor.available) }}</b>
                    <span>可注册</span>
                  </div>
                  <div>
                    <b>{{ fmt(runtime?.monitor.registered) }}</b>
                    <span>已注册</span>
                  </div>
                </div>
                <div class="runtime-line text-gray">
                  <span>
                    注册商接口 {{ runtime?.registrarApis.enabled || 0 }} /
                    {{ runtime?.registrarApis.total || 0 }} 个启用
                  </span>
                </div>
                <div v-if="runtime?.monitor.currentDomain" class="runtime-line text-gray">
                  <span class="truncate">当前：{{ runtime.monitor.currentDomain }}</span>
                </div>
              </div>
            </div>
          </ElCard>
        </ElCol>
      </ElRow>

      <!-- 查询结果分析 -->
      <ElRow :gutter="12">
        <ElCol :xl="6" :lg="12" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>查询结果</span>
                <span class="card-sub">符合率 {{ checks?.qualifiedRate }}%</span>
              </div>
            </template>
            <DashChart :option="resultOption" height="260px" />
          </ElCard>
        </ElCol>
        <ElCol :xl="6" :lg="12" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>不符合原因</span>
                <span class="card-sub">TOP 8</span>
              </div>
            </template>
            <DashChart v-if="checks?.reasons.length" :option="reasonOption" height="260px" />
            <ElEmpty v-else description="暂无数据" :image-size="80" />
          </ElCard>
        </ElCol>
        <ElCol :xl="6" :lg="12" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>备案性质</span>
                <span class="card-sub">已检测域名</span>
              </div>
            </template>
            <DashChart v-if="checks?.filingNature.length" :option="filingOption" height="260px" />
            <ElEmpty v-else description="暂无数据" :image-size="80" />
          </ElCard>
        </ElCol>
        <ElCol :xl="6" :lg="12" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>删除状态 / 风险标签</span>
              </div>
            </template>
            <div class="bar-list">
              <div class="bar-list-title">删除状态</div>
              <div v-for="item in deletionList" :key="item.name" class="bar-row">
                <span class="bar-name">{{ item.name }}</span>
                <ElProgress :percentage="item.percent" :stroke-width="8" :show-text="false" class="flex-1" />
                <span class="bar-value">{{ fmt(item.value) }}</span>
              </div>
              <div v-if="!deletionList.length" class="text-12px text-gray">暂无数据</div>
              <div class="bar-list-title mt-12px">风险标签（命中数）</div>
              <div v-for="item in riskList" :key="item.name" class="bar-row">
                <span class="bar-name">{{ item.name }}</span>
                <ElProgress
                  :percentage="item.percent"
                  :stroke-width="8"
                  :show-text="false"
                  status="exception"
                  class="flex-1"
                />
                <span class="bar-value">{{ fmt(item.value) }}</span>
              </div>
            </div>
          </ElCard>
        </ElCol>
      </ElRow>

      <!-- 域名结构分布 -->
      <ElRow :gutter="12">
        <ElCol :lg="10" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>后缀分布</span>
                <span class="card-sub">TOP 10，共 {{ stats?.suffixCount }} 种后缀</span>
              </div>
            </template>
            <DashChart :option="suffixOption" height="280px" />
          </ElCard>
        </ElCol>
        <ElCol :lg="8" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>长度分布</span>
                <span class="card-sub">主体字符数</span>
              </div>
            </template>
            <DashChart :option="lengthOption" height="280px" />
          </ElCard>
        </ElCol>
        <ElCol :lg="6" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>域名组成</span>
              </div>
            </template>
            <DashChart :option="kindOption" height="280px" />
          </ElCard>
        </ElCol>
      </ElRow>

      <!-- 最新符合域名 + 最近动态 -->
      <ElRow :gutter="12">
        <ElCol :lg="12" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>最新符合域名</span>
                <ElButton link type="primary" size="small" @click="goto('domain-management_qualified')">
                  查看全部
                </ElButton>
              </div>
            </template>
            <ElTable :data="checks?.latestQualified || []" size="small" empty-text="暂无符合条件的域名">
              <ElTableColumn prop="domain" label="域名" min-width="150" show-overflow-tooltip />
              <ElTableColumn prop="expiration_date" label="到期时间" width="105" />
              <ElTableColumn prop="deletion_status" label="删除状态" width="90">
                <template #default="{ row }"><DomainStatusTag kind="deletion" :value="row.deletion_status" /></template>
              </ElTableColumn>
              <ElTableColumn prop="filing_nature" label="备案性质" width="90">
                <template #default="{ row }"><DomainStatusTag kind="filing" :value="row.filing_nature" /></template>
              </ElTableColumn>
              <ElTableColumn label="检测时间" width="150">
                <template #default="{ row }">{{ formatDateTime(row.checked_at) }}</template>
              </ElTableColumn>
            </ElTable>
          </ElCard>
        </ElCol>
        <ElCol :lg="12" :sm="24" class="col-gap">
          <ElCard class="h-full card-wrapper" shadow="never">
            <template #header>
              <div class="card-header">
                <span>最近动态</span>
                <span class="card-sub">采集记录、查询与监控告警</span>
              </div>
            </template>
            <div class="activity">
              <ElTimeline v-if="data.activities.length">
                <ElTimelineItem
                  v-for="(item, index) in data.activities"
                  :key="index"
                  :color="activityColor[item.level] || activityColor.info"
                  :timestamp="formatDateTime(item.time)"
                  placement="top"
                >
                  <div class="activity-line">
                    <ElTag size="small" type="info" disable-transitions>{{ item.type }}</ElTag>
                    <span v-if="item.title" class="font-600">{{ item.title }}</span>
                    <span class="activity-msg">{{ item.message }}</span>
                  </div>
                </ElTimelineItem>
              </ElTimeline>
              <ElEmpty v-else description="暂无动态" :image-size="80" />
            </div>
          </ElCard>
        </ElCol>
      </ElRow>
    </template>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 400px;
}

.dashboard :deep(.el-row) {
  row-gap: 12px;
}

.col-gap :deep(.el-card__body) {
  padding: 14px 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.card-sub {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: normal;
}

.text-gray {
  color: var(--el-text-color-secondary);
}

/* 欢迎栏 */
.welcome {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.welcome-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: linear-gradient(135deg, var(--el-color-primary-light-3), var(--el-color-primary));
  color: #fff;
  font-size: 28px;
}

.welcome-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.welcome-desc {
  margin: 4px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.welcome-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 20px;
}

.status-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

/* 提醒 */
.alerts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.alert-item {
  cursor: pointer;
}

.alert-link {
  margin-left: 12px;
  color: var(--el-color-primary);
}

/* 指标卡片 */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 12px;
}

.kpi-card {
  display: flex;
  flex-direction: column;
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  transition:
    box-shadow 0.2s,
    transform 0.2s;
}

.kpi-card.clickable {
  cursor: pointer;
}

.kpi-card.clickable:hover {
  box-shadow: var(--el-box-shadow-light);
  transform: translateY(-2px);
}

.kpi-title {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.kpi-value {
  margin-top: 6px;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.kpi-icon {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  font-size: 24px;
}

.kpi-extra {
  overflow: hidden;
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* 运行状态 */
.runtime {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.runtime-block + .runtime-block {
  padding-top: 12px;
  border-top: 1px dashed var(--el-border-color);
}

.runtime-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.runtime-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
}

.runtime-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 24px;
  font-size: 12px;
}

.mini-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-bottom: 6px;
}

.mini-stats > div {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 6px 4px;
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
}

.mini-stats b {
  font-size: 16px;
  font-variant-numeric: tabular-nums;
}

.mini-stats span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

/* 条形列表 */
.bar-list-title {
  margin-bottom: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 600;
}

.bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
}

.bar-name {
  flex-shrink: 0;
  width: 64px;
}

.bar-value {
  flex-shrink: 0;
  width: 56px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* 动态 */
.activity {
  max-height: 340px;
  overflow-y: auto;
  padding: 4px 4px 0 2px;
}

.activity-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.activity-msg {
  color: var(--el-text-color-regular);
  word-break: break-all;
}
</style>

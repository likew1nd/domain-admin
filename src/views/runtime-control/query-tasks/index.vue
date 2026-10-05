<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue';
import {
  type CreateQueryTaskPayload,
  type DomainSuffix,
  type InterceptCheckItem,
  type QueryExceptionScheme,
  type QueryLog,
  type QueryTask,
  createQueryTask,
  fetchDomainSuffixes,
  fetchInterceptKeyStatus,
  fetchQueryLogs,
  fetchQuerySettings,
  fetchQueryTasks,
  previewQueryTask,
  saveInterceptKey,
  saveQuerySettings,
  stopQueryTask
} from '@/service/api';
import { $t } from '@/locales';
import { compositionOptions } from '@/components/domain-filter';

const suffixes = ref<DomainSuffix[]>([]);
const suffixGroupTab = ref<'all' | DomainSuffix['group']>('all');
const tasks = ref<QueryTask[]>([]);
const logs = ref<QueryLog[]>([]);
const logViewport = ref<HTMLElement | null>(null);
const logsLoading = ref(false);
const logTaskId = ref<number | null>(null);
const logAtLatest = ref(true);
const activeView = ref<'query' | 'logs'>('query');
const dataRequestPending = ref(false);
const submitting = ref(false);
const stopping = ref(false);
const savingSettings = ref(false);
const registrationRange = ref<string[]>([]);
const expirationRange = ref<string[]>([]);
const previewTotal = ref<number | null>(null);
const previewLoading = ref(false);
const lengthInput = ref('');
const excludeCharsInput = ref('');
const interceptKeyInput = ref('');
const interceptKeyConfigured = ref(false);
const savingInterceptKey = ref(false);
const apihzKeyInput = ref('');
const apihzKeyConfigured = ref(false);

type ExceptionSchemeDraft = Omit<QueryExceptionScheme, 'lengths' | 'suffixes' | 'patterns' | 'contains'> & {
  lengths: string;
  suffixes: string;
  patterns: string;
  contains: string;
};

function createExceptionScheme(index = 1): QueryExceptionScheme {
  return { name: `方案 ${index}`, enabled: true, logic: 'or', lengths: [], suffixes: [], patterns: [], contains: [] };
}

function createExceptionSchemeDraft(scheme: QueryExceptionScheme, index: number): ExceptionSchemeDraft {
  return {
    name: scheme.name || `方案 ${index}`,
    enabled: scheme.enabled,
    logic: scheme.logic || 'or',
    lengths: scheme.lengths.join(','),
    suffixes: scheme.suffixes.join(','),
    patterns: scheme.patterns.join(','),
    contains: scheme.contains.join(',')
  };
}

const exceptionSchemeDrafts = ref<ExceptionSchemeDraft[]>([createExceptionSchemeDraft(createExceptionScheme(), 1)]);
const defaultForm: CreateQueryTaskPayload = {
  domain_info_source: 'whois',
  apihz_id: '',
  apihz_key: '',
  lengths: [],
  suffixes: [],
  exclude_chars: [],
  domain_composition: [],
  delete_type: 'expired',
  expiration_start: '',
  expiration_end: '',
  registration_start: '',
  registration_end: '',
  exceptions: {
    enabled: false,
    lengths: [],
    suffixes: [],
    patterns: [],
    contains: [],
    schemes: [createExceptionScheme()]
  },
  threads: 5,
  whois_retries: 2,
  icp_retries: 2,
  qq_retries: 1,
  wechat_retries: 1,
  douyin_retries: 1,
  blocked_retries: 2,
  pollution_retries: 2,
  blacklist_retries: 2,
  intercept_checks: [],
  continuous: true,
  proxy_mode: 'direct',
  proxy_endpoint: '',
  proxy_max_requests: 50,
  proxy_stages: ['icp']
};
function freshForm() {
  return {
    ...defaultForm,
    lengths: [...defaultForm.lengths],
    suffixes: [...defaultForm.suffixes],
    exclude_chars: [...defaultForm.exclude_chars],
    domain_composition: [...defaultForm.domain_composition],
    proxy_stages: [...defaultForm.proxy_stages],
    intercept_checks: [...defaultForm.intercept_checks],
    exceptions: {
      ...defaultForm.exceptions,
      lengths: [...defaultForm.exceptions.lengths],
      suffixes: [...defaultForm.exceptions.suffixes],
      patterns: [...defaultForm.exceptions.patterns],
      contains: [...defaultForm.exceptions.contains],
      schemes: defaultForm.exceptions.schemes.map(scheme => ({
        ...scheme,
        lengths: [...scheme.lengths],
        suffixes: [...scheme.suffixes],
        patterns: [...scheme.patterns],
        contains: [...scheme.contains]
      }))
    }
  };
}
const form = reactive<CreateQueryTaskPayload>(freshForm());

const groupNames = ['two', 'three', 'four', 'chinese', 'other'] as const;
const groupLabels: Record<(typeof groupNames)[number], string> = {
  two: '双字符后缀',
  three: '三字符后缀',
  four: '四字符后缀',
  chinese: '中文后缀',
  other: '其他后缀'
};
const groupedSuffixes = computed(() =>
  groupNames.map(group => ({
    group,
    label: groupLabels[group],
    items: suffixes.value.filter(item => item.group === group)
  }))
);
const runningTask = computed(() => tasks.value.find(task => task.status === 'running' || task.status === 'created'));
const statusTask = computed(() => runningTask.value || tasks.value[0]);
const proxyStatsTask = computed(() => statusTask.value);
const pageStatus = computed(() => {
  const task = statusTask.value;
  return task ? statusLabel(task.status) : $t('page.runtime.queryTasks.notStarted');
});
const pageStatusType = computed<UI.ThemeColor>(() => {
  const task = statusTask.value;
  return task ? statusType(task.status) : 'info';
});
const proxyApiEnabled = computed(() => form.proxy_mode === 'api');
const deleteTypeValue = computed({
  get: () => (form.delete_type === 'all' ? '' : form.delete_type),
  set: value => {
    form.delete_type = value || 'all';
  }
});
const interceptOptions: { value: InterceptCheckItem; label: string }[] = [
  { value: 'wechat', label: '微信' },
  { value: 'qq', label: 'QQ' },
  { value: 'pollution', label: '污染' },
  { value: 'blocked', label: '拦截' },
  { value: 'blacklist', label: '黑名单' }
];
const proxyStageOptions = [{ value: 'rdap', label: 'RDAP' }, { value: 'icp', label: '备案' }, ...interceptOptions];
const retryItems = [
  { key: 'whois_retries', label: 'whoisRetries', short: 'WHOIS' },
  { key: 'icp_retries', label: 'icpRetries', short: '备案' },
  { key: 'wechat_retries', label: 'wechatRetries', short: '微信' },
  { key: 'qq_retries', label: 'qqRetries', short: 'QQ' },
  { key: 'pollution_retries', label: 'pollutionRetries', short: '污染' },
  { key: 'blocked_retries', label: 'blockedRetries', short: '拦截' },
  { key: 'blacklist_retries', label: 'blacklistRetries', short: '黑名单' }
] as const;
const interceptAllChecked = computed({
  get: () => form.intercept_checks.length === interceptOptions.length,
  set: value => {
    form.intercept_checks = value ? interceptOptions.map(item => item.value) : [];
  }
});
const interceptIndeterminate = computed(
  () => form.intercept_checks.length > 0 && form.intercept_checks.length < interceptOptions.length
);

function groupValues(group: DomainSuffix['group']) {
  return suffixes.value.filter(item => item.group === group).map(item => item.suffix);
}

function selectGroup(group: DomainSuffix['group'], mode: 'all' | 'invert') {
  const values = groupValues(group);
  const selected = new Set(form.suffixes);
  if (mode === 'all') values.forEach(value => selected.add(value));
  else values.forEach(value => (selected.has(value) ? selected.delete(value) : selected.add(value)));
  form.suffixes = [...selected];
  previewTotal.value = null;
}

function selectAllSuffixes(mode: 'all' | 'invert') {
  if (mode === 'all') form.suffixes = suffixes.value.map(item => item.suffix);
  else {
    const selected = new Set(form.suffixes);
    suffixes.value.forEach(item =>
      selected.has(item.suffix) ? selected.delete(item.suffix) : selected.add(item.suffix)
    );
    form.suffixes = [...selected];
  }
  previewTotal.value = null;
}

function invalidatePreview() {
  previewTotal.value = null;
}

function parseLengths(value: string) {
  const result = new Set<number>();
  value
    .split(/[，,、\s]+/)
    .filter(Boolean)
    .forEach(part => {
      const range = part.match(/^(\d+)\s*[-~]\s*(\d+)$/);
      if (range) {
        const start = Math.max(1, Math.min(63, Number(range[1])));
        const end = Math.max(start, Math.min(63, Number(range[2])));
        for (let length = start; length <= end; length += 1) result.add(length);
      } else if (/^\d+$/.test(part)) {
        const length = Number(part);
        if (length >= 1 && length <= 63) result.add(length);
      }
    });
  return [...result].sort((a, b) => a - b);
}

function syncLengths() {
  form.lengths = parseLengths(lengthInput.value);
  invalidatePreview();
}

function syncExcludeChars() {
  form.exclude_chars = [
    ...new Set(
      excludeCharsInput.value
        .split(/[，,、\s]+/)
        .flatMap(item => [...item.trim().toLowerCase()])
        .filter(Boolean)
    )
  ];
  invalidatePreview();
}

function parseTokens(value: string) {
  return [
    ...new Set(
      value
        .split(/[，,、\s]+/)
        .map(item => item.trim().toLowerCase())
        .filter(Boolean)
    )
  ];
}

function syncExceptionSchemes() {
  form.exceptions.schemes = exceptionSchemeDrafts.value.map((draft, index) => ({
    name: draft.name.trim() || `方案 ${index + 1}`,
    enabled: draft.enabled,
    logic: draft.logic,
    lengths: parseLengths(draft.lengths),
    suffixes: parseTokens(draft.suffixes),
    patterns: parseTokens(draft.patterns).map(value => value.toUpperCase()),
    contains: parseTokens(draft.contains)
  }));
  form.exceptions.enabled = form.exceptions.schemes.some(scheme => scheme.enabled);
  form.exceptions.lengths = [];
  form.exceptions.suffixes = [];
  form.exceptions.patterns = [];
  form.exceptions.contains = [];
}

function addExceptionScheme() {
  exceptionSchemeDrafts.value.push(
    createExceptionSchemeDraft(
      createExceptionScheme(exceptionSchemeDrafts.value.length + 1),
      exceptionSchemeDrafts.value.length + 1
    )
  );
}

function removeExceptionScheme(index: number) {
  if (exceptionSchemeDrafts.value.length <= 1) return;
  exceptionSchemeDrafts.value.splice(index, 1);
}

function retryLabel(key: string) {
  return $t(`page.runtime.queryTasks.${key}` as App.I18n.I18nKey);
}

function restoreDefaults() {
  Object.assign(form, freshForm());
  exceptionSchemeDrafts.value = [createExceptionSchemeDraft(createExceptionScheme(), 1)];
  lengthInput.value = '';
  excludeCharsInput.value = '';
  apihzKeyInput.value = '';
  registrationRange.value = [];
  expirationRange.value = [];
  previewTotal.value = null;
}

async function loadSavedSettings() {
  try {
    const saved = await fetchQuerySettings();
    if (!saved) return;
    applySavedSettings(saved.settings);
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加载保存配置失败');
  }
}

function applySavedSettings(settings: CreateQueryTaskPayload) {
  Object.assign(form, freshForm(), settings);
  form.apihz_key = '';
  apihzKeyInput.value = '';
  apihzKeyConfigured.value = Boolean(settings.apihz_key_configured);
  form.domain_composition = [...(settings.domain_composition || [])];
  form.intercept_checks = [...(settings.intercept_checks || [])];
  // 旧配置中的抖音代理环节已不再使用
  form.proxy_stages = (settings.proxy_stages || [])
    .map(stage => (stage === 'whois' ? 'rdap' : stage))
    .filter(stage => proxyStageOptions.some(item => item.value === stage));
  const exceptions = settings.exceptions || defaultForm.exceptions;
  const savedSchemes =
    exceptions.schemes?.length > 0
      ? exceptions.schemes
      : [
          {
            ...createExceptionScheme(),
            enabled: Boolean(exceptions.enabled),
            lengths: exceptions.lengths || [],
            suffixes: exceptions.suffixes || [],
            patterns: exceptions.patterns || [],
            contains: exceptions.contains || []
          }
        ];
  form.exceptions = {
    ...defaultForm.exceptions,
    ...exceptions,
    lengths: [...(exceptions.lengths || [])],
    suffixes: [...(exceptions.suffixes || [])],
    patterns: [...(exceptions.patterns || [])],
    contains: [...(exceptions.contains || [])],
    schemes: savedSchemes.map(scheme => ({
      ...createExceptionScheme(),
      ...scheme,
      lengths: [...(scheme.lengths || [])],
      suffixes: [...(scheme.suffixes || [])],
      patterns: [...(scheme.patterns || [])],
      contains: [...(scheme.contains || [])]
    }))
  };
  exceptionSchemeDrafts.value = form.exceptions.schemes.map((scheme, index) =>
    createExceptionSchemeDraft(scheme, index + 1)
  );
  lengthInput.value = form.lengths.join(',');
  excludeCharsInput.value = form.exclude_chars.join(',');
  registrationRange.value =
    form.registration_start && form.registration_end ? [form.registration_start, form.registration_end] : [];
  expirationRange.value =
    form.expiration_start && form.expiration_end ? [form.expiration_start, form.expiration_end] : [];
}

async function saveSettings() {
  syncLengths();
  syncExcludeChars();
  syncExceptionSchemes();
  syncRegistrationRange();
  syncExpirationRange();
  form.apihz_key = apihzKeyInput.value.trim();
  savingSettings.value = true;
  try {
    const result = await saveQuerySettings({ ...form });
    form.apihz_key = '';
    apihzKeyInput.value = '';
    apihzKeyConfigured.value = Boolean(result.settings.apihz_key_configured || apihzKeyConfigured.value);
    window.$message?.success('查询配置已保存');
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '保存查询配置失败');
  } finally {
    savingSettings.value = false;
  }
}

async function loadInterceptKeyStatus() {
  try {
    interceptKeyConfigured.value = (await fetchInterceptKeyStatus()).configured;
  } catch {
    interceptKeyConfigured.value = false;
  }
}

async function updateInterceptKey(clear = false) {
  const value = clear ? '' : interceptKeyInput.value.trim();
  if (!clear && !value) {
    window.$message?.warning('请输入 API Key');
    return;
  }
  savingInterceptKey.value = true;
  try {
    interceptKeyConfigured.value = (await saveInterceptKey(value)).configured;
    interceptKeyInput.value = '';
    window.$message?.success(clear ? '已清除 API Key' : 'API Key 已保存');
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '保存 API Key 失败');
  } finally {
    savingInterceptKey.value = false;
  }
}

function syncRegistrationRange() {
  form.registration_start = registrationRange.value[0] || '';
  form.registration_end = registrationRange.value[1] || '';
}

function syncExpirationRange() {
  form.expiration_start = expirationRange.value[0] || '';
  form.expiration_end = expirationRange.value[1] || '';
}

async function preview() {
  previewLoading.value = true;
  try {
    const data = await previewQueryTask({
      lengths: form.lengths,
      suffixes: form.suffixes,
      exclude_chars: form.exclude_chars,
      domain_composition: form.domain_composition
    });
    previewTotal.value = data.total;
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '预览域名数量失败');
  } finally {
    previewLoading.value = false;
  }
}

async function loadData(options: { silent?: boolean } = {}) {
  if (dataRequestPending.value) return;
  dataRequestPending.value = true;
  try {
    if (options.silent) {
      tasks.value = await fetchQueryTasks();
    } else {
      // 后缀统计可能需要扫描大量历史数据，放到后台加载，避免阻塞任务状态首屏。
      fetchDomainSuffixes()
        .then(data => {
          suffixes.value = data;
        })
        .catch(() => undefined);
      tasks.value = await fetchQueryTasks();
    }
    // 预览数量也不应阻塞首屏，完成后仅更新右上角计数。
    if (previewTotal.value === null && !previewLoading.value) preview();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加载查询任务失败');
  } finally {
    dataRequestPending.value = false;
  }
}

async function handleStart() {
  if (runningTask.value) {
    window.$message?.warning('已有查询任务正在运行');
    return;
  }
  syncLengths();
  syncExcludeChars();
  syncExceptionSchemes();
  syncRegistrationRange();
  syncExpirationRange();
  form.apihz_key = apihzKeyInput.value.trim();
  if (form.intercept_checks.length && !interceptKeyConfigured.value) {
    window.$message?.warning('已勾选拦截检测，请先保存拦截检测 API Key');
    return;
  }
  submitting.value = true;
  try {
    await createQueryTask({ ...form });
    if (form.apihz_key) {
      form.apihz_key = '';
      apihzKeyInput.value = '';
      apihzKeyConfigured.value = true;
    }
    window.$message?.success('查询任务已启动');
    await loadData();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '启动查询任务失败');
  } finally {
    submitting.value = false;
  }
}

async function handleStop(task: QueryTask) {
  stopping.value = true;
  try {
    await stopQueryTask(task.id);
    window.$message?.success('已发送停止请求');
    await loadData();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '停止查询任务失败');
  } finally {
    stopping.value = false;
  }
}

async function handleTaskAction() {
  if (runningTask.value) {
    await handleStop(runningTask.value);
  } else {
    await handleStart();
  }
}

function statusLabel(status: QueryTask['status']) {
  return $t(`page.runtime.queryTasks.statuses.${status}` as App.I18n.I18nKey);
}

function statusType(status: QueryTask['status']): UI.ThemeColor {
  const types: Record<QueryTask['status'], UI.ThemeColor> = {
    created: 'info',
    running: 'warning',
    completed: 'success',
    stopped: 'info',
    failed: 'danger'
  };
  return types[status];
}

function progress(task: QueryTask) {
  return task.total_count ? Math.min(100, Math.round((task.processed_count / task.total_count) * 100)) : 0;
}

function logLevelClass(level: QueryLog['level']) {
  return {
    info: 'text-blue-500',
    warning: 'text-orange-500',
    error: 'text-red-500'
  }[level];
}

function logLevelLabel(level: QueryLog['level']) {
  return { info: '信息', warning: '警告', error: '错误' }[level];
}

function formatLogTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('zh-CN', { hour12: false });
}

function isLogAtLatest() {
  const viewport = logViewport.value;
  return !viewport || viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 24;
}

function handleLogScroll() {
  logAtLatest.value = isLogAtLatest();
}

function scrollLogsToLatest() {
  if (logViewport.value) logViewport.value.scrollTop = logViewport.value.scrollHeight;
  logAtLatest.value = true;
}

async function loadLogs() {
  if (logsLoading.value) return;
  const task = statusTask.value;
  if (!task) {
    logs.value = [];
    logTaskId.value = null;
    return;
  }
  const taskChanged = logTaskId.value !== task.id;
  const stickToLatest = taskChanged || logAtLatest.value || !logViewport.value;
  const previousScrollTop = logViewport.value?.scrollTop ?? 0;
  if (taskChanged) {
    logTaskId.value = task.id;
    logs.value = [];
    logAtLatest.value = true;
  }
  logsLoading.value = true;
  try {
    logs.value = await fetchQueryLogs(task.id, 500);
    await nextTick();
    if (stickToLatest || (logTaskId.value === task.id && logs.value.length === 0)) scrollLogsToLatest();
    else if (logViewport.value) {
      logViewport.value.scrollTop = previousScrollTop;
      handleLogScroll();
    }
  } catch {
    // 日志轮询失败时保留已有内容，下一轮继续刷新。
  } finally {
    logsLoading.value = false;
  }
}

watch(activeView, async value => {
  if (value !== 'logs') return;
  await loadLogs();
  if (!logAtLatest.value) return;
  await nextTick();
  scrollLogsToLatest();
});

const refreshTimer = setInterval(() => loadData({ silent: true }), 3000);
const logRefreshTimer = setInterval(() => {
  if (activeView.value === 'logs') loadLogs();
}, 1000);
async function initialize() {
  loadInterceptKeyStatus();
  await loadSavedSettings();
  await loadData();
  await loadLogs();
}
initialize();
onBeforeUnmount(() => {
  clearInterval(refreshTimer);
  clearInterval(logRefreshTimer);
});
</script>

<template>
  <div class="h-full min-h-0">
    <ElScrollbar class="h-full" :always="true">
      <ElTabs v-model="activeView" type="card" class="min-h-0">
        <ElTabPane name="query" label="查询任务">
          <div class="flex-col-stretch gap-16px">
            <ElCard class="card-wrapper">
              <template #header>
                <div class="flex flex-wrap items-center justify-between gap-12px">
                  <div>
                    <div class="flex items-center gap-8px">
                      <p class="font-16px font-medium">
                        {{ $t('page.runtime.queryTasks.title') }}
                      </p>
                    </div>
                  </div>
                  <div class="flex items-center gap-8px">
                    <span class="text-13px text-gray-500">
                      {{
                        $t('page.runtime.queryTasks.previewCount', {
                          total: previewTotal ?? '--'
                        })
                      }}
                    </span>
                    <ElButton type="primary" :loading="savingSettings" @click="saveSettings">
                      {{ $t('page.runtime.queryTasks.saveSettings') }}
                    </ElButton>
                    <ElButton @click="restoreDefaults">{{ $t('page.runtime.queryTasks.resetDefaults') }}</ElButton>
                    <ElButton text :loading="previewLoading" @click="preview">
                      {{ $t('page.runtime.queryTasks.preview') }}
                    </ElButton>
                  </div>
                </div>
              </template>

              <div class="mb-16px border border-gray-200 rounded-6px px-16px py-12px dark:border-gray-700">
                <div class="mb-10px flex items-center justify-between">
                  <span class="font-15px font-medium">{{ $t('page.runtime.queryTasks.statusTitle') }}</span>
                  <ElTag :type="pageStatusType">{{ pageStatus }}</ElTag>
                </div>
                <template v-if="statusTask">
                  <div class="grid grid-cols-1 gap-10px md:grid-cols-2 xl:grid-cols-5">
                    <div class="rounded-6px bg-gray-50 px-12px py-10px md:col-span-2 xl:col-span-2 dark:bg-gray-800/60">
                      <div class="mb-6px flex items-center justify-between text-13px text-gray-500">
                        <span>{{ $t('page.runtime.queryTasks.progress') }}</span>
                        <span>{{ statusTask.processed_count }} / {{ statusTask.total_count }}</span>
                      </div>
                      <ElProgress
                        :percentage="progress(statusTask)"
                        :status="
                          statusTask.status === 'failed'
                            ? 'exception'
                            : statusTask.status === 'completed'
                              ? 'success'
                              : undefined
                        "
                      />
                    </div>
                    <div
                      class="min-w-0 rounded-6px bg-gray-50 px-12px py-10px md:col-span-2 xl:col-span-1 dark:bg-gray-800/60"
                    >
                      <div class="mb-4px text-12px text-gray-500">{{ $t('page.runtime.queryTasks.current') }}</div>
                      <div class="truncate text-14px font-medium" :title="statusTask.current_domain || '--'">
                        {{ statusTask.current_domain || '--' }}
                      </div>
                    </div>
                    <div class="rounded-6px bg-green-50 px-12px py-10px dark:bg-green-900/20">
                      <div class="mb-4px text-12px text-green-700 dark:text-green-300">
                        {{ $t('page.runtime.queryTasks.qualified') }}
                      </div>
                      <div class="text-18px text-green-700 font-semibold dark:text-green-300">
                        {{ statusTask.qualified_count }}
                      </div>
                    </div>
                    <div class="rounded-6px bg-orange-50 px-12px py-10px dark:bg-orange-900/20">
                      <div class="mb-4px text-12px text-orange-700 dark:text-orange-300">
                        {{ $t('page.runtime.queryTasks.unqualified') }}
                      </div>
                      <div class="text-18px text-orange-700 font-semibold dark:text-orange-300">
                        {{ statusTask.unqualified_count }}
                      </div>
                    </div>
                  </div>
                </template>
                <span v-else class="text-13px text-gray-500">{{ $t('page.runtime.queryTasks.noTask') }}</span>
              </div>
            </ElCard>
            <ElCard class="card-wrapper">
              <template #header>
                <div class="flex items-center justify-between gap-12px">
                  <span class="font-16px font-medium">查询配置</span>
                  <span class="text-12px text-gray-400">修改配置后请点击“保存配置”</span>
                </div>
              </template>

              <ElForm :model="form" label-width="128px">
                <ElDivider content-position="left">域名信息</ElDivider>
                <ElRow :gutter="24">
                  <ElCol :lg="8" :md="12" :sm="24">
                    <ElFormItem label="查询来源">
                      <ElSelect v-model="form.domain_info_source" class="w-full">
                        <ElOption label="WHOIS（RDAP 优先）" value="whois" />
                        <ElOption label="接口盒子" value="apihz" />
                      </ElSelect>
                    </ElFormItem>
                  </ElCol>
                  <template v-if="form.domain_info_source === 'apihz'">
                    <ElCol :lg="6" :md="12" :sm="24">
                      <ElFormItem label="接口盒子 ID">
                        <ElInput v-model="form.apihz_id" clearable placeholder="接口盒子用户 ID" />
                      </ElFormItem>
                    </ElCol>
                    <ElCol :lg="10" :md="24" :sm="24">
                      <ElFormItem label="接口盒子 KEY">
                        <div class="w-full flex flex-wrap items-center gap-8px">
                          <ElInput
                            v-model="apihzKeyInput"
                            class="min-w-220px flex-1"
                            type="password"
                            show-password
                            clearable
                            autocomplete="new-password"
                            :placeholder="apihzKeyConfigured ? '已配置，输入新 KEY 可替换' : '接口盒子通信 KEY'"
                          />
                          <ElTag :type="apihzKeyConfigured ? 'success' : 'warning'" size="small">
                            {{ apihzKeyConfigured ? '已配置' : '未配置' }}
                          </ElTag>
                        </div>
                      </ElFormItem>
                    </ElCol>
                  </template>
                </ElRow>
                <div
                  v-if="form.domain_info_source === 'apihz'"
                  class="mb-12px rounded-6px bg-gray-50 px-12px py-10px text-13px text-gray-600 dark:bg-gray-800/60 dark:text-gray-300"
                >
                  接口盒子凭据会加密保存；选择 WHOIS 时使用 RDAP 优先、Py-WHOIS 直连的现有流程。
                </div>
                <ElDivider content-position="left">{{ $t('page.runtime.queryTasks.scopeTitle') }}</ElDivider>
                <ElRow :gutter="24">
                  <ElCol :lg="6" :md="12" :sm="24">
                    <ElFormItem :label="$t('page.runtime.queryTasks.lengths')">
                      <ElInput
                        v-model="lengthInput"
                        class="w-full"
                        clearable
                        :placeholder="$t('page.runtime.queryTasks.lengthsPlaceholder')"
                        @change="syncLengths"
                        @blur="syncLengths"
                      />
                    </ElFormItem>
                  </ElCol>
                  <ElCol :lg="6" :md="12" :sm="24">
                    <ElFormItem :label="$t('page.runtime.queryTasks.expirationTime')">
                      <ElDatePicker
                        v-model="expirationRange"
                        class="w-full"
                        type="daterange"
                        value-format="YYYY-MM-DD"
                        :range-separator="$t('page.domain.expired.dateRangeSeparator')"
                        @change="syncExpirationRange"
                      />
                    </ElFormItem>
                  </ElCol>
                  <ElCol :lg="6" :md="12" :sm="24">
                    <ElFormItem :label="$t('page.runtime.queryTasks.registrationTime')">
                      <ElDatePicker
                        v-model="registrationRange"
                        class="w-full"
                        type="daterange"
                        value-format="YYYY-MM-DD"
                        :range-separator="$t('page.domain.expired.dateRangeSeparator')"
                        @change="syncRegistrationRange"
                      />
                    </ElFormItem>
                  </ElCol>
                  <ElCol :lg="6" :md="12" :sm="24">
                    <ElFormItem :label="$t('page.runtime.queryTasks.deleteType')">
                      <ElSelect
                        v-model="deleteTypeValue"
                        class="w-full"
                        clearable
                        :placeholder="$t('page.runtime.queryTasks.deleteTypes.all')"
                      >
                        <ElOption :label="$t('page.runtime.queryTasks.deleteTypes.expired')" value="expired" />
                        <ElOption :label="$t('page.runtime.queryTasks.deleteTypes.redemption')" value="redemption" />
                        <ElOption
                          :label="$t('page.runtime.queryTasks.deleteTypes.pendingDelete')"
                          value="pending_delete"
                        />
                      </ElSelect>
                    </ElFormItem>
                  </ElCol>
                  <ElCol :lg="6" :md="12" :sm="24">
                    <ElFormItem label="排除字符">
                      <ElInput
                        v-model="excludeCharsInput"
                        class="w-full"
                        placeholder="如 0,4,8（域名主体）"
                        clearable
                        @change="syncExcludeChars"
                        @blur="syncExcludeChars"
                      />
                    </ElFormItem>
                  </ElCol>
                  <ElCol :lg="6" :md="12" :sm="24">
                    <ElFormItem label="域名组成">
                      <ElSelect
                        v-model="form.domain_composition"
                        class="w-full"
                        multiple
                        clearable
                        placeholder="全部"
                        @change="invalidatePreview"
                      >
                        <ElOption
                          v-for="item in compositionOptions"
                          :key="item.value"
                          :label="item.label"
                          :value="item.value"
                        />
                      </ElSelect>
                    </ElFormItem>
                  </ElCol>
                  <ElCol :span="24">
                    <ElFormItem>
                      <template #label>
                        <span>{{ $t('page.runtime.queryTasks.suffixes') }}</span>
                      </template>
                      <div class="w-full">
                        <ElTabs v-model="suffixGroupTab" type="border-card">
                          <ElTabPane name="all" :label="$t('page.runtime.queryTasks.all')">
                            <div class="mb-8px flex gap-8px">
                              <ElButton size="small" @click="selectAllSuffixes('all')">
                                {{ $t('page.runtime.queryTasks.selectAll') }}
                              </ElButton>
                              <ElButton size="small" @click="selectAllSuffixes('invert')">
                                {{ $t('page.runtime.queryTasks.invert') }}
                              </ElButton>
                            </div>
                            <ElCheckboxGroup v-model="form.suffixes" @change="invalidatePreview">
                              <ElCheckbox v-for="item in suffixes" :key="item.suffix" :label="item.suffix">
                                {{ item.suffix }} ({{ item.count }})
                              </ElCheckbox>
                            </ElCheckboxGroup>
                          </ElTabPane>
                          <ElTabPane
                            v-for="group in groupedSuffixes"
                            :key="group.group"
                            :name="group.group"
                            :label="group.label"
                          >
                            <div class="mb-4px flex items-center justify-between">
                              <span class="font-medium">{{ group.label }}</span>
                              <span class="flex gap-4px">
                                <ElButton text size="small" @click="selectGroup(group.group, 'all')">
                                  {{ $t('page.runtime.queryTasks.selectAll') }}
                                </ElButton>
                                <ElButton text size="small" @click="selectGroup(group.group, 'invert')">
                                  {{ $t('page.runtime.queryTasks.invert') }}
                                </ElButton>
                              </span>
                            </div>
                            <ElCheckboxGroup v-model="form.suffixes" @change="invalidatePreview">
                              <ElCheckbox v-for="item in group.items" :key="item.suffix" :label="item.suffix">
                                {{ item.suffix }} ({{ item.count }})
                              </ElCheckbox>
                            </ElCheckboxGroup>
                          </ElTabPane>
                        </ElTabs>
                      </div>
                    </ElFormItem>
                  </ElCol>
                </ElRow>

                <ElDivider content-position="left">拦截检测</ElDivider>
                <div
                  class="mb-12px rounded-6px bg-gray-50 px-12px py-10px text-13px text-gray-600 leading-22px dark:bg-gray-800/60 dark:text-gray-300"
                >
                  备案查询之后执行，勾选什么查什么；任一项命中即判定为已拦截。已拦截的域名命中例外策略仍进入符合列表，否则进入不符合列表。
                  <div class="mt-4px text-12px text-gray-400">
                    已备案 → 未拦截 → 符合；已备案 → 已拦截 → 命中例外 → 符合 / 未命中例外 →
                    不符合。未备案且未命中例外的域名不再检测，节省波点。
                  </div>
                </div>
                <ElRow :gutter="24">
                  <ElCol :span="24">
                    <ElFormItem label="检测项">
                      <div class="flex flex-wrap items-center gap-x-16px">
                        <ElCheckbox v-model="interceptAllChecked" :indeterminate="interceptIndeterminate">
                          全选
                        </ElCheckbox>
                        <ElCheckboxGroup v-model="form.intercept_checks">
                          <ElCheckbox v-for="item in interceptOptions" :key="item.value" :label="item.value">
                            {{ item.label }}
                          </ElCheckbox>
                        </ElCheckboxGroup>
                      </div>
                    </ElFormItem>
                  </ElCol>
                  <ElCol :xl="12" :lg="16" :md="24" :sm="24">
                    <ElFormItem label="API Key">
                      <div class="w-full flex flex-wrap items-center gap-8px">
                        <ElInput
                          v-model="interceptKeyInput"
                          class="min-w-200px flex-1"
                          type="password"
                          show-password
                          clearable
                          autocomplete="new-password"
                          :placeholder="
                            interceptKeyConfigured ? '已配置，输入新 Key 可替换' : '拨测（boce.com）API Key'
                          "
                        />
                        <ElButton type="primary" plain :loading="savingInterceptKey" @click="updateInterceptKey()">
                          保存 Key
                        </ElButton>
                        <ElButton
                          v-if="interceptKeyConfigured"
                          text
                          type="danger"
                          :disabled="savingInterceptKey"
                          @click="updateInterceptKey(true)"
                        >
                          清除
                        </ElButton>
                        <ElTag :type="interceptKeyConfigured ? 'success' : 'warning'" size="small">
                          {{ interceptKeyConfigured ? '已配置' : '未配置' }}
                        </ElTag>
                      </div>
                    </ElFormItem>
                  </ElCol>
                </ElRow>

                <ElDivider content-position="left">例外策略</ElDivider>
                <div
                  class="mb-12px rounded-6px bg-gray-50 px-12px py-10px text-13px text-gray-600 dark:bg-gray-800/60 dark:text-gray-300"
                >
                  WHOIS
                  条件必须先通过；启用的任一方案命中后，即使未备案或已拦截也会进入符合域名列表。方案内可选择“或”或“且”逻辑。
                </div>
                <div
                  v-for="(scheme, index) in exceptionSchemeDrafts"
                  :key="index"
                  class="mb-12px border border-gray-200 rounded-6px p-12px dark:border-gray-700"
                >
                  <div class="mb-8px flex flex-wrap items-center gap-8px">
                    <ElInput v-model="scheme.name" class="w-180px" placeholder="方案名称" />
                    <ElSwitch v-model="scheme.enabled" active-text="启用" inactive-text="关闭" />
                    <ElRadioGroup v-model="scheme.logic" size="small">
                      <ElRadioButton label="or">或</ElRadioButton>
                      <ElRadioButton label="and">且</ElRadioButton>
                    </ElRadioGroup>
                    <ElButton
                      v-if="exceptionSchemeDrafts.length > 1"
                      text
                      type="danger"
                      @click="removeExceptionScheme(index)"
                    >
                      删除方案
                    </ElButton>
                  </div>
                  <ElRow :gutter="16">
                    <ElCol :lg="6" :md="12" :sm="24">
                      <ElFormItem label="长度">
                        <ElInput v-model="scheme.lengths" class="w-full" placeholder="如 1-4、8" clearable />
                      </ElFormItem>
                    </ElCol>
                    <ElCol :lg="6" :md="12" :sm="24">
                      <ElFormItem label="后缀">
                        <ElInput v-model="scheme.suffixes" class="w-full" placeholder="如 cn,com" clearable />
                      </ElFormItem>
                    </ElCol>
                    <ElCol :lg="6" :md="12" :sm="24">
                      <ElFormItem label="包含字符">
                        <ElInput v-model="scheme.contains" class="w-full" placeholder="如 ai,88" clearable />
                      </ElFormItem>
                    </ElCol>
                    <ElCol :lg="6" :md="12" :sm="24">
                      <ElFormItem label="重复模式">
                        <ElInput v-model="scheme.patterns" class="w-full" placeholder="如 ABAB,ABCABC" clearable />
                      </ElFormItem>
                    </ElCol>
                  </ElRow>
                  <div class="text-12px text-gray-400">
                    方案内的多个条件按“{{
                      scheme.logic === 'and' ? '且' : '或'
                    }}”判断；多个方案之间按“或”判断。重复模式仅支持大写字母。
                  </div>
                </div>
                <ElButton type="primary" plain @click="addExceptionScheme">新增例外方案</ElButton>

                <ElDivider content-position="left">{{ $t('page.runtime.queryTasks.strategyTitle') }}</ElDivider>
                <ElFormItem label="重试次数">
                  <div class="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] w-full gap-x-16px gap-y-8px">
                    <div v-for="item in retryItems" :key="item.key" class="flex items-center gap-8px">
                      <span
                        class="w-48px shrink-0 text-right text-[var(--el-text-color-regular)]"
                        :title="retryLabel(item.label)"
                      >
                        {{ item.short }}
                      </span>
                      <div class="min-w-0 flex-1">
                        <ElInputNumber
                          v-model="form[item.key]"
                          class="!w-full"
                          :min="1"
                          :max="99"
                          controls-position="right"
                        />
                      </div>
                    </div>
                  </div>
                </ElFormItem>

                <ElDivider content-position="left">{{ $t('page.runtime.queryTasks.proxyTitle') }}</ElDivider>
                <ElAlert
                  class="mb-12px"
                  :title="$t('page.runtime.queryTasks.proxyHint')"
                  type="info"
                  :closable="false"
                  show-icon
                />
                <ElRow :gutter="24">
                  <ElCol :lg="7" :md="8" :sm="24">
                    <ElFormItem :label="$t('page.runtime.queryTasks.proxyMode')">
                      <ElSelect v-model="form.proxy_mode" class="w-full">
                        <ElOption :label="$t('page.runtime.queryTasks.proxyModes.direct')" value="direct" />
                        <ElOption :label="$t('page.runtime.queryTasks.proxyModes.tunnel')" value="tunnel" />
                        <ElOption :label="$t('page.runtime.queryTasks.proxyModes.api')" value="api" />
                      </ElSelect>
                    </ElFormItem>
                  </ElCol>
                  <ElCol :lg="8" :md="8" :sm="24">
                    <ElFormItem :label="$t('page.runtime.queryTasks.proxyMaxRequests')">
                      <ElInputNumber v-model="form.proxy_max_requests" class="w-full" :min="1" />
                    </ElFormItem>
                  </ElCol>
                  <ElCol :lg="9" :md="8" :sm="24">
                    <ElFormItem>
                      <template #label>
                        <span class="whitespace-nowrap">{{ $t('page.runtime.queryTasks.proxyStatsShort') }}</span>
                      </template>
                      <div class="flex flex-wrap items-center gap-8px">
                        <ElTag type="info">
                          {{
                            $t('page.runtime.queryTasks.proxyAvailable', {
                              count: proxyApiEnabled ? (proxyStatsTask?.proxy_current_available ?? 0) : '--'
                            })
                          }}
                        </ElTag>
                        <ElTag type="success">
                          {{
                            $t('page.runtime.queryTasks.proxyAcquired', {
                              count: proxyApiEnabled ? (proxyStatsTask?.proxy_acquired_count ?? 0) : '--'
                            })
                          }}
                        </ElTag>
                      </div>
                    </ElFormItem>
                  </ElCol>
                  <ElCol :span="24">
                    <ElFormItem :label="$t('page.runtime.queryTasks.proxyStages')">
                      <ElCheckboxGroup v-model="form.proxy_stages">
                        <ElCheckbox v-for="item in proxyStageOptions" :key="item.value" :label="item.value">
                          {{ item.label }}
                        </ElCheckbox>
                      </ElCheckboxGroup>
                    </ElFormItem>
                  </ElCol>
                  <ElCol :span="24">
                    <ElFormItem :label="$t('page.runtime.queryTasks.proxyEndpoint')">
                      <ElInput
                        v-model="form.proxy_endpoint"
                        :placeholder="$t('page.runtime.queryTasks.proxyEndpointPlaceholder')"
                      />
                    </ElFormItem>
                  </ElCol>
                </ElRow>

                <div class="mt-8px flex flex-wrap items-center justify-end gap-24px">
                  <div class="flex items-center gap-8px">
                    <span class="text-14px">{{ $t('page.runtime.queryTasks.continuous') }}</span>
                    <ElSwitch v-model="form.continuous" />
                  </div>
                  <div class="flex items-center gap-8px">
                    <span class="text-14px">{{ $t('page.runtime.queryTasks.threads') }}</span>
                    <ElInputNumber v-model="form.threads" class="w-180px" :min="1" :max="50" />
                  </div>
                  <ElButton
                    type="primary"
                    class="w-160px"
                    :loading="submitting || stopping"
                    :disabled="submitting || stopping"
                    @click="handleTaskAction"
                  >
                    {{ runningTask ? $t('page.runtime.queryTasks.stop') : $t('page.runtime.queryTasks.start') }}
                  </ElButton>
                </div>
              </ElForm>
            </ElCard>
          </div>
        </ElTabPane>

        <ElTabPane name="logs" label="运行日志">
          <ElCard class="card-wrapper">
            <template #header>
              <div class="flex items-center justify-between gap-8px">
                <div class="flex items-center gap-8px">
                  <span>{{ $t('page.runtime.queryTasks.logTitle') }}</span>
                  <ElTag type="info">最新 {{ logs.length }} / 500</ElTag>
                  <ElTag v-if="!logAtLatest" type="warning">已暂停跟随</ElTag>
                </div>
                <div class="flex items-center gap-8px">
                  <ElButton v-if="!logAtLatest" text type="primary" @click="scrollLogsToLatest">跳到最新</ElButton>
                  <ElButton text :loading="logsLoading" @click="loadLogs">{{ $t('common.refresh') }}</ElButton>
                </div>
              </div>
            </template>
            <div
              ref="logViewport"
              class="h-420px overflow-auto overscroll-contain border border-gray-200 rounded-4px dark:border-gray-700"
              @scroll="handleLogScroll"
            >
              <div
                class="sticky top-0 z-1 grid grid-cols-[82px_170px_72px_180px_minmax(240px,1fr)] gap-12px border-b border-gray-200 bg-gray-50 px-12px py-8px text-12px text-gray-500 dark:border-gray-700 dark:bg-gray-800"
              >
                <span>时间</span>
                <span>域名</span>
                <span>环节</span>
                <span>状态</span>
                <span>详细信息</span>
              </div>
              <div v-if="!logs.length" class="px-12px py-30px text-center text-13px text-gray-400">暂无运行日志</div>
              <div
                v-for="item in logs"
                :key="item.id"
                class="grid grid-cols-[82px_170px_72px_180px_minmax(240px,1fr)] gap-12px border-b border-gray-100 px-12px py-7px text-12px dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50"
              >
                <span class="whitespace-nowrap text-gray-500">{{ formatLogTime(item.created_at) }}</span>
                <span class="truncate" :title="item.domain">{{ item.domain || '--' }}</span>
                <span class="whitespace-nowrap">{{ item.stage || '--' }}</span>
                <span class="truncate" :class="logLevelClass(item.level)" :title="item.message">
                  {{ logLevelLabel(item.level) }} · {{ item.message }}
                </span>
                <span class="truncate text-gray-500" :title="item.detail">{{ item.detail || '--' }}</span>
              </div>
            </div>
          </ElCard>
        </ElTabPane>
      </ElTabs>
    </ElScrollbar>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import {
  type MonitorLog,
  type MonitorSettings,
  type MonitorStatus,
  type RegistrarApi,
  type RegistrarApiPayload,
  createRegistrarApi,
  deleteRegistrarApi,
  fetchMonitorLogs,
  fetchMonitorSettings,
  fetchMonitorStatus,
  fetchRegistrarApis,
  saveMonitorSettings,
  startMonitor,
  stopMonitor,
  testRegistrarApi,
  updateRegistrarApi
} from '@/service/api';

const activeTab = ref<'settings' | 'apis'>('settings');
const loading = ref(false);
const saving = ref(false);
const actionLoading = ref(false);
const apis = ref<RegistrarApi[]>([]);
const logs = ref<MonitorLog[]>([]);
const logViewport = ref<HTMLElement | null>(null);
const logAtLatest = ref(true);
const logsLoading = ref(false);
const status = ref<MonitorStatus | null>(null);
const settings = reactive<MonitorSettings>({
  interval_seconds: 30,
  concurrency: 5,
  whois_retries: 2,
  auto_register: false,
  auto_start: false,
  availability_api_id: 0,
  api_ids: []
});
const dialogVisible = ref(false);
const editingId = ref<number | null>(null);
const apiSaving = ref(false);
const apiTesting = ref<number | null>(null);
const adapterOptions = [
  { value: 'aliyun_intl', label: '阿里云国际', endpoint: 'https://domain-intl.aliyuncs.com/' },
  { value: 'dynadot', label: 'Dynadot', endpoint: 'https://api.dynadot.com' },
  { value: 'gname', label: 'GNAME', endpoint: 'https://api.gname.com' },
  { value: 'godaddy', label: 'GoDaddy', endpoint: 'https://api.godaddy.com/v1/domains' }
] as const;
const adapterLabels = Object.fromEntries(adapterOptions.map(option => [option.value, option.label]));
const apiForm = reactive<RegistrarApiPayload>({
  name: '',
  adapter: 'aliyun_intl',
  endpoint: '',
  token: '',
  headers_json: '{}',
  config_json: '{}',
  enabled: true
});
const tokenPlaceholder = computed(() => {
  const placeholders: Record<RegistrarApiPayload['adapter'], string> = {
    aliyun_intl: 'AccessKeyId:AccessKeySecret',
    dynadot: 'API密钥:API Secret（API密码）',
    gname: 'AppID:AppKey',
    godaddy: 'API Key:API Secret',
    http_json: '不使用通用 HTTP JSON'
  };
  return placeholders[apiForm.adapter];
});
type ConfigGuideField = {
  key: string;
  label: string;
  required?: boolean;
  description: string;
  example?: string;
};

const configGuides: Record<
  RegistrarApiPayload['adapter'],
  { summary: string; fields: ConfigGuideField[]; notes: string[] }
> = {
  aliyun_intl: {
    summary: '用于填写阿里云国际域名接口的动作、注册年限和实名资料。Token 只填写 AccessKeyId:AccessKeySecret。',
    fields: [
      {
        key: 'register_action',
        label: '注册动作',
        description: '注册域名时调用的阿里云 Action，通常不用修改。',
        example: 'CreateOrder'
      },
      {
        key: 'check_action',
        label: '测试动作',
        description: '点击“测试”时调用的 Action，通常不用修改。',
        example: 'CheckDomain'
      },
      { key: 'Period', label: '注册年限', description: '注册年限，填数字，例如 1 表示一年。', example: '1' },
      {
        key: 'RegistrantProfileId',
        label: '注册人资料 ID',
        required: true,
        description: '阿里云国际控制台创建的注册人资料 ID。',
        example: 'rp-xxxxxxxx'
      },
      {
        key: 'ContactId',
        label: '联系人 ID',
        description: '如果账号要求单独联系人资料，在此填写 ContactId。',
        example: 'c-xxxxxxxx'
      },
      {
        key: 'test_domain',
        label: '测试域名',
        description: '测试 API 时查询的域名，不填则使用 example.com。',
        example: 'example.com'
      }
    ],
    notes: [
      '不要把 AccessKey 写进 JSON，它应该填写在上面的 Token。',
      '接口地址默认使用 https://domain-intl.aliyuncs.com/。'
    ]
  },
  dynadot: {
    summary: 'Dynadot 使用 RESTful v2；Token 填写 API密钥:API Secret（API密码）。',
    fields: [
      {
        key: 'payload',
        label: '注册请求体（可选）',
        description: '需要自定义注册参数时填写 JSON 对象；至少按官方文档提供 domain 对象。',
        example: '{"domain":{}}'
      },
      {
        key: 'currency',
        label: '结算币种（可选）',
        description: '注册请求使用的币种，例如 USD；也可以直接放进 payload。',
        example: 'USD'
      },
      {
        key: 'endpoint',
        label: '接口地址（可选）',
        description: '只有使用自定义接口地址时才填写，通常留空使用官方地址。',
        example: 'https://api.dynadot.com'
      }
    ],
    notes: [
      'Token 格式为 API密钥:API Secret，不要加 Bearer 或其他前缀。',
      'API 密钥和 API Secret 必须是同一次生成、同一环境（生产或沙盒）的一对；不要填写账户登录密码。',
      '生产 API 地址为 https://api.dynadot.com；沙盒 API 地址为 https://api-sandbox.dynadot.com。请按凭据所属环境填写接口地址。',
      '两项凭据都在 Dynadot API 页面生成；不要把生产和沙盒两行凭据一起粘贴到 Token。'
    ]
  },
  gname: {
    summary: 'GNAME 配置用于指定接口路径、模板编号和 DNS。Token 填写 AppID:AppKey。',
    fields: [
      {
        key: 'register_path',
        label: '注册接口路径',
        description: 'GNAME 注册接口的相对路径，通常不用修改。',
        example: '/domain/reg'
      },
      {
        key: 'test_path',
        label: '测试接口路径',
        description: '点击“测试”时调用的用户信息接口路径。',
        example: '/user/info'
      },
      {
        key: 'check_path',
        label: '可注册查询路径',
        description: '监控时查询域名状态的接口路径，默认使用 /domain/check。',
        example: '/domain/check'
      },
      {
        key: 'mbid',
        label: '注册模板 ID',
        description: 'GNAME 后台配置的注册人/模板编号，有要求时填写。',
        example: '12345'
      },
      {
        key: 'dns',
        label: 'DNS',
        description: '可选，填写 DNS 服务器，多个值按平台要求填写。',
        example: 'ns1.example.com,ns2.example.com'
      },
      { key: 'qian', label: '签名扩展参数', description: '平台开通该参数时才填写，否则留空。', example: '' }
    ],
    notes: ['Token 格式必须是 AppID:AppKey。', '不确定的字段可以保留在 JSON 中但不要随意填写，避免接口拒绝。']
  },
  godaddy: {
    summary: 'GoDaddy 注册需要注册年限、隐私设置、联系人和 consent；Token 填写 API Key:API Secret。',
    fields: [
      { key: 'period', label: '注册年限', description: '注册域名的年数，填数字。', example: '1' },
      { key: 'renew_auto', label: '自动续费', description: 'true 开启自动续费，false 关闭。', example: 'false' },
      {
        key: 'privacy',
        label: '隐私保护',
        description: 'true 开启隐私保护，具体可用性以域名后缀为准。',
        example: 'true'
      },
      {
        key: 'consent',
        label: 'consent',
        required: true,
        description: 'GoDaddy 要求的同意信息对象，请按账号/API 返回要求填写。',
        example: '{"agreedAt":"2026-01-01T00:00:00Z","agreedBy":"1.2.3.4","agreementKeys":["DNRA"]}'
      },
      {
        key: 'contactRegistrant',
        label: '注册人联系人',
        required: true,
        description: '注册人联系人对象，通常包含 name、email、phone、address、city、state、postalCode、country。',
        example:
          '{"nameFirst":"张","nameLast":"三","email":"name@example.com","phone":"+86.13800000000","addressMailing":{"address1":"示例路1号","city":"上海","state":"SH","postalCode":"200000","country":"CN"}}'
      },
      {
        key: 'contactAdmin / Billing / Tech',
        label: '其他联系人',
        description: '管理员、账单、技术联系人；未单独填写时可按 GoDaddy 账号要求补齐。',
        example: '{}'
      },
      { key: 'test_domain', label: '测试域名', description: '点击“测试”时检查可用性的域名。', example: 'example.com' }
    ],
    notes: [
      '联系人和 consent 不是普通字符串，必须是 JSON 对象。',
      '生产环境建议先用测试账号和 test_domain 验证，再开启自动抢注。'
    ]
  },
  http_json: {
    summary: '通用 HTTP JSON 会向接口 POST {"domain":"域名"}，没有固定的平台配置字段。',
    fields: [
      {
        key: 'payload（可选）',
        label: '自定义扩展',
        description: '当前适配器默认只发送 domain；如平台有额外要求，请在自定义请求头或后端适配器中处理。',
        example: '{}'
      }
    ],
    notes: [
      'Token 会作为 Authorization: Bearer Token 发送。',
      '接口必须返回 2xx，且响应中不能包含 error、failed 等失败标记。'
    ]
  }
};
const configGuide = computed(() => configGuides[apiForm.adapter]);
const enabledApis = computed(() => apis.value.filter(api => api.enabled));
let statusTimer: ReturnType<typeof setInterval> | undefined;
let logTimer: ReturnType<typeof setInterval> | undefined;

const isRunning = computed(() => status.value?.status === 'running' || status.value?.status === 'stopping');
const statusLabel = computed(() => {
  const labels: Record<MonitorStatus['status'] | 'unknown', string> = {
    running: '运行中',
    stopping: '停止中',
    stopped: '已停止',
    error: '异常',
    unknown: '未启动'
  };
  return labels[status.value?.status || 'unknown'];
});
const statusType = computed<UI.ThemeColor>(() => {
  if (status.value?.status === 'running') return 'success';
  if (status.value?.status === 'error') return 'danger';
  if (status.value?.status === 'stopping') return 'warning';
  return 'info';
});
const progress = computed(() => {
  if (!status.value?.total_count) return 0;
  return Math.min(100, Math.round((status.value.checked_count / status.value.total_count) * 100));
});

async function loadApis() {
  apis.value = await fetchRegistrarApis();
}

async function loadSettings() {
  const data = await fetchMonitorSettings();
  Object.assign(settings, data);
}

async function loadStatus() {
  status.value = await fetchMonitorStatus();
}

function handleLogScroll() {
  const viewport = logViewport.value;
  logAtLatest.value = !viewport || viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 24;
}

function scrollLogsToLatest() {
  if (logViewport.value) logViewport.value.scrollTop = logViewport.value.scrollHeight;
  logAtLatest.value = true;
}

async function loadLogs() {
  if (logsLoading.value) return;
  logsLoading.value = true;
  try {
    const data = await fetchMonitorLogs(100);
    // 请求期间用户可能上滑，在更新日志前记录最新的跟随状态和位置。
    const stickToLatest = logAtLatest.value;
    const previousScrollTop = logViewport.value?.scrollTop ?? 0;
    logs.value = data;
    await nextTick();
    if (stickToLatest) scrollLogsToLatest();
    else if (logViewport.value) {
      logViewport.value.scrollTop = previousScrollTop;
      handleLogScroll();
    }
  } finally {
    logsLoading.value = false;
  }
}

async function loadAll() {
  loading.value = true;
  try {
    await Promise.all([loadApis(), loadSettings(), loadStatus(), loadLogs()]);
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加载监控任务失败');
  } finally {
    loading.value = false;
  }
}

async function saveSettings() {
  saving.value = true;
  try {
    const data = await saveMonitorSettings({ ...settings, api_ids: [...settings.api_ids] });
    Object.assign(settings, data);
    window.$message?.success('监控运行设置已保存');
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '保存运行设置失败');
  } finally {
    saving.value = false;
  }
}

async function toggleMonitor() {
  actionLoading.value = true;
  try {
    status.value = isRunning.value ? await stopMonitor() : await startMonitor();
    await loadStatus();
    window.$message?.success(isRunning.value ? '已发送停止请求' : '监控任务已启动');
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '更新监控状态失败');
  } finally {
    actionLoading.value = false;
  }
}

function resetApiForm() {
  editingId.value = null;
  Object.assign(apiForm, {
    name: '',
    adapter: 'aliyun_intl',
    endpoint: 'https://domain-intl.aliyuncs.com/',
    token: '',
    headers_json: '{}',
    config_json: '{}',
    enabled: true
  });
  selectAdapter('aliyun_intl');
}

function selectAdapter(value: RegistrarApiPayload['adapter']) {
  const option = adapterOptions.find(item => item.value === value);
  if (option?.endpoint) apiForm.endpoint = option.endpoint;
  const templates: Record<RegistrarApiPayload['adapter'], string> = {
    aliyun_intl:
      '{\n  "register_action": "CreateOrder",\n  "check_action": "CheckDomain",\n  "SubscriptionType": "New",\n  "Period": 1,\n  "RegistrantProfileId": "填写注册人资料 ID"\n}',
    dynadot: '{\n  "payload": {},\n  "currency": "USD"\n}',
    gname: '{\n  "register_path": "/domain/reg",\n  "test_path": "/user/info",\n  "check_path": "/domain/check"\n}',
    godaddy:
      '{\n  "period": 1,\n  "renew_auto": false,\n  "privacy": true,\n  "consent": {},\n  "contactRegistrant": {},\n  "contactAdmin": {},\n  "contactBilling": {},\n  "contactTech": {}\n}',
    http_json: '{}'
  };
  apiForm.config_json = templates[value];
  if (value !== 'http_json') apiForm.headers_json = '{}';
}

function openCreateApi() {
  resetApiForm();
  dialogVisible.value = true;
}

function openEditApi(api: RegistrarApi) {
  editingId.value = api.id;
  Object.assign(apiForm, {
    name: api.name,
    adapter: api.adapter,
    endpoint: api.endpoint,
    token: '',
    headers_json: api.headers_json,
    config_json: api.config_json,
    enabled: api.enabled
  });
  if (!api.config_json.trim() || api.config_json.trim() === '{}') selectAdapter(api.adapter);
  dialogVisible.value = true;
}

async function submitApi() {
  if (!apiForm.name.trim() || !apiForm.endpoint.trim()) {
    window.$message?.warning('请填写 API 名称和注册接口地址');
    return;
  }
  apiSaving.value = true;
  try {
    const payload = { ...apiForm };
    if (!payload.token) delete payload.token;
    if (editingId.value) await updateRegistrarApi(editingId.value, payload);
    else await createRegistrarApi(payload);
    dialogVisible.value = false;
    await loadApis();
    window.$message?.success('注册商 API 已保存');
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '保存注册商 API 失败');
  } finally {
    apiSaving.value = false;
  }
}

async function removeApi(api: RegistrarApi) {
  try {
    await window.$messageBox?.confirm(`确定删除“${api.name}”吗？`);
    await deleteRegistrarApi(api.id);
    settings.api_ids = settings.api_ids.filter(id => id !== api.id);
    if (settings.availability_api_id === api.id) settings.availability_api_id = 0;
    await loadApis();
  } catch {
    // 用户取消或删除失败时保留当前页面。
  }
}

async function testApi(api: RegistrarApi) {
  apiTesting.value = api.id;
  try {
    const result = await testRegistrarApi(api.id);
    if (result.success) window.$message?.success(`连接成功（HTTP ${result.status_code}）`);
    else {
      const detail = result.response?.trim().replace(/\s+/g, ' ').slice(0, 160);
      const signatureInvalid = detail?.includes('X-Signature') && detail.toLowerCase().includes('not valid');
      const hint = signatureInvalid ? '；签名校验失败，请核对同一环境的 API Key/Secret，并确认后端已更新' : '';
      window.$message?.warning(`鉴权失败（HTTP ${result.status_code}）${hint}${detail ? `：${detail}` : ''}`);
    }
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '测试 API 失败');
  } finally {
    apiTesting.value = null;
  }
}

function logClass(level: MonitorLog['level']) {
  return { info: 'text-blue-500', warning: 'text-orange-500', error: 'text-red-500' }[level];
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('zh-CN', { hour12: false });
}

onMounted(async () => {
  await loadAll();
  statusTimer = setInterval(() => loadStatus().catch(() => undefined), 3000);
  logTimer = setInterval(() => loadLogs().catch(() => undefined), 2000);
});

onBeforeUnmount(() => {
  if (statusTimer) clearInterval(statusTimer);
  if (logTimer) clearInterval(logTimer);
});
</script>

<template>
  <div class="flex-col-stretch gap-16px">
    <ElCard v-loading="loading" class="card-wrapper">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-12px">
          <div class="flex items-center gap-8px">
            <span class="font-16px font-medium">监控任务</span>
            <ElTag :type="statusType">{{ statusLabel }}</ElTag>
          </div>
          <div class="flex items-center gap-8px">
            <span class="text-13px text-gray-500">当前监控 {{ status?.total_count ?? 0 }} 个符合域名</span>
            <ElButton :type="isRunning ? 'danger' : 'primary'" :loading="actionLoading" @click="toggleMonitor">
              {{ isRunning ? '停止监控' : '开始监控' }}
            </ElButton>
          </div>
        </div>
      </template>

      <div class="grid grid-cols-2 gap-10px md:grid-cols-5">
        <div class="rounded-6px bg-gray-50 px-12px py-10px md:col-span-2 dark:bg-gray-800/60">
          <div class="mb-5px flex justify-between text-12px text-gray-500">
            <span>本轮进度</span>
            <span>{{ status?.checked_count ?? 0 }} / {{ status?.total_count ?? 0 }}</span>
          </div>
          <ElProgress :percentage="progress" :status="status?.status === 'error' ? 'exception' : undefined" />
        </div>
        <div class="rounded-6px bg-blue-50 px-12px py-10px dark:bg-blue-900/20">
          <div class="text-12px text-gray-500">当前域名</div>
          <div class="mt-5px truncate font-medium" :title="status?.current_domain || '--'">
            {{ status?.current_domain || '--' }}
          </div>
        </div>
        <div class="rounded-6px bg-green-50 px-12px py-10px dark:bg-green-900/20">
          <div class="text-12px text-gray-500">可注册</div>
          <div class="mt-5px text-18px text-green-700 font-semibold dark:text-green-300">
            {{ status?.available_count ?? 0 }}
          </div>
        </div>
        <div class="rounded-6px bg-orange-50 px-12px py-10px dark:bg-orange-900/20">
          <div class="text-12px text-gray-500">已踢出</div>
          <div class="mt-5px text-18px text-orange-700 font-semibold dark:text-orange-300">
            {{ status?.kicked_count ?? 0 }}
          </div>
        </div>
      </div>
      <ElAlert v-if="status?.last_error" class="mt-12px" type="error" :closable="false" :title="status.last_error" />
    </ElCard>

    <ElCard class="card-wrapper">
      <ElTabs v-model="activeTab" type="card">
        <ElTabPane name="settings" label="运行设置">
          <ElForm label-width="150px" class="pt-8px">
            <ElRow :gutter="24">
              <ElCol :lg="8" :md="12" :sm="24">
                <ElFormItem label="检查间隔（秒）">
                  <ElInputNumber v-model="settings.interval_seconds" class="w-full" :min="5" :max="3600" />
                </ElFormItem>
              </ElCol>
              <ElCol :lg="8" :md="12" :sm="24">
                <ElFormItem label="并发查询数">
                  <ElInputNumber v-model="settings.concurrency" class="w-full" :min="1" :max="50" />
                </ElFormItem>
              </ElCol>
              <ElCol :lg="8" :md="12" :sm="24">
                <ElFormItem label="WHOIS 重试次数">
                  <ElInputNumber v-model="settings.whois_retries" class="w-full" :min="1" :max="10" />
                </ElFormItem>
              </ElCol>
            </ElRow>
            <ElFormItem label="可注册查询来源">
              <ElSelect
                v-model="settings.availability_api_id"
                class="max-w-420px w-full"
                placeholder="请选择一个注册商 API"
              >
                <ElOption :value="0" label="请选择一个注册商 API" disabled />
                <ElOption v-for="api in enabledApis" :key="api.id" :value="api.id" :label="api.name" />
              </ElSelect>
              <div class="ml-10px text-12px text-gray-500">
                先由此 API 判断可注册状态；不可注册时再查询 WHOIS，确认是否已续费或被注册。
              </div>
            </ElFormItem>
            <ElFormItem label="自动抢注">
              <ElSwitch v-model="settings.auto_register" />
              <span class="ml-10px text-12px text-gray-500">发现可注册域名后，同时调用勾选的注册商 API</span>
            </ElFormItem>
            <ElFormItem label="服务启动后自动监控">
              <ElSwitch v-model="settings.auto_start" />
            </ElFormItem>
            <ElFormItem label="启用注册商 API">
              <div v-if="apis.length" class="flex flex-wrap gap-x-20px gap-y-8px">
                <ElCheckboxGroup v-model="settings.api_ids">
                  <ElCheckbox v-for="api in apis" :key="api.id" :label="api.id">
                    {{ api.name }}
                    <span v-if="!api.enabled" class="ml-4px text-12px text-gray-400">已停用</span>
                  </ElCheckbox>
                </ElCheckboxGroup>
              </div>
              <span v-else class="text-13px text-gray-400">请先在“API 设置”中添加注册商接口</span>
            </ElFormItem>
            <div class="flex justify-end">
              <ElButton type="primary" :loading="saving" @click="saveSettings">保存运行设置</ElButton>
            </div>
          </ElForm>
        </ElTabPane>

        <ElTabPane name="apis" label="API 设置">
          <div class="mb-12px flex justify-end">
            <ElButton type="primary" @click="openCreateApi">新增注册商 API</ElButton>
          </div>
          <ElTable :data="apis" border>
            <ElTableColumn prop="name" label="名称" min-width="150" />
            <ElTableColumn label="适配器" width="150">
              <template #default="{ row }">{{ adapterLabels[row.adapter] || row.adapter }}</template>
            </ElTableColumn>
            <ElTableColumn prop="endpoint" label="注册接口地址" min-width="280" show-overflow-tooltip />
            <ElTableColumn label="状态" width="100">
              <template #default="{ row }">
                <ElTag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="Token" width="100">
              <template #default="{ row }">{{ row.has_token ? '已配置' : '未配置' }}</template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="260" fixed="right">
              <template #default="{ row }">
                <ElButton text type="primary" :loading="apiTesting === row.id" @click="testApi(row)">测试</ElButton>
                <ElButton text @click="openEditApi(row)">编辑</ElButton>
                <ElButton text type="danger" @click="removeApi(row)">删除</ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElTabPane>
      </ElTabs>
    </ElCard>

    <ElCard class="card-wrapper">
      <template #header>
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-8px">
            <span class="font-16px font-medium">运行日志</span>
            <ElTag v-if="!logAtLatest" type="warning">已暂停跟随</ElTag>
          </div>
          <div class="flex items-center gap-8px">
            <ElButton v-if="!logAtLatest" text type="primary" @click="scrollLogsToLatest">跳到最新</ElButton>
            <ElTag type="info">最新 {{ logs.length }} / 500</ElTag>
          </div>
        </div>
      </template>
      <div
        ref="logViewport"
        class="h-300px overflow-auto overscroll-contain border border-gray-200 rounded-4px dark:border-gray-700"
        @scroll="handleLogScroll"
      >
        <div
          v-for="log in logs"
          :key="log.id"
          class="grid grid-cols-[82px_90px_170px_minmax(220px,1fr)] gap-10px border-b border-gray-100 px-10px py-7px text-12px dark:border-gray-800"
        >
          <span class="text-gray-500">{{ formatTime(log.created_at) }}</span>
          <span :class="logClass(log.level)">{{ log.stage }}</span>
          <span class="truncate" :title="log.domain">{{ log.domain || '--' }}</span>
          <span>{{ log.message }}</span>
        </div>
        <ElEmpty v-if="!logs.length" description="暂无运行日志" :image-size="70" />
      </div>
    </ElCard>

    <ElDialog v-model="dialogVisible" :title="editingId ? '编辑注册商 API' : '新增注册商 API'" width="620px">
      <ElAlert
        class="mb-14px"
        type="info"
        :closable="false"
        title="平台适配器会按对应平台协议调用注册接口；Token 和平台配置仅保存在本机。"
      />
      <ElForm label-width="120px">
        <ElFormItem label="API 名称"><ElInput v-model="apiForm.name" placeholder="例如：平台 A" /></ElFormItem>
        <ElFormItem label="注册商平台">
          <ElSelect v-model="apiForm.adapter" class="w-full" @change="selectAdapter">
            <ElOption
              v-for="option in adapterOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="注册接口地址">
          <ElInput v-model="apiForm.endpoint" placeholder="https://api.example.com/register" />
        </ElFormItem>
        <ElFormItem label="Token">
          <ElInput
            v-model="apiForm.token"
            type="password"
            show-password
            :placeholder="editingId ? '留空表示保持原 Token' : tokenPlaceholder"
          />
        </ElFormItem>
        <ElFormItem label="平台配置 JSON">
          <div
            class="mb-8px max-h-260px w-full overflow-y-auto rounded-6px bg-blue-50 px-10px py-8px text-12px dark:bg-blue-900/20"
          >
            <div class="text-blue-700 font-medium dark:text-blue-300">填写说明</div>
            <div class="mt-3px text-gray-600 leading-18px dark:text-gray-300">{{ configGuide.summary }}</div>
            <div class="grid mt-7px gap-5px">
              <div
                v-for="field in configGuide.fields"
                :key="field.key"
                class="grid grid-cols-[150px_minmax(0,1fr)] gap-8px border-t border-blue-100 pt-5px dark:border-blue-800/50"
              >
                <div class="text-blue-700 font-mono dark:text-blue-300">
                  {{ field.key }}
                  <ElTag v-if="field.required" size="small" type="danger" class="ml-3px">必填</ElTag>
                </div>
                <div class="min-w-0 text-gray-600 dark:text-gray-300">
                  <div>{{ field.label }}：{{ field.description }}</div>
                  <div v-if="field.example" class="mt-2px break-all text-gray-500 dark:text-gray-400">
                    示例：
                    <code>{{ field.example }}</code>
                  </div>
                </div>
              </div>
            </div>
            <ul class="mt-7px list-disc pl-16px text-gray-500 dark:text-gray-400">
              <li v-for="note in configGuide.notes" :key="note">{{ note }}</li>
            </ul>
          </div>
          <ElInput
            v-model="apiForm.config_json"
            type="textarea"
            :rows="8"
            placeholder="上方有当前平台的中文字段说明；这里填写合法 JSON 对象"
          />
        </ElFormItem>
        <ElFormItem label="启用"><ElSwitch v-model="apiForm.enabled" /></ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="apiSaving" @click="submitApi">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

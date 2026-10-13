<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import {
  type BookingScheme,
  type BookingSettings,
  type GnameChannel,
  type QueryExceptionScheme,
  type RegistrarApi,
  fetchBackorderChannels,
  fetchBookingSettings,
  saveBookingSettings
} from '@/service/api';
import { compositionOptions, compositionText } from '@/components/domain-filter';

const props = defineProps<{ apis: RegistrarApi[] }>();
const emit = defineEmits<{ refreshApis: [] }>();
const settings = reactive<BookingSettings>({ enabled: false, schemes: [] });
const loading = ref(false);
const saving = ref(false);
type ExceptionSchemeDraft = Omit<
  QueryExceptionScheme,
  'lengths' | 'suffixes' | 'patterns' | 'contains' | 'exclude_chars'
> & {
  lengths: string;
  suffixes: string;
  patterns: string;
  contains: string;
  exclude_chars: string;
  registration_start: string;
  registration_end: string;
  filing_start: string;
  filing_end: string;
  filing_natures: string[];
  api_id: number;
  channels: number[];
};

function createExceptionScheme(index = 1): QueryExceptionScheme {
  return {
    name: `方案 ${index}`,
    enabled: true,
    domain_composition: [],
    lengths: [],
    suffixes: [],
    patterns: [],
    contains: [],
    exclude_chars: []
  };
}

function createExceptionSchemeDraft(
  scheme: QueryExceptionScheme & Partial<BookingScheme>,
  index: number
): ExceptionSchemeDraft {
  return {
    name: scheme.name || `方案 ${index}`,
    enabled: scheme.enabled,
    lengths: scheme.lengths.join(','),
    suffixes: scheme.suffixes.join(','),
    patterns: scheme.patterns.join(','),
    contains: scheme.contains.join(','),
    exclude_chars: scheme.exclude_chars.join(','),
    domain_composition: [...(scheme.domain_composition || [])],
    registration_start: scheme.registration_start || '',
    registration_end: scheme.registration_end || '',
    filing_start: scheme.filing_start || '',
    filing_end: scheme.filing_end || '',
    filing_natures: [...(scheme.filing_natures || [])],
    api_id: scheme.api_id || 0,
    channels: [...(scheme.channels || [])]
  };
}

const schemeDrafts = ref<ExceptionSchemeDraft[]>([createExceptionSchemeDraft(createExceptionScheme(), 1)]);
const activeSchemeIndex = ref(0);
const activeScheme = computed(() => schemeDrafts.value[activeSchemeIndex.value]);
const bookingApis = computed(() => props.apis.filter(api => api.adapter === 'gname' && api.enabled && api.has_token));
const missingBookingApi = computed(() => {
  const id = activeScheme.value?.api_id;
  return id && !bookingApis.value.some(api => api.id === id) ? id : 0;
});
function loadBookingApis() {
  emit('refreshApis');
}
const bookingChannels = ref<Record<number, GnameChannel[]>>({});
const channelsLoading = ref(false);
const channelsError = ref('');
let channelRequestId = 0;
const selectedChannelOptions = computed(() => bookingChannels.value[activeScheme.value?.api_id || 0] || []);
const missingChannels = computed(() =>
  (activeScheme.value?.channels || []).filter(
    id => !selectedChannelOptions.value.some(item => Number(item.channel) === id)
  )
);

async function loadBookingChannels() {
  channelRequestId += 1;
  const requestId = channelRequestId;
  const apiId = activeScheme.value?.api_id || 0;
  channelsError.value = '';
  if (!apiId) {
    channelsLoading.value = false;
    return;
  }
  channelsLoading.value = true;
  try {
    bookingChannels.value[apiId] = (await fetchBackorderChannels(apiId)).sort(
      (a, b) => Number(a.channel) - Number(b.channel)
    );
  } catch (error) {
    if (requestId === channelRequestId)
      channelsError.value = error instanceof Error ? error.message : '获取 Gname 通道失败';
  } finally {
    if (requestId === channelRequestId) channelsLoading.value = false;
  }
}

watch(
  () => activeScheme.value?.api_id || 0,
  () => {
    loadBookingChannels();
  }
);
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

function getSchemeSummary(scheme: ExceptionSchemeDraft): string {
  const parts: string[] = [];
  if (scheme.lengths) parts.push(`长度:${scheme.lengths}`);
  if (scheme.suffixes) parts.push(`后缀:${scheme.suffixes}`);
  if (scheme.patterns) parts.push(`模式:${scheme.patterns}`);
  if (scheme.contains) parts.push(`包含:${scheme.contains}`);
  if (scheme.domain_composition.length) parts.push(`组成:${compositionText(scheme.domain_composition)}`);
  if (scheme.registration_start || scheme.registration_end)
    parts.push(`注册:${scheme.registration_start || '不限'} ~ ${scheme.registration_end || '不限'}`);
  if (scheme.filing_start || scheme.filing_end)
    parts.push(`备案:${scheme.filing_start || '不限'} ~ ${scheme.filing_end || '不限'}`);
  if (scheme.filing_natures.length) parts.push(`性质:${scheme.filing_natures.join(',')}`);
  let summary = parts.length ? parts.join(' 且 ') : '未配置';
  if (scheme.exclude_chars) summary += `；排除:${scheme.exclude_chars}`;
  if (scheme.channels.length) summary += `；通道顺序:${scheme.channels.join(' → ')}`;
  return summary;
}

function addScheme() {
  if (schemeDrafts.value.length >= 20) {
    window.$message?.warning('最多支持 20 个方案');
    return;
  }
  schemeDrafts.value.push(
    createExceptionSchemeDraft(createExceptionScheme(schemeDrafts.value.length + 1), schemeDrafts.value.length + 1)
  );
  activeSchemeIndex.value = schemeDrafts.value.length - 1;
}
function removeScheme(index: number) {
  if (schemeDrafts.value.length <= 1) return;
  schemeDrafts.value.splice(index, 1);
  activeSchemeIndex.value = Math.min(activeSchemeIndex.value, schemeDrafts.value.length - 1);
}
async function loadSettings() {
  loading.value = true;
  try {
    const saved = await fetchBookingSettings();
    Object.assign(settings, saved);
    schemeDrafts.value = saved.schemes.length
      ? saved.schemes.map((scheme, index) => createExceptionSchemeDraft(scheme, index + 1))
      : [createExceptionSchemeDraft(createExceptionScheme(), 1)];
    activeSchemeIndex.value = 0;
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加载预订设置失败');
  } finally {
    loading.value = false;
  }
}
async function saveSettings() {
  const schemes: BookingScheme[] = schemeDrafts.value.map((draft, index) => ({
    ...draft,
    name: draft.name.trim() || `方案 ${index + 1}`,
    lengths: parseLengths(draft.lengths),
    suffixes: parseTokens(draft.suffixes),
    patterns: parseTokens(draft.patterns).map(value => value.toUpperCase()),
    contains: parseTokens(draft.contains),
    exclude_chars: [...new Set(draft.exclude_chars.toLowerCase().replace(/[，,、\s]+/g, ''))],
    registration_start: draft.registration_start || '',
    registration_end: draft.registration_end || '',
    filing_start: draft.filing_start || '',
    filing_end: draft.filing_end || ''
  }));
  saving.value = true;
  try {
    Object.assign(settings, await saveBookingSettings({ enabled: settings.enabled, schemes }));
    window.$message?.success('预订设置已保存');
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '保存预订设置失败');
  } finally {
    saving.value = false;
  }
}
onMounted(loadSettings);
</script>

<template>
  <ElForm v-loading="loading" label-width="90px" class="pt-8px">
    <div class="mb-12px">
      <ElAlert class="booking-notice" type="info" :closable="false">
        <template #title>
          <div class="w-full flex items-center justify-between gap-16px">
            <span>
              预订会冻结 Gname 账户资金。明确失败才换下一通道；超时或结果不明确时暂停并等待核实。预订成功后继续监控。
            </span>
            <div class="flex shrink-0 items-center gap-8px whitespace-nowrap">
              <span>自动预订</span>
              <ElSwitch v-model="settings.enabled" aria-label="自动预订" />
            </div>
          </div>
        </template>
      </ElAlert>
    </div>
    <div v-if="activeScheme" class="grid gap-12px lg:grid-cols-[260px_minmax(0,1fr)]">
      <div class="min-w-0">
        <div class="h-320px overflow-y-auto border border-gray-200 rounded-6px p-4px dark:border-gray-700">
          <div class="grid gap-4px">
            <button
              v-for="(scheme, index) in schemeDrafts"
              :key="index"
              type="button"
              class="w-full border border-transparent rounded-4px bg-transparent px-8px py-8px text-left transition-colors"
              :class="
                activeSchemeIndex === index ? 'border-primary bg-primary/8' : 'hover:bg-gray-100 dark:hover:bg-gray-800'
              "
              :aria-current="activeSchemeIndex === index ? 'true' : undefined"
              @click="activeSchemeIndex = index"
            >
              <div class="flex items-center gap-6px">
                <span class="min-w-0 flex-1 truncate text-13px font-medium">
                  {{ scheme.name || `方案 ${index + 1}` }}
                </span>
                <ElTag size="small" :type="scheme.enabled ? 'success' : 'info'">
                  {{ scheme.enabled ? '启用' : '停用' }}
                </ElTag>
                <ElTag size="small" type="info">且</ElTag>
              </div>
              <div class="mt-4px truncate text-12px text-gray-500">
                {{ getSchemeSummary(scheme) }}
              </div>
            </button>
          </div>
        </div>
        <ElButton class="mt-8px w-full" plain @click="addScheme">新增预订方案</ElButton>
      </div>

      <div class="min-w-0">
        <ElRow :gutter="16">
          <ElCol :lg="12" :md="12" :sm="24">
            <ElFormItem label="方案名称">
              <ElInput v-model="activeScheme.name" placeholder="方案名称" />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="12" :md="12" :sm="24">
            <ElFormItem label="状态">
              <div class="h-32px w-full flex items-center gap-6px">
                <ElSwitch v-model="activeScheme.enabled" size="small" />
                <span class="text-12px text-gray-500">
                  {{ activeScheme.enabled ? '启用' : '停用' }}
                </span>
                <ElButton
                  v-if="schemeDrafts.length > 1"
                  text
                  type="danger"
                  size="small"
                  @click="removeScheme(activeSchemeIndex)"
                >
                  删除
                </ElButton>
                <ElButton class="ml-auto" type="primary" :loading="saving" :disabled="loading" @click="saveSettings">
                  保存预订设置
                </ElButton>
              </div>
            </ElFormItem>
          </ElCol>
        </ElRow>
        <ElRow :gutter="16">
          <ElCol :lg="8" :md="12" :sm="24">
            <ElFormItem label="长度">
              <ElInput v-model="activeScheme.lengths" class="w-full" placeholder="如 1-4、8" clearable />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="8" :md="12" :sm="24">
            <ElFormItem label="后缀">
              <ElInput v-model="activeScheme.suffixes" class="w-full" placeholder="如 cn,com" clearable />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="8" :md="12" :sm="24">
            <ElFormItem label="包含字符">
              <ElInput v-model="activeScheme.contains" class="w-full" placeholder="如 ai,88" clearable />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="8" :md="12" :sm="24">
            <ElFormItem label="重复模式">
              <ElInput v-model="activeScheme.patterns" class="w-full" placeholder="如 ABAB,ABCABC" clearable />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="8" :md="12" :sm="24">
            <ElFormItem label="排除字符">
              <ElInput v-model="activeScheme.exclude_chars" class="w-full" placeholder="如 0,4,8" clearable />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="8" :md="12" :sm="24">
            <ElFormItem label="域名组成">
              <ElSelect v-model="activeScheme.domain_composition" class="w-full" multiple clearable placeholder="不限">
                <ElOption
                  v-for="item in compositionOptions"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </ElSelect>
            </ElFormItem>
          </ElCol>
        </ElRow>
        <ElRow :gutter="16">
          <ElCol :lg="12" :sm="24">
            <ElFormItem label="注册时间">
              <div class="flex flex-wrap gap-8px">
                <ElDatePicker
                  v-model="activeScheme.registration_start"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="开始日期"
                  aria-label="注册开始日期"
                  class="!w-150px"
                />
                <ElDatePicker
                  v-model="activeScheme.registration_end"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="结束日期"
                  aria-label="注册结束日期"
                  class="!w-150px"
                />
              </div>
            </ElFormItem>
          </ElCol>
          <ElCol :lg="12" :sm="24">
            <ElFormItem label="备案时间">
              <div class="flex flex-wrap gap-8px">
                <ElDatePicker
                  v-model="activeScheme.filing_start"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="开始日期"
                  aria-label="备案开始日期"
                  class="!w-150px"
                />
                <ElDatePicker
                  v-model="activeScheme.filing_end"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="结束日期"
                  aria-label="备案结束日期"
                  class="!w-150px"
                />
              </div>
            </ElFormItem>
          </ElCol>
          <ElCol :lg="12" :sm="24">
            <ElFormItem label="备案性质">
              <ElSelect
                v-model="activeScheme.filing_natures"
                multiple
                filterable
                allow-create
                default-first-option
                placeholder="不限，可输入查询结果中的备案性质"
                class="w-full"
              >
                <ElOption
                  v-for="nature in ['企业', '个人', '事业单位', '政府机关', '社会团体']"
                  :key="nature"
                  :label="nature"
                  :value="nature"
                />
              </ElSelect>
            </ElFormItem>
          </ElCol>
          <ElCol :lg="12" :sm="24">
            <ElFormItem label="Gname 账号">
              <template #label>
                <span class="whitespace-nowrap">Gname 账号</span>
              </template>
              <div class="w-full">
                <div class="flex items-center gap-8px">
                  <ElSelect
                    v-model="activeScheme.api_id"
                    placeholder="选择已配置的 Gname 账号"
                    class="min-w-0 flex-1"
                    @change="activeScheme.channels = []"
                  >
                    <ElOption v-for="api in bookingApis" :key="api.id" :label="api.name" :value="api.id" />
                    <ElOption
                      v-if="missingBookingApi"
                      :value="missingBookingApi"
                      :label="`账号 #${missingBookingApi}（不可用，请重新选择）`"
                      disabled
                    />
                  </ElSelect>
                  <ElButton class="shrink-0" text size="small" @click="loadBookingApis">刷新账号</ElButton>
                </div>
                <div v-if="!bookingApis.length" class="mt-4px text-12px text-gray-500">
                  请先在监控任务 → API 设置中配置并启用 Gname 账号。
                </div>
              </div>
            </ElFormItem>
          </ElCol>
          <ElCol :span="24">
            <ElFormItem label="自选通道">
              <div class="w-full">
                <div class="flex gap-8px">
                  <ElSelect
                    v-model="activeScheme.channels"
                    multiple
                    filterable
                    :loading="channelsLoading"
                    :disabled="!activeScheme.api_id"
                    placeholder="按优先顺序选择通道，可多选"
                    class="min-w-0 flex-1"
                  >
                    <ElOption
                      v-for="channel in selectedChannelOptions"
                      :key="channel.channel"
                      :value="Number(channel.channel)"
                      :disabled="Number(channel.status) !== 1"
                      :label="`${channel.channel_name} (#${channel.channel}) · 价格 ${channel.price} / 保证金 ${channel.deposit} · ${(channel.tlds || []).join(',')} ${Number(channel.status) === 1 ? '' : '（未开放）'}`"
                    />
                    <ElOption
                      v-for="id in missingChannels"
                      :key="`missing-${id}`"
                      :value="id"
                      :label="`通道 #${id}（尚未加载或已不可用）`"
                      disabled
                    />
                  </ElSelect>
                  <ElButton :loading="channelsLoading" :disabled="!activeScheme.api_id" @click="loadBookingChannels">
                    刷新通道
                  </ElButton>
                </div>
                <div v-if="activeScheme.channels.length" class="mt-8px text-13px text-primary" aria-live="polite">
                  预订顺序：{{ activeScheme.channels.map(id => `${id}号通道`).join(' → ') }}
                </div>
                <div class="mt-4px text-12px text-gray-500">
                  先选的先尝试；失败后换下一通道，成功即停止。调整顺序可移除通道后重新选择。
                </div>
                <div v-if="channelsError" role="alert" class="mt-4px text-12px text-red-500">
                  {{ channelsError }}
                </div>
              </div>
            </ElFormItem>
          </ElCol>
        </ElRow>
      </div>
    </div>
  </ElForm>
</template>

<style scoped>
.booking-notice :deep(.el-alert__content) {
  min-width: 0;
  flex: 1;
}
</style>

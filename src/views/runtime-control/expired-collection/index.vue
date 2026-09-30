<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue';
import type { UploadFile, UploadInstance } from 'element-plus';
import {
  type DomainSource,
  type ImportRun,
  type Schedule,
  deleteSchedule,
  deleteSource,
  fetchRuns,
  fetchSchedules,
  fetchSources,
  queueCollection,
  queueLatest,
  queueWestSuffixes,
  runSchedule,
  saveSchedule,
  saveSource,
  testSource,
  uploadTxtCollection
} from '@/service/api';
import { $t } from '@/locales';

interface SourceForm {
  source_key: string;
  name: string;
  adapter: 'gname' | 'west_cn' | 'generic';
  base_url: string;
  page_path: string;
  download_template: string;
  parser: 'line' | 'csv';
  cookie: string;
  clear_cookie: boolean;
  enabled: boolean;
}

const activeTab = ref<'manual' | 'schedule'>('manual');
const sources = ref<DomainSource[]>([]);
const schedules = ref<Schedule[]>([]);
const manualRuns = ref<ImportRun[]>([]);
const scheduledRuns = ref<ImportRun[]>([]);
const loading = ref(false);
const dataRequestPending = ref(false);
const formVisible = ref(false);
const txtDialogVisible = ref(false);
const txtUploading = ref(false);
const txtFile = ref<File>();
const uploadRef = ref<UploadInstance>();
const editingId = ref<number>();
const sourceForm = reactive<SourceForm>(createDefaultForm());
const collectionForm = reactive({
  sourceId: 0,
  date: new Date().toISOString().slice(0, 10),
  format: 'txt',
  suffixes: ''
});
const txtForm = reactive({
  sourceId: 0,
  date: new Date().toISOString().slice(0, 10)
});
const scheduleForm = reactive({
  sourceId: 0,
  name: 'GNAME 每日采集',
  runTime: '08:00',
  suffixes: '',
  enabled: true
});
const editingScheduleId = ref<number>();
const selectedSource = computed(() => sources.value.find(source => source.id === collectionForm.sourceId));
const isWestSource = computed(() => selectedSource.value?.adapter === 'west_cn');
const scheduleSource = computed(() => sources.value.find(source => source.id === scheduleForm.sourceId));
const isScheduleWestSource = computed(() => scheduleSource.value?.adapter === 'west_cn');

function createDefaultForm(): SourceForm {
  return {
    source_key: 'gname',
    name: 'GNAME',
    adapter: 'gname',
    base_url: 'https://www.gname.com',
    page_path: '/download/expired',
    download_template: '/request/downfile?xm=delete&date={date}&lx={format}',
    parser: 'line',
    cookie: '',
    clear_cookie: false,
    enabled: true
  };
}

// eslint-disable-next-line complexity -- initial loading and silent polling share one request path
async function loadData(options: { silent?: boolean } = {}) {
  if (dataRequestPending.value) return;
  dataRequestPending.value = true;
  if (!options.silent) loading.value = true;
  try {
    const [sourceList, scheduleList, manualRunList, scheduledRunList] = options.silent
      ? [undefined, undefined, ...(await Promise.all([fetchRuns(50, 'manual'), fetchRuns(50, 'scheduled')]))]
      : await Promise.all([fetchSources(), fetchSchedules(), fetchRuns(50, 'manual'), fetchRuns(50, 'scheduled')]);
    if (sourceList) sources.value = sourceList;
    if (scheduleList) schedules.value = scheduleList;
    manualRuns.value = manualRunList;
    scheduledRuns.value = scheduledRunList;
    if (!options.silent && sourceList && !collectionForm.sourceId && sourceList.length) {
      collectionForm.sourceId = sourceList[0].id;
    }

    const firstSchedule = scheduleList?.[0];
    if (!options.silent && firstSchedule) {
      scheduleForm.sourceId = firstSchedule.source_id;
      scheduleForm.name = firstSchedule.name;
      scheduleForm.runTime = firstSchedule.run_time;
      scheduleForm.suffixes = firstSchedule.suffixes?.join(',') || '';
      scheduleForm.enabled = Boolean(firstSchedule.enabled);
    } else if (!options.silent && !scheduleForm.sourceId && sourceList?.length) {
      scheduleForm.sourceId = sourceList[0].id;
    }
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加载采集控制台失败');
  } finally {
    dataRequestPending.value = false;
    if (!options.silent) loading.value = false;
  }
}

function openCreate() {
  editingId.value = undefined;
  Object.assign(sourceForm, createDefaultForm());
  formVisible.value = true;
}

function openEdit(source: DomainSource) {
  editingId.value = source.id;
  Object.assign(sourceForm, {
    source_key: source.source_key,
    name: source.name,
    adapter: source.adapter,
    base_url: source.base_url,
    page_path: source.page_path,
    download_template: source.download_template,
    parser: source.parser,
    cookie: '',
    clear_cookie: false,
    enabled: Boolean(source.enabled)
  });
  formVisible.value = true;
}

function handleSourceChange(sourceId: number) {
  const source = sources.value.find(item => item.id === sourceId);
  if (source?.adapter === 'west_cn') collectionForm.format = 'txt';
}

function selectSourceAdapter(adapter: SourceForm['adapter']) {
  if (adapter === 'west_cn') {
    Object.assign(sourceForm, {
      source_key: 'west_cn',
      name: '西部数码',
      base_url: 'https://www.west.cn',
      page_path: '/booking/',
      download_template: '/services/grabnew/newlist.asp',
      parser: 'line'
    });
  } else if (adapter === 'gname') {
    Object.assign(sourceForm, {
      source_key: 'gname',
      name: 'GNAME',
      base_url: 'https://www.gname.com',
      page_path: '/download/expired',
      download_template: '/request/downfile?xm=delete&date={date}&lx={format}',
      parser: 'line'
    });
  }
}

async function handleSaveSource() {
  try {
    await saveSource({ ...sourceForm }, editingId.value);
    window.$message?.success($t('common.updateSuccess'));
    formVisible.value = false;
    await loadData();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '保存数据源失败');
  }
}

async function handleTest(source: DomainSource) {
  try {
    const result = await testSource(source.id);
    window.$message?.success(`连接成功，可下载 ${result.available_dates.length} 个日期`);
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '连接测试失败');
  }
}

async function handleDeleteSource(source: DomainSource) {
  const linked = schedules.value.filter(schedule => schedule.source_id === source.id);
  const scheduleNote = linked.length
    ? `该数据源关联了 ${linked.length} 个定时任务（${linked.map(item => item.name).join('、')}），删除后这些定时任务也会一并删除。`
    : '';
  try {
    await window.$messageBox?.confirm(
      `${scheduleNote}已采集的域名和采集记录会保留。确定删除数据源“${source.name}”吗？`,
      '删除数据源',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' }
    );
  } catch {
    return;
  }
  try {
    const result = await deleteSource(source.id);
    if (editingId.value === source.id) formVisible.value = false;
    if (linked.some(item => item.id === editingScheduleId.value)) editingScheduleId.value = undefined;
    if (collectionForm.sourceId === source.id) collectionForm.sourceId = 0;
    if (scheduleForm.sourceId === source.id) scheduleForm.sourceId = 0;
    if (txtForm.sourceId === source.id) txtForm.sourceId = 0;
    window.$message?.success(
      result.removed_schedules ? `数据源已删除，同时删除了 ${result.removed_schedules} 个定时任务` : '数据源已删除'
    );
    await loadData();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '删除数据源失败');
  }
}

async function handleCollect(latest = false) {
  if (!collectionForm.sourceId) {
    window.$message?.warning('请先选择数据源');
    return;
  }
  if (isWestSource.value) {
    const suffixes = collectionForm.suffixes
      .split(/[\s,，、]+/)
      .map(item => item.trim().toLowerCase().replace(/^\./, ''))
      .filter(Boolean);
    if (!suffixes.length) {
      window.$message?.warning('西部数码请先填写要采集的域名后缀，例如：com,cn,vip');
      return;
    }
    try {
      const result = await queueWestSuffixes({
        source_id: collectionForm.sourceId,
        suffixes
      });
      window.$message?.success(`已创建 ${result.run_ids.length} 个后缀采集任务`);
      await loadData();
    } catch (error) {
      window.$message?.error(error instanceof Error ? error.message : '创建西部数码采集任务失败');
    }
    return;
  }
  try {
    const result = latest
      ? await queueLatest(collectionForm.sourceId, collectionForm.format)
      : await queueCollection({
          source_id: collectionForm.sourceId,
          requested_date: collectionForm.date,
          file_format: collectionForm.format
        });
    window.$message?.success(`采集任务 #${result.run_id} 已创建`);
    await loadData();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '创建采集任务失败');
  }
}

function resetTxtUpload() {
  txtFile.value = undefined;
  uploadRef.value?.clearFiles();
}

function openTxtImport() {
  if (!sources.value.length) {
    window.$message?.warning('请先配置数据源');
    return;
  }
  txtForm.sourceId = collectionForm.sourceId || sources.value[0].id;
  txtForm.date = new Date().toISOString().slice(0, 10);
  resetTxtUpload();
  txtDialogVisible.value = true;
}

function handleTxtFileChange(file: UploadFile) {
  const rawFile = file.raw;
  if (!rawFile) return;
  if (!rawFile.name.toLowerCase().endsWith('.txt')) {
    window.$message?.error('只支持 TXT 文件');
    resetTxtUpload();
    return;
  }
  if (rawFile.size > 50 * 1024 * 1024) {
    window.$message?.error('TXT 文件不能超过 50 MB');
    resetTxtUpload();
    return;
  }
  txtFile.value = rawFile;
}

function handleTxtFileRemove() {
  txtFile.value = undefined;
}

async function handleTxtImport() {
  if (!txtForm.sourceId) {
    window.$message?.warning('请选择数据源');
    return;
  }
  if (!txtFile.value) {
    window.$message?.warning('请选择 TXT 文件');
    return;
  }
  txtUploading.value = true;
  try {
    const result = await uploadTxtCollection({
      sourceId: txtForm.sourceId,
      requestedDate: txtForm.date,
      file: txtFile.value
    });
    window.$message?.success(`TXT 导入任务 #${result.run_id} 已创建`);
    txtDialogVisible.value = false;
    resetTxtUpload();
    await loadData();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '创建 TXT 导入任务失败');
  } finally {
    txtUploading.value = false;
  }
}

async function handleSaveSchedule() {
  if (!scheduleForm.sourceId) {
    window.$message?.warning('请先选择数据源');
    return;
  }
  try {
    const suffixes = scheduleForm.suffixes
      .split(/[\s,，、]+/)
      .map(item => item.trim().toLowerCase().replace(/^\./, ''))
      .filter(Boolean);
    if (isScheduleWestSource.value && !suffixes.length) {
      window.$message?.warning('西部数码定时任务请填写要采集的域名后缀');
      return;
    }
    await saveSchedule(
      {
        source_id: scheduleForm.sourceId,
        name: scheduleForm.name,
        run_time: scheduleForm.runTime,
        suffixes,
        enabled: scheduleForm.enabled
      },
      editingScheduleId.value
    );
    editingScheduleId.value = undefined;
    window.$message?.success($t('common.updateSuccess'));
    await loadData();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '保存定时任务失败');
  }
}

function resetScheduleForm() {
  editingScheduleId.value = undefined;
  scheduleForm.sourceId = sources.value[0]?.id || 0;
  scheduleForm.name = 'GNAME 每日采集';
  scheduleForm.runTime = '08:00';
  scheduleForm.suffixes = '';
  scheduleForm.enabled = true;
}

function handleEditSchedule(schedule: Schedule) {
  editingScheduleId.value = schedule.id;
  scheduleForm.sourceId = schedule.source_id;
  scheduleForm.name = schedule.name;
  scheduleForm.runTime = schedule.run_time;
  scheduleForm.suffixes = schedule.suffixes?.join(',') || '';
  scheduleForm.enabled = Boolean(schedule.enabled);
  activeTab.value = 'schedule';
}

async function handleDeleteSchedule(schedule: Schedule) {
  try {
    await deleteSchedule(schedule.id);
    if (editingScheduleId.value === schedule.id) resetScheduleForm();
    window.$message?.success('定时任务已删除');
    await loadData();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '删除定时任务失败');
  }
}

async function handleRun(schedule: Schedule) {
  try {
    const result = await runSchedule(schedule.id);
    window.$message?.success(
      result.reused ? `今天已执行过采集任务 #${result.run_id}` : `采集任务 #${result.run_id} 已创建`
    );
    await loadData();
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '启动任务失败');
  }
}

function statusType(status: ImportRun['status']): UI.ThemeColor {
  const types: Record<ImportRun['status'], UI.ThemeColor> = {
    queued: 'info',
    running: 'warning',
    success: 'success',
    failure: 'danger'
  };
  return types[status];
}

function formatRunTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function statusLabel(status: ImportRun['status']): string {
  const labels: Record<ImportRun['status'], App.I18n.I18nKey> = {
    queued: 'page.runtime.taskDetails.statuses.queued',
    running: 'page.runtime.taskDetails.statuses.running',
    success: 'page.runtime.taskDetails.statuses.success',
    failure: 'page.runtime.taskDetails.statuses.failure'
  };
  return $t(labels[status]);
}

function triggerLabel(trigger: ImportRun['trigger_type'], filename = ''): string {
  if (trigger.startsWith('manual-west:')) return `手动采集西部数码 _${trigger.slice('manual-west:'.length)}`;
  if (trigger === 'manual-west') {
    const suffix = filename.match(/-\d{4}-\d{2}-\d{2}-(.+)\.txt$/i)?.[1];
    return suffix ? `手动采集西部数码 _${suffix}` : '手动采集西部数码';
  }
  const labels: Record<string, App.I18n.I18nKey> = {
    schedule: 'page.runtime.taskDetails.triggerTypes.schedule',
    'manual-schedule': 'page.runtime.taskDetails.triggerTypes.manual-schedule',
    'manual-latest': 'page.runtime.taskDetails.triggerTypes.manual-latest',
    manual: 'page.runtime.taskDetails.triggerTypes.manual',
    'manual-file': 'page.runtime.taskDetails.triggerTypes.manual-file'
  };
  return labels[trigger] ? $t(labels[trigger]) : trigger;
}

const refreshTimer = setInterval(() => loadData({ silent: true }), 10_000);
loadData();
onBeforeUnmount(() => clearInterval(refreshTimer));
</script>

<template>
  <div class="min-h-500px flex-col-stretch gap-16px overflow-auto">
    <ElCard class="flex-shrink-0 card-wrapper">
      <template #header>
        <div class="flex items-center justify-between gap-12px">
          <div>
            <p class="font-16px font-medium">
              {{ $t('page.runtime.expiredCollection.sourceTitle') }}
            </p>
            <p class="mt-4px text-13px text-gray-500">
              {{ $t('page.runtime.expiredCollection.sourceHint') }}
            </p>
          </div>
          <ElButton type="primary" @click="openCreate">
            <template #icon><icon-ic-round-add class="text-icon" /></template>
            {{ $t('common.add') }}
          </ElButton>
        </div>
      </template>
      <ElTable v-loading="loading" class="min-h-120px" :data="sources" border row-key="id">
        <ElTableColumn prop="name" :label="$t('page.runtime.expiredCollection.sourceName')" min-width="140" />
        <ElTableColumn prop="adapter" :label="$t('page.runtime.expiredCollection.adapter')" width="120">
          <template #default="{ row }">
            <ElTag>{{ row.adapter === 'gname' ? 'GNAME' : row.adapter === 'west_cn' ? '西部数码' : 'HTTP' }}</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="base_url" :label="$t('page.runtime.expiredCollection.baseUrl')" min-width="240" />
        <ElTableColumn :label="$t('page.runtime.expiredCollection.cookie')" width="120">
          <template #default="{ row }">
            <ElTag :type="row.has_cookie ? 'success' : 'warning'">
              {{
                row.has_cookie
                  ? $t('page.runtime.expiredCollection.configured')
                  : $t('page.runtime.expiredCollection.missing')
              }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn :label="$t('common.action')" width="220" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" @click="openEdit(row)">{{ $t('common.edit') }}</ElButton>
            <ElButton link type="primary" @click="handleTest(row)">
              {{ $t('page.runtime.expiredCollection.test') }}
            </ElButton>
            <ElButton link type="danger" @click="handleDeleteSource(row)">{{ $t('common.delete') }}</ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <ElCard v-if="formVisible" class="flex-shrink-0 card-wrapper">
      <template #header>
        <div class="flex items-center justify-between">
          <p>
            {{
              editingId
                ? $t('page.runtime.expiredCollection.editSource')
                : $t('page.runtime.expiredCollection.addSource')
            }}
          </p>
          <ElButton text @click="formVisible = false">{{ $t('common.close') }}</ElButton>
        </div>
      </template>
      <ElForm :model="sourceForm" label-width="132px">
        <ElAlert
          v-if="sourceForm.adapter === 'west_cn'"
          class="mb-12px"
          type="warning"
          :closable="false"
          title="西部数码需要登录 Cookie（CK）：请在浏览器登录 west.cn 后复制完整 Cookie，粘贴到下面的 Cookie 输入框并保存。"
        />
        <ElRow :gutter="24">
          <ElCol :lg="6" :md="12" :sm="24">
            <ElFormItem :label="$t('page.runtime.expiredCollection.sourceKey')">
              <ElInput v-model="sourceForm.source_key" />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="6" :md="12" :sm="24">
            <ElFormItem :label="$t('page.runtime.expiredCollection.sourceName')">
              <ElInput v-model="sourceForm.name" />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="6" :md="12" :sm="24">
            <ElFormItem :label="$t('page.runtime.expiredCollection.adapter')">
              <ElSelect v-model="sourceForm.adapter" class="w-full" @change="selectSourceAdapter">
                <ElOption label="GNAME" value="gname" />
                <ElOption label="西部数码" value="west_cn" />
                <ElOption label="HTTP 通用" value="generic" />
              </ElSelect>
            </ElFormItem>
          </ElCol>
          <ElCol :lg="6" :md="12" :sm="24">
            <ElFormItem :label="$t('page.runtime.expiredCollection.parser')">
              <ElSelect v-model="sourceForm.parser" class="w-full">
                <ElOption label="TXT / 每行一个域名" value="line" />
                <ElOption label="CSV" value="csv" />
              </ElSelect>
            </ElFormItem>
          </ElCol>
          <ElCol :lg="12" :md="24" :sm="24">
            <ElFormItem :label="$t('page.runtime.expiredCollection.baseUrl')">
              <ElInput v-model="sourceForm.base_url" />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="12" :md="24" :sm="24">
            <ElFormItem :label="$t('page.runtime.expiredCollection.pagePath')">
              <ElInput v-model="sourceForm.page_path" />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="12" :md="24" :sm="24">
            <ElFormItem :label="$t('page.runtime.expiredCollection.downloadTemplate')">
              <ElInput v-model="sourceForm.download_template" />
            </ElFormItem>
          </ElCol>
          <ElCol :lg="12" :md="24" :sm="24">
            <ElFormItem
              :label="
                sourceForm.adapter === 'west_cn' ? '登录 Cookie（CK）' : $t('page.runtime.expiredCollection.cookie')
              "
            >
              <ElInput
                v-model="sourceForm.cookie"
                type="password"
                show-password
                :placeholder="
                  sourceForm.adapter === 'west_cn'
                    ? '粘贴西部数码登录后的完整 Cookie'
                    : $t('page.runtime.expiredCollection.cookiePlaceholder')
                "
              />
            </ElFormItem>
          </ElCol>
          <ElCol :span="24">
            <ElFormItem :label="$t('page.runtime.expiredCollection.enabled')">
              <ElSwitch v-model="sourceForm.enabled" />
              <ElCheckbox v-if="editingId" v-model="sourceForm.clear_cookie" class="ml-16px">
                {{ $t('page.runtime.expiredCollection.clearCookie') }}
              </ElCheckbox>
            </ElFormItem>
          </ElCol>
        </ElRow>
        <div class="flex justify-end gap-8px">
          <ElButton @click="formVisible = false">{{ $t('common.cancel') }}</ElButton>
          <ElButton type="primary" @click="handleSaveSource">{{ $t('common.confirm') }}</ElButton>
        </div>
      </ElForm>
    </ElCard>

    <ElCard class="flex-shrink-0 card-wrapper">
      <ElTabs v-model="activeTab" type="card">
        <ElTabPane name="manual" :label="$t('page.runtime.expiredCollection.manualTitle')">
          <ElForm :model="collectionForm" label-width="112px">
            <ElRow :gutter="24">
              <ElCol :lg="8" :md="12" :sm="24">
                <ElFormItem :label="$t('page.runtime.expiredCollection.sourceName')">
                  <ElSelect v-model="collectionForm.sourceId" class="w-full" @change="handleSourceChange">
                    <ElOption v-for="source in sources" :key="source.id" :label="source.name" :value="source.id" />
                  </ElSelect>
                </ElFormItem>
              </ElCol>
              <ElCol v-if="isWestSource" :lg="8" :md="12" :sm="24">
                <ElFormItem label="采集后缀">
                  <ElInput v-model="collectionForm.suffixes" placeholder="例如：com,cn,vip" clearable />
                </ElFormItem>
              </ElCol>
              <ElCol v-if="!isWestSource" :lg="6" :md="12" :sm="24">
                <ElFormItem :label="$t('page.runtime.expiredCollection.date')">
                  <ElDatePicker v-model="collectionForm.date" class="w-full" type="date" value-format="YYYY-MM-DD" />
                </ElFormItem>
              </ElCol>
              <ElCol v-if="!isWestSource" :lg="4" :md="12" :sm="24">
                <ElFormItem :label="$t('page.runtime.expiredCollection.format')">
                  <ElSelect v-model="collectionForm.format" class="w-full">
                    <ElOption label="TXT" value="txt" />
                    <ElOption label="CSV" value="csv" />
                  </ElSelect>
                </ElFormItem>
              </ElCol>
              <ElCol :lg="6" :md="12" :sm="24">
                <ElSpace class="w-full justify-end" alignment="end">
                  <ElButton v-if="!isWestSource" plain @click="openTxtImport">导入 TXT</ElButton>
                  <ElButton v-if="!isWestSource" @click="handleCollect(true)">
                    {{ $t('page.runtime.expiredCollection.latest') }}
                  </ElButton>
                  <ElButton type="primary" @click="handleCollect()">
                    {{ isWestSource ? '开始采集' : $t('page.runtime.expiredCollection.start') }}
                  </ElButton>
                </ElSpace>
              </ElCol>
            </ElRow>
          </ElForm>
          <ElDivider />
          <div class="mb-12px flex items-center justify-between">
            <p class="font-16px font-medium">
              {{ $t('page.runtime.taskDetails.runTitle') }}
            </p>
            <ElButton text :loading="loading" @click="loadData()">{{ $t('common.refresh') }}</ElButton>
          </div>
          <ElTable :data="manualRuns" border>
            <ElTableColumn prop="id" label="ID" width="80" />
            <ElTableColumn prop="source_key" :label="$t('page.runtime.taskDetails.source')" width="130" />
            <ElTableColumn prop="requested_date" :label="$t('page.runtime.taskDetails.date')" width="130" />
            <ElTableColumn :label="$t('page.runtime.taskDetails.trigger')" width="180">
              <template #default="{ row }">{{ triggerLabel(row.trigger_type, row.downloaded_filename) }}</template>
            </ElTableColumn>
            <ElTableColumn :label="$t('page.runtime.taskDetails.status')" width="110">
              <template #default="{ row }">
                <ElTag :type="statusType(row.status)">{{ statusLabel(row.status) }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="total_count" :label="$t('page.runtime.taskDetails.total')" width="100" />
            <ElTableColumn prop="inserted_count" :label="$t('page.runtime.taskDetails.inserted')" width="100" />
            <ElTableColumn prop="updated_count" :label="$t('page.runtime.taskDetails.updated')" width="100" />
            <ElTableColumn
              prop="error"
              :label="$t('page.runtime.taskDetails.error')"
              min-width="240"
              show-overflow-tooltip
            />
          </ElTable>
        </ElTabPane>

        <ElTabPane name="schedule" :label="$t('page.runtime.taskDetails.scheduleTitle')">
          <ElForm :model="scheduleForm" label-width="112px">
            <ElRow :gutter="24">
              <ElCol :lg="6" :md="12" :sm="24">
                <ElFormItem :label="$t('page.runtime.taskDetails.source')">
                  <ElSelect v-model="scheduleForm.sourceId" class="w-full">
                    <ElOption v-for="source in sources" :key="source.id" :label="source.name" :value="source.id" />
                  </ElSelect>
                </ElFormItem>
              </ElCol>
              <ElCol v-if="isScheduleWestSource" :lg="6" :md="12" :sm="24">
                <ElFormItem label="导出后缀">
                  <ElInput v-model="scheduleForm.suffixes" placeholder="例如：com,cn,vip" />
                </ElFormItem>
              </ElCol>
              <ElCol :lg="6" :md="12" :sm="24">
                <ElFormItem :label="$t('page.runtime.taskDetails.name')">
                  <ElInput v-model="scheduleForm.name" />
                </ElFormItem>
              </ElCol>
              <ElCol :lg="4" :md="12" :sm="24">
                <ElFormItem :label="$t('page.runtime.taskDetails.runTime')">
                  <ElTimePicker v-model="scheduleForm.runTime" class="w-full" format="HH:mm" value-format="HH:mm" />
                </ElFormItem>
              </ElCol>
              <ElCol :lg="3" :md="6" :sm="12">
                <ElFormItem :label="$t('page.runtime.taskDetails.enabled')">
                  <ElSwitch v-model="scheduleForm.enabled" />
                </ElFormItem>
              </ElCol>
              <ElCol :lg="5" :md="12" :sm="24">
                <ElFormItem label-width="0">
                  <ElSpace class="w-full" alignment="end">
                    <ElButton @click="resetScheduleForm">新增任务</ElButton>
                    <ElButton type="primary" @click="handleSaveSchedule">
                      {{ editingScheduleId ? '保存修改' : $t('common.confirm') }}
                    </ElButton>
                  </ElSpace>
                </ElFormItem>
              </ElCol>
            </ElRow>
          </ElForm>
          <ElAlert :title="$t('page.runtime.taskDetails.scheduleHint')" type="info" :closable="false" show-icon />
          <ElDivider />
          <div class="mb-12px">
            <p class="font-16px font-medium">
              {{ $t('page.runtime.taskDetails.listTitle') }}
            </p>
          </div>
          <ElTable v-loading="loading" :data="schedules" border row-key="id">
            <ElTableColumn prop="name" :label="$t('page.runtime.taskDetails.name')" min-width="180" />
            <ElTableColumn prop="source_name" :label="$t('page.runtime.taskDetails.source')" width="140" />
            <ElTableColumn prop="run_time" :label="$t('page.runtime.taskDetails.runTime')" width="110" />
            <ElTableColumn :label="$t('page.runtime.taskDetails.lastRun')" min-width="190">
              <template #default="{ row }">
                {{ row.last_run_at ? formatRunTime(row.last_run_at) : $t('page.runtime.taskDetails.never') }}
              </template>
            </ElTableColumn>
            <ElTableColumn :label="$t('common.action')" width="220" fixed="right">
              <template #default="{ row }">
                <ElButton link type="primary" @click="handleEditSchedule(row)">编辑</ElButton>
                <ElButton link type="primary" @click="handleRun(row)">
                  {{ $t('page.runtime.taskDetails.runNow') }}
                </ElButton>
                <ElPopconfirm title="确认删除这个定时任务？" @confirm="handleDeleteSchedule(row)">
                  <template #reference>
                    <ElButton link type="danger">删除</ElButton>
                  </template>
                </ElPopconfirm>
              </template>
            </ElTableColumn>
          </ElTable>
          <ElDivider />
          <div class="mb-12px flex items-center justify-between">
            <p class="font-16px font-medium">
              {{ $t('page.runtime.taskDetails.runTitle') }}
            </p>
            <ElButton text :loading="loading" @click="loadData()">{{ $t('common.refresh') }}</ElButton>
          </div>
          <ElTable :data="scheduledRuns" border>
            <ElTableColumn prop="id" label="ID" width="80" />
            <ElTableColumn prop="source_key" :label="$t('page.runtime.taskDetails.source')" width="130" />
            <ElTableColumn prop="requested_date" :label="$t('page.runtime.taskDetails.date')" width="130" />
            <ElTableColumn :label="$t('page.runtime.taskDetails.trigger')" width="180">
              <template #default="{ row }">{{ triggerLabel(row.trigger_type, row.downloaded_filename) }}</template>
            </ElTableColumn>
            <ElTableColumn :label="$t('page.runtime.taskDetails.status')" width="110">
              <template #default="{ row }">
                <ElTag :type="statusType(row.status)">{{ statusLabel(row.status) }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="total_count" :label="$t('page.runtime.taskDetails.total')" width="100" />
            <ElTableColumn prop="inserted_count" :label="$t('page.runtime.taskDetails.inserted')" width="100" />
            <ElTableColumn prop="updated_count" :label="$t('page.runtime.taskDetails.updated')" width="100" />
            <ElTableColumn
              prop="error"
              :label="$t('page.runtime.taskDetails.error')"
              min-width="240"
              show-overflow-tooltip
            />
          </ElTable>
        </ElTabPane>
      </ElTabs>
    </ElCard>

    <ElDialog v-model="txtDialogVisible" title="导入 TXT 文件" width="520px" destroy-on-close>
      <ElForm :model="txtForm" label-width="88px">
        <ElFormItem label="数据源">
          <ElSelect v-model="txtForm.sourceId" class="w-full">
            <ElOption v-for="source in sources" :key="source.id" :label="source.name" :value="source.id" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="数据日期">
          <ElDatePicker v-model="txtForm.date" class="w-full" type="date" value-format="YYYY-MM-DD" />
        </ElFormItem>
        <ElFormItem label="TXT 文件">
          <ElUpload
            ref="uploadRef"
            class="w-full"
            drag
            :auto-upload="false"
            :limit="1"
            accept=".txt,text/plain"
            :on-change="handleTxtFileChange"
            :on-remove="handleTxtFileRemove"
          >
            <icon-ep-upload-filled class="text-32px text-gray-400" />
            <div class="el-upload__text">
              将 TXT 文件拖到此处，或
              <em>点击选择</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">每行一个域名，重复域名会自动去重，文件大小不超过 50 MB</div>
            </template>
          </ElUpload>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="txtDialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="txtUploading" @click="handleTxtImport">开始导入</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

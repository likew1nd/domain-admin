<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useIntervalFn } from '@vueuse/core';
import { fetchGetVersion, fetchStartUpdate } from '@/service/api';
import { useAuthStore } from '@/store/modules/auth';

defineOptions({ name: 'VersionButton' });

/** 后台定时检查新版本的间隔（后端另有 10 分钟缓存） */
const CHECK_INTERVAL = 30 * 60_000;
/** 更新期间轮询后端恢复情况的间隔 */
const POLL_INTERVAL = 3_000;
/** 超过该时长仍未切换到新版本则提示失败 */
const UPDATE_TIMEOUT = 5 * 60_000;

const authStore = useAuthStore();

const info = ref<Api.SystemSetting.VersionInfo | null>(null);
const visible = ref(false);
const checking = ref(false);
const updating = ref(false);
const updateStartedAt = ref(0);

const isSuper = computed(() => authStore.userInfo.roles.includes('R_SUPER'));
const versionText = computed(() => {
  const current = info.value?.current;
  if (!current) return '';
  return /^\d/.test(current) ? `v${current}` : current;
});

async function loadVersion(refresh = false) {
  checking.value = refresh;
  const { data, error } = await fetchGetVersion(refresh);
  checking.value = false;
  if (!error) info.value = data;
  return error ? null : data;
}

const { pause: stopPolling, resume: startPolling } = useIntervalFn(pollUpdate, POLL_INTERVAL, { immediate: false });

async function pollUpdate() {
  // 更新时后端会重启，期间请求失败属正常情况，继续等待
  const { data, error } = await fetchGetVersion();
  if (error) return;
  const state = data.updateStatus.state;
  if (data.current !== info.value?.current || state === 'success') {
    stopPolling();
    window.$message?.success('更新完成，即将刷新页面');
    setTimeout(() => window.location.reload(), 1500);
    return;
  }
  if (state === 'failed' || Date.now() - updateStartedAt.value > UPDATE_TIMEOUT) {
    stopPolling();
    updating.value = false;
    info.value = data;
    window.$message?.error('更新失败，可在服务器执行 docker logs domain-admin-updater 查看原因');
  }
}

async function handleUpdate() {
  if (!info.value) return;
  try {
    await window.$messageBox?.confirm(
      `将从 ${versionText.value} 更新到 ${info.value.latest}。更新期间服务会重启约 1 分钟，正在运行的查询任务会在重启后自动继续，数据不会丢失。`,
      '确认更新',
      { type: 'warning', confirmButtonText: '立即更新', cancelButtonText: '取消' }
    );
  } catch {
    return;
  }
  const { error } = await fetchStartUpdate();
  if (error) return;
  updating.value = true;
  updateStartedAt.value = Date.now();
  window.$message?.info('正在下载新版本，请勿关闭页面');
  startPolling();
}

function init() {
  loadVersion();
}

useIntervalFn(() => loadVersion(), CHECK_INTERVAL);

onMounted(() => {
  init();
});
</script>

<template>
  <ElPopover v-if="info" v-model:visible="visible" placement="bottom" :width="320" trigger="click">
    <template #reference>
      <ElButton text class="h-36px px-8px!">
        <ElBadge is-dot :hidden="!info.hasUpdate" class="flex-y-center">
          <span class="text-13px text-gray-500">{{ versionText }}</span>
        </ElBadge>
      </ElButton>
    </template>
    <div class="flex-col gap-10px text-13px">
      <div class="flex-y-center justify-between">
        <span class="text-gray-500">当前版本</span>
        <span class="font-medium">{{ versionText }}</span>
      </div>
      <div class="flex-y-center justify-between">
        <span class="text-gray-500">最新版本</span>
        <span v-if="info.latest" class="font-medium">
          {{ info.latest }}
          <ElTag v-if="info.hasUpdate" type="danger" size="small" class="ml-4px" disable-transitions>新</ElTag>
        </span>
        <span v-else class="text-gray-400">—</span>
      </div>
      <p v-if="info.checkError" class="m-0 text-12px text-orange-500">{{ info.checkError }}</p>
      <div v-if="info.hasUpdate && info.releaseNotes" class="release-notes">{{ info.releaseNotes }}</div>
      <p v-if="info.updateStatus.state === 'failed'" class="m-0 text-12px text-red-500">
        上次更新未完成，可在服务器执行
        <code>docker logs domain-admin-updater</code>
        查看原因
      </p>
      <div class="flex justify-end gap-8px">
        <ElButton size="small" :loading="checking" :disabled="updating" @click="loadVersion(true)">检查更新</ElButton>
        <template v-if="info.hasUpdate && isSuper">
          <ElButton v-if="info.updateSupported" size="small" type="primary" :loading="updating" @click="handleUpdate">
            {{ updating ? '更新中…' : '立即更新' }}
          </ElButton>
          <ElTooltip v-else content="当前部署方式不支持在线更新，请在服务器上执行 update 命令" placement="top">
            <ElButton size="small" type="primary" disabled>立即更新</ElButton>
          </ElTooltip>
        </template>
      </div>
    </div>
  </ElPopover>
</template>

<style scoped>
.release-notes {
  max-height: 160px;
  overflow: auto;
  padding: 8px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
  white-space: pre-wrap;
  font-size: 12px;
}
</style>

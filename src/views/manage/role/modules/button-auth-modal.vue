<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue';
import { fetchGetAllButtons, fetchGetRoleButtonAuth, fetchUpdateRoleButtonAuth } from '@/service/api';
import { $t } from '@/locales';

defineOptions({ name: 'ButtonAuthModal' });

interface Props {
  /** the roleId */
  roleId: number;
}

const props = defineProps<Props>();

const visible = defineModel<boolean>('visible', {
  default: false
});

function closeModal() {
  visible.value = false;
}

const title = computed(() => $t('common.edit') + $t('page.manage.role.buttonAuth'));

const loading = ref(false);
const submitting = ref(false);

/** 按钮来自菜单管理中为各菜单配置的按钮 */
const buttons = shallowRef<Api.SystemManage.ButtonOption[]>([]);
const checks = ref<string[]>([]);

async function init() {
  loading.value = true;
  const [buttonsResult, authResult] = await Promise.all([fetchGetAllButtons(), fetchGetRoleButtonAuth(props.roleId)]);
  loading.value = false;
  if (buttonsResult.error || authResult.error) return;

  buttons.value = buttonsResult.data;
  checks.value = authResult.data;
}

async function handleSubmit() {
  submitting.value = true;
  const { error } = await fetchUpdateRoleButtonAuth(props.roleId, checks.value);
  submitting.value = false;
  if (error) return;

  window.$message?.success($t('common.modifySuccess'));
  closeModal();
}

watch(visible, val => {
  if (val) {
    init();
  }
});
</script>

<template>
  <ElDialog v-model="visible" :title="title" class="w-480px">
    <div v-loading="loading" class="h-280px overflow-y-auto">
      <ElCheckboxGroup v-if="buttons.length" v-model="checks" class="flex-col">
        <ElCheckbox v-for="item in buttons" :key="item.code" :value="item.code">
          {{ item.desc || item.code }}
          <span class="text-12px text-gray-400">（{{ item.menuName }} · {{ item.code }}）</span>
        </ElCheckbox>
      </ElCheckboxGroup>
      <ElEmpty v-else-if="!loading" description="暂无按钮，可在菜单管理中为菜单配置按钮" />
    </div>
    <template #footer>
      <ElSpace class="w-full justify-end">
        <ElButton size="small" @click="closeModal">{{ $t('common.cancel') }}</ElButton>
        <ElButton type="primary" size="small" :loading="submitting" @click="handleSubmit">
          {{ $t('common.confirm') }}
        </ElButton>
      </ElSpace>
    </template>
  </ElDialog>
</template>

<style scoped></style>

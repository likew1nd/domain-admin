<script setup lang="ts">
import { computed, nextTick, ref, shallowRef, watch } from 'vue';
import type { TreeInstance } from 'element-plus';
import { fetchGetMenuTree, fetchGetRoleMenuAuth, fetchUpdateRoleMenuAuth } from '@/service/api';
import { $t } from '@/locales';

defineOptions({ name: 'MenuAuthModal' });

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

const title = computed(() => $t('common.edit') + $t('page.manage.role.menuAuth'));

const treeRef = ref<TreeInstance>();
const loading = ref(false);
const submitting = ref(false);

const home = shallowRef('');
const tree = shallowRef<Api.SystemManage.MenuTree[]>([]);
/** 当前勾选的菜单（含半选的上级目录），用于计算可选首页 */
const checkedIds = shallowRef<number[]>([]);

function flattenTree(nodes: Api.SystemManage.MenuTree[]): Api.SystemManage.MenuTree[] {
  return nodes.flatMap(node => [node, ...flattenTree(node.children || [])]);
}

const allNodes = computed(() => flattenTree(tree.value));

/** 首页只能从已勾选的页面中选择 */
const homeOptions = computed(() => {
  const checked = new Set(checkedIds.value);
  return allNodes.value
    .filter(node => node.isPage && checked.has(node.id))
    .map(node => ({ label: node.label, value: node.routeName }));
});

function syncCheckedIds() {
  const instance = treeRef.value;
  if (!instance) return;
  checkedIds.value = [...instance.getCheckedKeys(), ...instance.getHalfCheckedKeys()] as number[];
  if (home.value && !homeOptions.value.some(item => item.value === home.value)) {
    home.value = '';
  }
}

async function init() {
  loading.value = true;
  const [treeResult, authResult] = await Promise.all([fetchGetMenuTree(), fetchGetRoleMenuAuth(props.roleId)]);
  loading.value = false;
  if (treeResult.error || authResult.error) return;

  tree.value = treeResult.data;
  home.value = authResult.data.home;

  // 只回填叶子节点，父级目录的勾选状态由子节点推导，避免部分授权的目录被显示为全选
  const granted = new Set(authResult.data.menuIds);
  const leafKeys = allNodes.value.filter(node => !node.children?.length && granted.has(node.id)).map(node => node.id);
  await nextTick();
  treeRef.value?.setCheckedKeys(leafKeys);
  syncCheckedIds();
}

async function handleSubmit() {
  syncCheckedIds();
  submitting.value = true;
  const { error } = await fetchUpdateRoleMenuAuth(props.roleId, { home: home.value, menuIds: checkedIds.value });
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
    <div v-loading="loading">
      <div class="flex-y-center gap-16px pb-12px">
        <div>{{ $t('page.manage.menu.home') }}</div>
        <ElSelect v-model="home" size="small" clearable placeholder="默认首页" class="w-200px">
          <ElOption v-for="{ value, label } in homeOptions" :key="value" :value="value" :label="label" />
        </ElSelect>
      </div>
      <ElTree
        ref="treeRef"
        :data="tree"
        node-key="id"
        show-checkbox
        default-expand-all
        class="h-320px overflow-y-auto"
        @check="syncCheckedIds"
      />
    </div>
    <template #footer>
      <ElSpace class="w-full justify-end">
        <ElButton size="small" @click="closeModal">
          {{ $t('common.cancel') }}
        </ElButton>
        <ElButton type="primary" size="small" :loading="submitting" @click="handleSubmit">
          {{ $t('common.confirm') }}
        </ElButton>
      </ElSpace>
    </template>
  </ElDialog>
</template>

<style scoped></style>

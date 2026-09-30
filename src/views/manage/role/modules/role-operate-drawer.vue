<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useBoolean } from '@sa/hooks';
import { enableStatusOptions } from '@/constants/business';
import { fetchAddRole, fetchUpdateRole } from '@/service/api';
import { useForm, useFormRules } from '@/hooks/common/form';
import { $t } from '@/locales';
import MenuAuthModal from './menu-auth-modal.vue';
import ButtonAuthModal from './button-auth-modal.vue';

defineOptions({ name: 'RoleOperateDrawer' });

interface Props {
  /** the type of operation */
  operateType: UI.TableOperateType;
  /** the edit row data */
  rowData?: Api.SystemManage.Role | null;
}

const props = defineProps<Props>();

interface Emits {
  (e: 'submitted'): void;
}

const emit = defineEmits<Emits>();

const visible = defineModel<boolean>('visible', {
  default: false
});

const { formRef, validate, restoreValidation } = useForm();
const { defaultRequiredRule } = useFormRules();
const { bool: menuAuthVisible, setTrue: openMenuAuthModal } = useBoolean();
const { bool: buttonAuthVisible, setTrue: openButtonAuthModal } = useBoolean();

const title = computed(() => {
  const titles: Record<UI.TableOperateType, string> = {
    add: $t('page.manage.role.addRole'),
    edit: $t('page.manage.role.editRole')
  };
  return titles[props.operateType];
});

type Model = Api.SystemManage.RoleEdit;

const model = ref(createDefaultModel());

function createDefaultModel(): Model {
  return {
    roleName: '',
    roleCode: '',
    roleDesc: '',
    status: '1'
  };
}

type RuleKey = Exclude<keyof Model, 'roleDesc'>;

const rules: Record<RuleKey, App.Global.FormRule> = {
  roleName: defaultRequiredRule,
  roleCode: defaultRequiredRule,
  status: defaultRequiredRule
};

const roleId = computed(() => props.rowData?.id || -1);

const isEdit = computed(() => props.operateType === 'edit');

function handleInitModel() {
  model.value = createDefaultModel();

  if (props.operateType === 'edit' && props.rowData) {
    const { roleName, roleCode, roleDesc, status } = props.rowData;
    Object.assign(model.value, { roleName, roleCode, roleDesc, status });
  }
}

function closeDrawer() {
  visible.value = false;
}

const submitting = ref(false);

/** 超级管理员始终拥有全部菜单和按钮，无需授权 */
const isSuperRole = computed(() => props.rowData?.roleCode === import.meta.env.VITE_STATIC_SUPER_ROLE);

async function handleSubmit() {
  await validate();

  submitting.value = true;
  const { error } =
    isEdit.value && props.rowData
      ? await fetchUpdateRole(props.rowData.id, model.value)
      : await fetchAddRole(model.value);
  submitting.value = false;
  if (error) return;

  window.$message?.success($t(isEdit.value ? 'common.updateSuccess' : 'common.addSuccess'));
  closeDrawer();
  emit('submitted');
}

watch(visible, () => {
  if (visible.value) {
    handleInitModel();
    restoreValidation();
  }
});
</script>

<template>
  <ElDrawer v-model="visible" :title="title" :size="360">
    <ElForm ref="formRef" :model="model" :rules="rules" label-position="top">
      <ElFormItem :label="$t('page.manage.role.roleName')" prop="roleName">
        <ElInput v-model="model.roleName" :placeholder="$t('page.manage.role.form.roleName')" />
      </ElFormItem>
      <ElFormItem :label="$t('page.manage.role.roleCode')" prop="roleCode">
        <ElInput v-model="model.roleCode" :placeholder="$t('page.manage.role.form.roleCode')" />
      </ElFormItem>
      <ElFormItem :label="$t('page.manage.role.roleStatus')" prop="status">
        <ElRadioGroup v-model="model.status">
          <ElRadio v-for="{ label, value } in enableStatusOptions" :key="value" :value="value" :label="$t(label)" />
        </ElRadioGroup>
      </ElFormItem>
      <ElFormItem :label="$t('page.manage.role.roleDesc')" prop="roleDesc">
        <ElInput v-model="model.roleDesc" :placeholder="$t('page.manage.role.form.roleDesc')" />
      </ElFormItem>
    </ElForm>
    <ElAlert v-if="isEdit && isSuperRole" type="info" :closable="false" title="超级管理员拥有全部菜单和按钮权限" />
    <ElSpace v-else-if="isEdit">
      <ElButton @click="openMenuAuthModal">{{ $t('page.manage.role.menuAuth') }}</ElButton>
      <MenuAuthModal v-model:visible="menuAuthVisible" :role-id="roleId" />
      <ElButton @click="openButtonAuthModal">{{ $t('page.manage.role.buttonAuth') }}</ElButton>
      <ButtonAuthModal v-model:visible="buttonAuthVisible" :role-id="roleId" />
    </ElSpace>
    <template #footer>
      <ElSpace :size="16">
        <ElButton @click="closeDrawer">{{ $t('common.cancel') }}</ElButton>
        <ElButton type="primary" :loading="submitting" @click="handleSubmit">{{ $t('common.confirm') }}</ElButton>
      </ElSpace>
    </template>
  </ElDrawer>
</template>

<style scoped></style>

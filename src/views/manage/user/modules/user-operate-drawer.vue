<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { enableStatusOptions, userGenderOptions } from '@/constants/business';
import { fetchAddUser, fetchGetAllRoles, fetchUpdateUser } from '@/service/api';
import { useForm, useFormRules } from '@/hooks/common/form';
import { $t } from '@/locales';

defineOptions({ name: 'UserOperateDrawer' });

interface Props {
  /** the type of operation */
  operateType: UI.TableOperateType;
  /** the edit row data */
  rowData?: Api.SystemManage.User | null;
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
const { defaultRequiredRule, formRules, patternRules } = useFormRules();

const title = computed(() => {
  const titles: Record<UI.TableOperateType, string> = {
    add: $t('page.manage.user.addUser'),
    edit: $t('page.manage.user.editUser')
  };
  return titles[props.operateType];
});

type Model = Api.SystemManage.UserEdit;

const model = ref(createDefaultModel());

function createDefaultModel(): Model {
  return {
    userName: '',
    userGender: undefined,
    nickName: '',
    userPhone: '',
    userEmail: '',
    userRoles: [],
    status: '1',
    password: ''
  };
}

const isAdd = computed(() => props.operateType === 'add');

/** 新增时必须设置密码，编辑时留空表示不修改密码 */
const rules = computed<Record<string, App.Global.FormRule | App.Global.FormRule[]>>(() => ({
  userName: formRules.userName,
  password: isAdd.value ? formRules.pwd : [patternRules.pwd],
  userPhone: [patternRules.phone],
  userEmail: [patternRules.email],
  status: defaultRequiredRule
}));

/** the enabled role options */
const roleOptions = ref<CommonType.Option<string>[]>([]);

async function getRoleOptions() {
  const { error, data } = await fetchGetAllRoles();

  if (!error) {
    roleOptions.value = data.map(item => ({
      label: item.roleName,
      value: item.roleCode
    }));
  }
}

function handleInitModel() {
  model.value = createDefaultModel();

  if (props.operateType === 'edit' && props.rowData) {
    const { userName, userGender, nickName, userPhone, userEmail, userRoles, status } = props.rowData;
    Object.assign(model.value, { userName, userGender, nickName, userPhone, userEmail, userRoles, status });
  }
}

function closeDrawer() {
  visible.value = false;
}

const submitting = ref(false);

async function handleSubmit() {
  await validate();

  const { password, ...rest } = model.value;
  const params: Model = { ...rest, ...(password ? { password } : {}) };

  submitting.value = true;
  const { error } =
    isAdd.value || !props.rowData ? await fetchAddUser(params) : await fetchUpdateUser(props.rowData.id, params);
  submitting.value = false;
  if (error) return;

  window.$message?.success($t(isAdd.value ? 'common.addSuccess' : 'common.updateSuccess'));
  closeDrawer();
  emit('submitted');
}

watch(visible, () => {
  if (visible.value) {
    handleInitModel();
    restoreValidation();
    getRoleOptions();
  }
});
</script>

<template>
  <ElDrawer v-model="visible" :title="title" :size="360">
    <ElForm ref="formRef" :model="model" :rules="rules" label-position="top">
      <ElFormItem :label="$t('page.manage.user.userName')" prop="userName">
        <ElInput v-model="model.userName" :placeholder="$t('page.manage.user.form.userName')" />
      </ElFormItem>
      <ElFormItem label="密码" prop="password">
        <ElInput
          v-model="model.password"
          type="password"
          show-password
          autocomplete="new-password"
          :placeholder="isAdd ? '6-18 位字母、数字或下划线' : '留空表示不修改密码'"
        />
      </ElFormItem>
      <ElFormItem :label="$t('page.manage.user.userGender')" prop="userGender">
        <ElRadioGroup v-model="model.userGender">
          <ElRadio v-for="item in userGenderOptions" :key="item.value" :value="item.value" :label="$t(item.label)" />
        </ElRadioGroup>
      </ElFormItem>
      <ElFormItem :label="$t('page.manage.user.nickName')" prop="nickName">
        <ElInput v-model="model.nickName" :placeholder="$t('page.manage.user.form.nickName')" />
      </ElFormItem>
      <ElFormItem :label="$t('page.manage.user.userPhone')" prop="userPhone">
        <ElInput v-model="model.userPhone" :placeholder="$t('page.manage.user.form.userPhone')" />
      </ElFormItem>
      <ElFormItem :label="$t('page.manage.user.userEmail')" prop="userEmail">
        <ElInput v-model="model.userEmail" :placeholder="$t('page.manage.user.form.userEmail')" />
      </ElFormItem>
      <ElFormItem :label="$t('page.manage.user.userStatus')" prop="status">
        <ElRadioGroup v-model="model.status">
          <ElRadio v-for="item in enableStatusOptions" :key="item.value" :value="item.value" :label="$t(item.label)" />
        </ElRadioGroup>
      </ElFormItem>
      <ElFormItem :label="$t('page.manage.user.userRole')" prop="userRoles">
        <ElSelect v-model="model.userRoles" multiple :placeholder="$t('page.manage.user.form.userRole')">
          <ElOption v-for="{ label, value } in roleOptions" :key="value" :label="label" :value="value" />
        </ElSelect>
      </ElFormItem>
    </ElForm>
    <template #footer>
      <ElSpace :size="16">
        <ElButton @click="closeDrawer">{{ $t('common.cancel') }}</ElButton>
        <ElButton type="primary" :loading="submitting" @click="handleSubmit">{{ $t('common.confirm') }}</ElButton>
      </ElSpace>
    </template>
  </ElDrawer>
</template>

<style scoped></style>

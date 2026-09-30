<script setup lang="ts">
import { computed, ref } from 'vue';
import { fetchResetPassword } from '@/service/api';
import { useRouterPush } from '@/hooks/common/router';
import { useForm, useFormRules } from '@/hooks/common/form';
import EmailCodeInput from './email-code-input.vue';

defineOptions({ name: 'ResetPwd' });

const { toggleLoginModule } = useRouterPush();
const { formRef, validate } = useForm();

interface FormModel {
  email: string;
  code: string;
  password: string;
  confirmPassword: string;
}

const model = ref<FormModel>({
  email: '',
  code: '',
  password: '',
  confirmPassword: ''
});

const submitting = ref(false);

const rules = computed<Record<keyof FormModel, App.Global.FormRule[]>>(() => {
  const { formRules, createConfirmPwdRule } = useFormRules();

  return {
    email: formRules.email,
    code: formRules.code,
    password: formRules.pwd,
    confirmPassword: createConfirmPwdRule(model.value.password)
  };
});

async function handleSubmit() {
  await validate();
  submitting.value = true;
  const { error } = await fetchResetPassword({
    email: model.value.email.trim(),
    code: model.value.code.trim(),
    password: model.value.password
  });
  submitting.value = false;
  if (error) return;
  window.$message?.success('密码已重置，请使用新密码登录');
  toggleLoginModule('pwd-login');
}
</script>

<template>
  <ElForm ref="formRef" :model="model" :rules="rules" size="large" :show-label="false" @keyup.enter="handleSubmit">
    <ElFormItem prop="email">
      <ElInput v-model="model.email" placeholder="请输入账号绑定的邮箱" />
    </ElFormItem>
    <ElFormItem prop="code">
      <EmailCodeInput v-model="model.code" purpose="reset" :email="model.email" />
    </ElFormItem>
    <ElFormItem prop="password">
      <ElInput v-model="model.password" type="password" show-password placeholder="新密码：6-18 位字母、数字或下划线" />
    </ElFormItem>
    <ElFormItem prop="confirmPassword">
      <ElInput
        v-model="model.confirmPassword"
        type="password"
        show-password
        :placeholder="$t('page.login.common.confirmPasswordPlaceholder')"
      />
    </ElFormItem>
    <ElSpace direction="vertical" fill :size="18" class="w-full">
      <ElButton type="primary" size="large" round :loading="submitting" @click="handleSubmit">
        {{ $t('common.confirm') }}
      </ElButton>
      <ElButton size="large" round @click="toggleLoginModule('pwd-login')">
        {{ $t('page.login.common.back') }}
      </ElButton>
    </ElSpace>
  </ElForm>
</template>

<style scoped></style>

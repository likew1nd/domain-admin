<script setup lang="ts">
import { computed, ref } from 'vue';
import { useAuthStore } from '@/store/modules/auth';
import { useRouterPush } from '@/hooks/common/router';
import { useForm, useFormRules } from '@/hooks/common/form';
import EmailCodeInput from './email-code-input.vue';

defineOptions({ name: 'CodeLogin' });

const authStore = useAuthStore();
const { toggleLoginModule } = useRouterPush();
const { formRef, validate } = useForm();

interface FormModel {
  email: string;
  code: string;
}

const model = ref<FormModel>({ email: '', code: '' });

const rules = computed<Record<keyof FormModel, App.Global.FormRule[]>>(() => {
  const { formRules } = useFormRules();

  return { email: formRules.email, code: formRules.code };
});

async function handleSubmit() {
  await validate();
  await authStore.loginByEmail(model.value.email.trim(), model.value.code.trim());
}
</script>

<template>
  <ElForm ref="formRef" :model="model" :rules="rules" size="large" :show-label="false" @keyup.enter="handleSubmit">
    <ElFormItem prop="email">
      <ElInput v-model="model.email" placeholder="请输入账号绑定的邮箱" />
    </ElFormItem>
    <ElFormItem prop="code">
      <EmailCodeInput v-model="model.code" purpose="login" :email="model.email" />
    </ElFormItem>
    <ElSpace direction="vertical" :size="18" fill class="w-full">
      <ElButton type="primary" size="large" round block :loading="authStore.loginLoading" @click="handleSubmit">
        {{ $t('common.confirm') }}
      </ElButton>
      <ElButton size="large" round block @click="toggleLoginModule('pwd-login')">
        {{ $t('page.login.common.back') }}
      </ElButton>
    </ElSpace>
  </ElForm>
</template>

<style scoped></style>

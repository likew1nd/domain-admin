<script setup lang="ts">
import { computed, ref } from 'vue';
import { fetchRegister } from '@/service/api';
import { useAppStore } from '@/store/modules/app';
import { useRouterPush } from '@/hooks/common/router';
import { useForm, useFormRules } from '@/hooks/common/form';
import CaptchaInput from './captcha-input.vue';
import EmailCodeInput from './email-code-input.vue';

defineOptions({ name: 'Register' });

const appStore = useAppStore();
const { toggleLoginModule } = useRouterPush();
const { formRef, validate } = useForm();

interface FormModel {
  userName: string;
  email: string;
  emailCode: string;
  captchaCode: string;
  password: string;
  confirmPassword: string;
}

const model = ref<FormModel>({
  userName: '',
  email: '',
  emailCode: '',
  captchaCode: '',
  password: '',
  confirmPassword: ''
});

const submitting = ref(false);
const captchaRef = ref<InstanceType<typeof CaptchaInput>>();
// 系统配置了发信邮箱时注册必须验证邮箱，否则用图形验证码防止批量注册
const needEmail = computed(() => appStore.systemSettings.registerEmailRequired);

const rules = computed<Partial<Record<keyof FormModel, App.Global.FormRule[]>>>(() => {
  const { formRules, createConfirmPwdRule, createRequiredRule } = useFormRules();

  return {
    userName: formRules.userName,
    password: formRules.pwd,
    confirmPassword: createConfirmPwdRule(model.value.password),
    ...(needEmail.value
      ? { email: formRules.email, emailCode: formRules.code }
      : { captchaCode: [createRequiredRule('请输入图形验证码')] })
  };
});

async function handleSubmit() {
  await validate();
  submitting.value = true;
  const { userName, password, email, emailCode } = model.value;
  const { data, error } = await fetchRegister(
    needEmail.value
      ? { userName, password, email: email.trim(), emailCode: emailCode.trim() }
      : { userName, password, ...captchaRef.value?.answer() }
  );
  submitting.value = false;
  if (error) {
    captchaRef.value?.refresh();
    return;
  }
  window.$message?.success(data.needApproval ? '注册成功，请等待管理员审核后再登录' : '注册成功，请登录');
  toggleLoginModule('pwd-login');
}
</script>

<template>
  <ElForm
    ref="formRef"
    :model="model"
    :rules="rules"
    size="large"
    :show-label="false"
    :validate-on-rule-change="false"
    @keyup.enter="handleSubmit"
  >
    <ElFormItem prop="userName">
      <ElInput v-model="model.userName" placeholder="用户名：4-16 位中文、字母、数字、下划线或短横线" />
    </ElFormItem>
    <template v-if="needEmail">
      <ElFormItem prop="email">
        <ElInput v-model="model.email" placeholder="请输入邮箱，可用于验证码登录和找回密码" />
      </ElFormItem>
      <ElFormItem prop="emailCode">
        <EmailCodeInput v-model="model.emailCode" purpose="register" :email="model.email" />
      </ElFormItem>
    </template>
    <ElFormItem prop="password">
      <ElInput v-model="model.password" type="password" show-password placeholder="密码：6-18 位字母、数字或下划线" />
    </ElFormItem>
    <ElFormItem prop="confirmPassword">
      <ElInput
        v-model="model.confirmPassword"
        type="password"
        show-password
        :placeholder="$t('page.login.common.confirmPasswordPlaceholder')"
      />
    </ElFormItem>
    <ElFormItem v-if="!needEmail" prop="captchaCode">
      <CaptchaInput ref="captchaRef" v-model="model.captchaCode" />
    </ElFormItem>
    <ElSpace direction="vertical" :size="18" fill class="w-full">
      <ElButton type="primary" size="large" round block :loading="submitting" @click="handleSubmit">
        {{ $t('common.confirm') }}
      </ElButton>
      <ElButton size="large" round @click="toggleLoginModule('pwd-login')">
        {{ $t('page.login.common.back') }}
      </ElButton>
    </ElSpace>
  </ElForm>
</template>

<style scoped></style>

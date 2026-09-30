<script setup lang="ts">
import { computed, ref } from 'vue';
import { loginModuleRecord } from '@/constants/app';
import { useAppStore } from '@/store/modules/app';
import { useAuthStore } from '@/store/modules/auth';
import { useRouterPush } from '@/hooks/common/router';
import { useForm, useFormRules } from '@/hooks/common/form';
import { localStg } from '@/utils/storage';
import { $t } from '@/locales';
import CaptchaInput from './captcha-input.vue';

defineOptions({ name: 'PwdLogin' });

const appStore = useAppStore();
const authStore = useAuthStore();
const { toggleLoginModule } = useRouterPush();
const { formRef, validate } = useForm();

interface FormModel {
  userName: string;
  password: string;
  captchaCode: string;
}

const rememberedUserName = localStg.get('rememberedUserName') || '';
const rememberMe = ref(Boolean(rememberedUserName));
const captchaRef = ref<InstanceType<typeof CaptchaInput>>();

const model = ref<FormModel>({
  userName: rememberedUserName,
  password: '',
  captchaCode: ''
});

const settings = computed(() => appStore.systemSettings);

const rules = computed<Partial<Record<keyof FormModel, App.Global.FormRule[]>>>(() => {
  // inside computed to make locale ref, if not apply i18n, you can define it without computed
  const { formRules, createRequiredRule } = useFormRules();

  return {
    userName: formRules.userName,
    // 登录只校验必填，密码强度由注册/改密时校验，否则初始密码 admin 无法登录
    password: [createRequiredRule($t('form.pwd.required'))],
    captchaCode: settings.value.captchaEnabled ? [createRequiredRule('请输入图形验证码')] : []
  };
});

async function handleSubmit() {
  await validate();
  if (rememberMe.value) {
    localStg.set('rememberedUserName', model.value.userName);
  } else {
    localStg.remove('rememberedUserName');
  }
  const captcha = settings.value.captchaEnabled ? captchaRef.value?.answer() : undefined;
  const pass = await authStore.login(model.value.userName, model.value.password, { captcha });
  if (!pass && settings.value.captchaEnabled) captchaRef.value?.refresh();
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
      <ElInput v-model="model.userName" :placeholder="$t('page.login.common.userNamePlaceholder')" />
    </ElFormItem>
    <ElFormItem prop="password">
      <ElInput
        v-model="model.password"
        type="password"
        show-password
        :placeholder="$t('page.login.common.passwordPlaceholder')"
      />
    </ElFormItem>
    <ElFormItem v-if="settings.captchaEnabled" prop="captchaCode">
      <CaptchaInput ref="captchaRef" v-model="model.captchaCode" />
    </ElFormItem>
    <ElSpace direction="vertical" :size="24" class="w-full" fill>
      <div class="flex-y-center justify-between">
        <ElCheckbox v-model="rememberMe">{{ $t('page.login.pwdLogin.rememberMe') }}</ElCheckbox>
        <ElButton v-if="settings.passwordResetEnabled" text @click="toggleLoginModule('reset-pwd')">
          {{ $t('page.login.pwdLogin.forgetPassword') }}
        </ElButton>
      </div>
      <ElButton type="primary" size="large" round block :loading="authStore.loginLoading" @click="handleSubmit">
        {{ $t('common.confirm') }}
      </ElButton>
      <div v-if="settings.emailLoginEnabled || settings.registerEnabled" class="flex-y-center justify-between gap-12px">
        <ElButton
          v-if="settings.emailLoginEnabled"
          class="flex-1"
          size="default"
          @click="toggleLoginModule('code-login')"
        >
          {{ $t(loginModuleRecord['code-login']) }}
        </ElButton>
        <ElButton v-if="settings.registerEnabled" class="flex-1" size="default" @click="toggleLoginModule('register')">
          {{ $t(loginModuleRecord.register) }}
        </ElButton>
      </div>
    </ElSpace>
  </ElForm>
</template>

<style scoped></style>

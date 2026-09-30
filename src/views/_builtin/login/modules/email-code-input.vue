<script setup lang="ts">
import { computed, ref } from 'vue';
import { useCountDown, useLoading } from '@sa/hooks';
import { REG_EMAIL } from '@/constants/reg';
import { fetchSendEmailCode } from '@/service/api';
import CaptchaInput from './captcha-input.vue';

defineOptions({ name: 'EmailCodeInput' });

interface Props {
  purpose: 'login' | 'reset' | 'register';
  email: string;
}

const props = defineProps<Props>();

const code = defineModel<string>({ default: '' });

const { loading, startLoading, endLoading } = useLoading();
const { count, start, isCounting } = useCountDown(60);
const captchaVisible = ref(false);
const captchaCode = ref('');
const captchaRef = ref<InstanceType<typeof CaptchaInput>>();

const label = computed(() => (isCounting.value ? `${count.value} 秒后重新获取` : '获取验证码'));

function openCaptcha() {
  if (!REG_EMAIL.test(props.email.trim())) {
    window.$message?.error('请输入正确的邮箱');
    return;
  }
  captchaVisible.value = true;
  captchaRef.value?.refresh();
}

async function send() {
  if (!captchaCode.value.trim()) {
    window.$message?.warning('请输入图形验证码');
    return;
  }
  startLoading();
  const { error } = await fetchSendEmailCode({
    purpose: props.purpose,
    email: props.email.trim(),
    ...captchaRef.value?.answer()
  });
  endLoading();
  if (error) {
    captchaRef.value?.refresh();
    return;
  }
  captchaVisible.value = false;
  window.$message?.success('如果该邮箱可用，验证码已发送，请查收邮件');
  start();
}
</script>

<template>
  <div class="w-full flex-y-center gap-16px">
    <ElInput v-model="code" placeholder="请输入邮箱验证码" maxlength="6" />
    <ElButton size="large" :disabled="isCounting" @click="openCaptcha">{{ label }}</ElButton>
  </div>
  <ElDialog v-model="captchaVisible" title="安全验证" width="360px" append-to-body>
    <CaptchaInput ref="captchaRef" v-model="captchaCode" @keyup.enter="send" />
    <template #footer>
      <ElButton @click="captchaVisible = false">取消</ElButton>
      <ElButton type="primary" :loading="loading" @click="send">发送验证码</ElButton>
    </template>
  </ElDialog>
</template>

<style scoped></style>

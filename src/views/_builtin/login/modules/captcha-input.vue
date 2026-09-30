<script setup lang="ts">
import { ref } from 'vue';
import { fetchCaptcha } from '@/service/api';

defineOptions({ name: 'CaptchaInput' });

const code = defineModel<string>({ default: '' });

const captchaId = ref('');
const image = ref('');

async function refresh() {
  code.value = '';
  const { data, error } = await fetchCaptcha();
  if (error) return;
  captchaId.value = data.captchaId;
  image.value = data.image;
}

function answer() {
  return { captchaId: captchaId.value, captchaCode: code.value };
}

refresh();

defineExpose({ refresh, answer });
</script>

<template>
  <div class="w-full flex-y-center gap-12px">
    <ElInput v-model="code" placeholder="请输入图形验证码" maxlength="4" />
    <img
      v-if="image"
      :src="image"
      class="h-40px w-120px flex-shrink-0 cursor-pointer rd-4px"
      alt="验证码"
      title="看不清？点击换一张"
      @click="refresh"
    />
  </div>
</template>

<style scoped></style>

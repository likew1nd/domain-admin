<script setup lang="ts">
import { watch } from 'vue';
import { type ECOption, useEcharts } from '@/hooks/common/echarts';

defineOptions({ name: 'DashChart' });

interface Props {
  option: ECOption;
  height?: string;
}

const props = withDefaults(defineProps<Props>(), { height: '300px' });

const { domRef, updateOptions } = useEcharts(() => props.option);

watch(
  () => props.option,
  option => updateOptions(() => option)
);
</script>

<template>
  <div ref="domRef" class="w-full" :style="{ height }"></div>
</template>

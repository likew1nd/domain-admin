<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import type { InputInstance } from 'element-plus';
import { importGeneratedDomains } from '@/service/api';
import { loadDomainDictionaries } from '@/utils/domain-dictionary';
import { loadDomainSuffixes } from '@/utils/domain-suffix';

interface GeneratedRow {
  domain: string;
  suffix: string;
}
const MAX_ROWS = 200000;
const commonSuffixes = ['.com', '.cn', '.net'];
const dictionaries = ref(loadDomainDictionaries());
const allSuffixes = loadDomainSuffixes();
const suffixes = ref(allSuffixes.filter(item => commonSuffixes.includes(item)));
const rule = ref('{常见单词}{数字无04}');
const ruleInput = ref<InputInstance>();
const cursor = ref(rule.value.length);
const excluded = ref('');
const rows = ref<GeneratedRow[]>([]);
const generating = ref(false);
const adding = ref(false);
const keyword = ref('');
const selectedSuffix = ref('all');
const page = ref(1);
const pageSize = ref(50);

const variables = computed(() => [...new Set([...rule.value.matchAll(/\{([^{}]+)\}/g)].map(match => match[1]))]);
const missingVariables = computed(() => variables.value.filter(name => !dictionaryValues(name).length));
const estimate = computed(
  () =>
    variables.value.reduce((total, name) => total * Math.max(dictionaryValues(name).length, 1), 1) *
    Math.max(suffixes.value.length, 1)
);
const example = computed(() => {
  if (!rule.value.trim() || missingVariables.value.length) return '';
  const label = variables.value
    .reduce((text, name) => text.split(`{${name}}`).join(dictionaryValues(name)[0]), rule.value.trim())
    .toLowerCase();
  return `${label}${suffixes.value[0] || ''}`;
});
const missingText = computed(() => missingVariables.value.map(item => `{${item}}`).join('、'));
const resultSuffixes = computed(() => [...new Set(rows.value.map(item => item.suffix))]);
const filteredRows = computed(() => {
  const text = keyword.value.trim().toLowerCase();
  return rows.value.filter(
    row =>
      (!text || row.domain.includes(text)) && (selectedSuffix.value === 'all' || row.suffix === selectedSuffix.value)
  );
});
const pagedRows = computed(() =>
  filteredRows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value)
);
watch([keyword, selectedSuffix], () => {
  page.value = 1;
});

function dictionaryValues(name: string) {
  const group = dictionaries.value.find(item => item.name === name || item.id === name);
  if (!group || !group.enabled) return [];
  return [...new Set(group.words.map(item => item.trim().toLowerCase()).filter(Boolean))];
}
function rememberCursor() {
  cursor.value = ruleInput.value?.input?.selectionStart ?? rule.value.length;
}
async function insertVariable(name: string) {
  const token = `{${name}}`;
  const position = Math.min(cursor.value, rule.value.length);
  rule.value = `${rule.value.slice(0, position)}${token}${rule.value.slice(position)}`;
  cursor.value = position + token.length;
  await nextTick();
  ruleInput.value?.focus();
  ruleInput.value?.input?.setSelectionRange(cursor.value, cursor.value);
}
function removeLastVariable() {
  rule.value = rule.value.replace(/\{[^{}]+\}$|.$/u, '');
  cursor.value = rule.value.length;
}
function selectCommonSuffixes() {
  suffixes.value = allSuffixes.filter(item => commonSuffixes.includes(item));
}
function toggleSuffix(suffix: string) {
  suffixes.value = suffixes.value.includes(suffix)
    ? suffixes.value.filter(item => item !== suffix)
    : [...suffixes.value, suffix];
}
async function generateDomains() {
  const template = rule.value.trim();
  let warning = '';
  if (!template) warning = '请输入生成规则';
  else if (!suffixes.value.length) warning = '请至少选择一个域名后缀';
  else if (missingVariables.value.length) warning = `词典不存在或没有启用词条：${missingVariables.value.join('、')}`;
  if (warning) {
    window.$message?.warning(warning);
    return;
  }
  generating.value = true;
  // Let the loading state render before the synchronous work starts
  await new Promise(resolve => {
    setTimeout(resolve, 16);
  });
  const names = variables.value;
  const values = names.map(name => dictionaryValues(name));
  const forbidden = new Set([...excluded.value.toLowerCase()]);
  const labels: string[] = [];
  const walk = (index: number, current: string) => {
    if (labels.length >= MAX_ROWS) return;
    if (index === names.length) {
      if (current && ![...current].some(char => forbidden.has(char))) labels.push(current.toLowerCase());
      return;
    }
    const marker = `{${names[index]}}`;
    for (const value of values[index]) walk(index + 1, current.split(marker).join(value));
  };
  walk(0, template);
  const result: GeneratedRow[] = [];
  const seen = new Set<string>();
  for (const label of labels) {
    for (const suffix of suffixes.value) {
      const domain = `${label}${suffix}`;
      if (result.length < MAX_ROWS && !seen.has(domain)) {
        seen.add(domain);
        result.push({ domain, suffix });
      }
    }
  }
  rows.value = result;
  page.value = 1;
  generating.value = false;
  const capped = result.length >= MAX_ROWS ? `（已达上限 ${MAX_ROWS.toLocaleString()}）` : '';
  window.$message?.success(`已生成 ${result.length.toLocaleString()} 个域名${capped}`);
}
function exportTxt() {
  if (!filteredRows.value.length) return;
  const blob = new Blob([filteredRows.value.map(item => item.domain).join('\n')], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `generated-domains-${Date.now()}.txt`;
  link.click();
  URL.revokeObjectURL(url);
}
async function addToExpired() {
  if (!filteredRows.value.length) return;
  adding.value = true;
  try {
    const result = await importGeneratedDomains(filteredRows.value.map(item => item.domain));
    window.$message?.success(`已加入过期域名列表：新增 ${result.inserted}，更新 ${result.updated}`);
  } catch (error) {
    window.$message?.error(error instanceof Error ? error.message : '加入失败');
  } finally {
    adding.value = false;
  }
}
function clearRows() {
  rows.value = [];
  keyword.value = '';
  selectedSuffix.value = 'all';
}
</script>

<template>
  <div class="generator-page">
    <ElCard shadow="never" class="hero card-wrapper">
      <div class="hero-body">
        <div class="hero-title">
          <div class="hero-icon"><icon-mdi-auto-fix /></div>
          <div>
            <h2>域名生成</h2>
            <p>
              用
              <code>{词典名称}</code>
              组合规则批量生成候选域名，可导出 TXT 或直接加入过期域名列表
            </p>
          </div>
        </div>
        <div class="stats">
          <div class="stat">
            <span>预计组合</span>
            <strong>{{ estimate.toLocaleString() }}</strong>
          </div>
          <div class="stat">
            <span>已选后缀</span>
            <strong>{{ suffixes.length }}</strong>
          </div>
          <div class="stat">
            <span>生成结果</span>
            <strong>{{ rows.length.toLocaleString() }}</strong>
          </div>
        </div>
      </div>
    </ElCard>

    <ElCard shadow="never" class="card-wrapper">
      <div class="section">
        <div class="section-label">生成规则</div>
        <div class="section-body">
          <ElInput
            ref="ruleInput"
            v-model="rule"
            size="large"
            placeholder="点击下方词典插入变量，例如 {常见单词}{数字无04}"
            clearable
            @blur="rememberCursor"
            @keyup="rememberCursor"
            @click="rememberCursor"
          >
            <template #prefix><icon-mdi-code-braces class="text-icon" /></template>
            <template #append>
              <ElButton title="删除末尾变量" @click="removeLastVariable"><icon-mdi-backspace-outline /></ElButton>
            </template>
          </ElInput>
          <div class="dict-tags">
            <span class="sub-label">可用词典</span>
            <button
              v-for="group in dictionaries"
              :key="group.id"
              type="button"
              class="dict-tag"
              :class="{ used: variables.includes(group.name), disabled: !group.enabled || !group.words.length }"
              :disabled="!group.enabled || !group.words.length"
              :title="group.enabled ? `${group.description || group.name}，点击插入 {${group.name}}` : '该词典已停用'"
              @mousedown.prevent
              @click="insertVariable(group.name)"
            >
              <icon-mdi-plus class="plus" />
              {{ group.name }}
              <em>{{ group.words.length }}</em>
            </button>
          </div>
          <div class="rule-status">
            <template v-if="missingVariables.length">
              <ElTag type="danger" effect="plain" size="small">无效变量</ElTag>
              <span class="danger">{{ missingText }} 不存在或已停用</span>
            </template>
            <template v-else-if="example">
              <ElTag effect="plain" size="small">示例</ElTag>
              <span class="example">{{ example }}</span>
            </template>
            <span v-else class="muted">输入规则或点击词典标签开始组合</span>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-label">域名后缀</div>
        <div class="section-body">
          <div class="suffix-actions">
            <span class="muted">已选 {{ suffixes.length }} / {{ allSuffixes.length }}</span>
            <ElButton link type="primary" @click="selectCommonSuffixes">常用</ElButton>
            <ElButton link type="primary" @click="suffixes = [...allSuffixes]">全选</ElButton>
            <ElButton link @click="suffixes = []">清空</ElButton>
          </div>
          <div class="suffix-grid">
            <button
              v-for="suffix in allSuffixes"
              :key="suffix"
              type="button"
              class="suffix-chip"
              :class="{ checked: suffixes.includes(suffix) }"
              @click="toggleSuffix(suffix)"
            >
              {{ suffix }}
            </button>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-label">排除字符</div>
        <div class="section-body">
          <ElInput v-model="excluded" placeholder="输入不允许出现的字符，例如 04" clearable class="exclude-input">
            <template #prefix><icon-mdi-filter-remove-outline class="text-icon" /></template>
          </ElInput>
        </div>
      </div>

      <div class="section actions">
        <div class="section-label" />
        <div class="section-body action-row">
          <ElButton type="primary" size="large" :loading="generating" @click="generateDomains">
            <icon-mdi-lightning-bolt v-if="!generating" class="mr-4px" />
            生成域名
          </ElButton>
          <span class="muted">单次最多生成 {{ MAX_ROWS.toLocaleString() }} 个</span>
        </div>
      </div>
    </ElCard>

    <ElCard shadow="never" class="card-wrapper">
      <template #header>
        <div class="toolbar">
          <div class="toolbar-title">
            生成结果
            <ElTag type="success" round>
              {{ filteredRows.length.toLocaleString() }}
              <template v-if="filteredRows.length !== rows.length">/ {{ rows.length.toLocaleString() }}</template>
            </ElTag>
          </div>
          <div class="toolbar-actions">
            <ElInput v-model="keyword" placeholder="筛选域名" clearable class="search">
              <template #prefix><icon-mdi-magnify /></template>
            </ElInput>
            <ElSelect v-model="selectedSuffix" class="suffix-filter">
              <ElOption label="全部后缀" value="all" />
              <ElOption v-for="suffix in resultSuffixes" :key="suffix" :label="suffix" :value="suffix" />
            </ElSelect>
            <ElButton :disabled="!filteredRows.length" @click="exportTxt">
              <icon-mdi-download class="mr-4px" />
              导出 TXT
            </ElButton>
            <ElButton type="success" :loading="adding" :disabled="!filteredRows.length" @click="addToExpired">
              <icon-mdi-playlist-plus v-if="!adding" class="mr-4px" />
              加入过期域名列表
            </ElButton>
            <ElButton :disabled="!rows.length" @click="clearRows">清空</ElButton>
          </div>
        </div>
      </template>
      <ElTable :data="pagedRows" height="440" stripe>
        <template #empty><ElEmpty :image-size="80" description="设置规则后点击「生成域名」" /></template>
        <ElTableColumn label="#" width="80" align="center">
          <template #default="{ $index }">{{ (page - 1) * pageSize + $index + 1 }}</template>
        </ElTableColumn>
        <ElTableColumn prop="domain" label="域名" min-width="260">
          <template #default="{ row }">
            <span class="domain">{{ row.domain }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="suffix" label="后缀" width="140" align="center">
          <template #default="{ row }">
            <ElTag size="small" effect="plain">{{ row.suffix }}</ElTag>
          </template>
        </ElTableColumn>
      </ElTable>
      <div class="footer">
        <span class="muted">导出与加入列表均作用于当前筛选结果</span>
        <ElPagination
          v-if="filteredRows.length > pageSize"
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="filteredRows.length"
          :page-sizes="[50, 100, 200, 500]"
          layout="total, sizes, prev, pager, next"
          background
        />
      </div>
    </ElCard>
  </div>
</template>

<style scoped>
.generator-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hero {
  background: linear-gradient(135deg, var(--el-color-primary-light-9), var(--el-bg-color) 70%);
}
.hero-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
}
.hero-title {
  display: flex;
  align-items: center;
  gap: 14px;
}
.hero-icon {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  font-size: 24px;
  color: #fff;
  background: var(--el-color-primary);
}
.hero h2 {
  margin: 0 0 4px;
  font-size: 18px;
}
.hero p {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.hero code {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--el-fill-color);
  color: var(--el-color-primary);
  font-size: 12px;
}
.stats {
  display: flex;
  gap: 10px;
}
.stat {
  min-width: 110px;
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
}
.stat span {
  display: block;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.stat strong {
  font-size: 20px;
  font-variant-numeric: tabular-nums;
}
.section {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 12px;
  padding: 14px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}
.section:first-child {
  padding-top: 0;
}
.section.actions {
  border-bottom: none;
  padding-bottom: 0;
}
.section-label {
  padding-top: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-regular);
}
.section-body {
  min-width: 0;
}
.sub-label {
  margin-right: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.dict-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}
.dict-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 6px 0 10px;
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 14px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.dict-tag em {
  font-style: normal;
  font-size: 11px;
  padding: 0 6px;
  border-radius: 8px;
  background: var(--el-bg-color);
  color: var(--el-text-color-secondary);
}
.dict-tag .plus {
  font-size: 14px;
}
.dict-tag:hover {
  background: var(--el-color-primary);
  border-color: var(--el-color-primary);
  color: #fff;
}
.dict-tag:hover em {
  background: rgb(255 255 255 / 25%);
  color: #fff;
}
.dict-tag.used {
  border-color: var(--el-color-primary);
  font-weight: 500;
}
.dict-tag.disabled {
  cursor: not-allowed;
  opacity: 0.45;
  background: var(--el-fill-color-light);
  border-color: var(--el-border-color);
  color: var(--el-text-color-secondary);
}
.rule-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  font-size: 13px;
}
.example {
  font-family: ui-monospace, Consolas, monospace;
  color: var(--el-text-color-primary);
}
.danger {
  color: var(--el-color-danger);
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.suffix-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 6px;
  margin-bottom: 10px;
}
.suffix-actions .el-button + .el-button {
  margin-left: 0;
}
.suffix-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 8px;
}
.suffix-chip {
  height: 30px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
  color: var(--el-text-color-regular);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.suffix-chip:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
.suffix-chip.checked {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 500;
}
.exclude-input {
  max-width: 420px;
}
.action-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.toolbar-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 500;
}
.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.toolbar-actions .el-button + .el-button {
  margin-left: 0;
}
.search {
  width: 200px;
}
.suffix-filter {
  width: 130px;
}
.domain {
  font-family: ui-monospace, Consolas, monospace;
}
.footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 12px;
}
@media (max-width: 768px) {
  .section {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .section-label {
    padding-top: 0;
  }
  .stats {
    width: 100%;
  }
  .stat {
    flex: 1;
    min-width: 0;
  }
}
</style>

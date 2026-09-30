<script setup lang="ts">
import { computed, ref } from 'vue';
import { type DomainDictionaryGroup, loadDomainDictionaries, saveDomainDictionaries } from '@/utils/domain-dictionary';
import { defaultDomainSuffixes, loadDomainSuffixes, saveDomainSuffixes } from '@/utils/domain-suffix';

const groups = ref(loadDomainDictionaries());
const selectedId = ref(groups.value[0]?.id || '');
const suffixes = ref(loadDomainSuffixes());
const selected = computed(() => groups.value.find(item => item.id === selectedId.value));
const totalWords = computed(() => groups.value.reduce((total, group) => total + group.words.length, 0));
const draftWords = ref('');
const groupKeyword = ref('');
const suffixKeyword = ref('');
const newSuffix = ref('');

const selectedToken = computed(() => `{${selected.value?.name || ''}}`);
const filteredGroups = computed(() =>
  groups.value.filter(item => !groupKeyword.value || item.name.includes(groupKeyword.value.trim()))
);
const filteredSuffixes = computed(() =>
  suffixes.value.filter(item => !suffixKeyword.value || item.includes(suffixKeyword.value.trim().toLowerCase()))
);
const draftList = computed(() => parseWords(draftWords.value));
const dirty = computed(() => {
  const group = selected.value;
  return group !== undefined && draftList.value.join('\n') !== group.words.join('\n');
});

function parseWords(text: string) {
  return [
    ...new Set(
      text
        .split(/\r?\n|[,，]/)
        .map(item => item.trim().toLowerCase())
        .filter(Boolean)
    )
  ];
}
function selectGroup(group: DomainDictionaryGroup) {
  selectedId.value = group.id;
  draftWords.value = group.words.join('\n');
}
function saveGroup() {
  const group = selected.value;
  if (!group) return;
  const name = group.name.trim();
  let warning = '';
  if (!name) warning = '词典名称不能为空';
  else if (/[{}]/.test(name)) warning = '词典名称不能包含 { }';
  else if (groups.value.some(item => item.id !== group.id && item.name === name)) warning = '词典名称已存在';
  if (warning) {
    window.$message?.warning(warning);
    return;
  }
  selected.value.name = name;
  selected.value.words = draftList.value;
  draftWords.value = selected.value.words.join('\n');
  saveDomainDictionaries(groups.value);
  window.$message?.success('词典已保存');
}
function dedupeDraft() {
  draftWords.value = draftList.value.join('\n');
}
function sortDraft() {
  draftWords.value = [...draftList.value].sort().join('\n');
}
function createGroup() {
  const names = new Set(groups.value.map(item => item.name));
  let index = 1;
  while (names.has(index === 1 ? '新词典' : `新词典${index}`)) index += 1;
  const name = index === 1 ? '新词典' : `新词典${index}`;
  const group = { id: `custom-${Date.now()}`, name, description: '自定义词典', enabled: true, words: [] };
  groups.value.push(group);
  selectGroup(group);
  saveDomainDictionaries(groups.value);
}
function removeGroup(group: DomainDictionaryGroup) {
  if (groups.value.length <= 1) {
    window.$message?.warning('至少保留一个词典');
    return;
  }
  groups.value = groups.value.filter(item => item.id !== group.id);
  if (selectedId.value === group.id) selectGroup(groups.value[0]);
  saveDomainDictionaries(groups.value);
}
function addSuffix() {
  const values = newSuffix.value
    .split(/[\s,，]+/)
    .map(item => item.trim().toLowerCase())
    .filter(Boolean)
    .map(item => (item.startsWith('.') ? item : `.${item}`));
  if (!values.length) return;
  const invalid = values.filter(item => !/^(\.[^\s.]+)+$/u.test(item));
  if (invalid.length) {
    window.$message?.warning(`后缀格式不正确：${invalid.join('、')}`);
    return;
  }
  const added = values.filter(item => !suffixes.value.includes(item));
  suffixes.value = [...suffixes.value, ...added];
  saveDomainSuffixes(suffixes.value);
  newSuffix.value = '';
  window.$message?.success(added.length ? `已添加 ${added.length} 个后缀` : '后缀已存在');
}
function removeSuffix(suffix: string) {
  suffixes.value = suffixes.value.filter(item => item !== suffix);
  saveDomainSuffixes(suffixes.value);
}
function resetSuffixes() {
  suffixes.value = [...defaultDomainSuffixes];
  saveDomainSuffixes(suffixes.value);
  window.$message?.success('已恢复默认后缀');
}
if (groups.value[0]) selectGroup(groups.value[0]);
</script>

<template>
  <div class="dictionary-page">
    <ElCard shadow="never" class="hero card-wrapper">
      <div class="hero-body">
        <div class="hero-title">
          <div class="hero-icon"><icon-mdi-book-open-variant /></div>
          <div>
            <h2>字典管理</h2>
            <p>
              维护域名生成规则可用的词典与后缀，词典名称即规则中的
              <code>{变量名}</code>
            </p>
          </div>
        </div>
        <div class="hero-side">
          <div class="stats">
            <div class="stat">
              <span>词典</span>
              <strong>{{ groups.length }}</strong>
            </div>
            <div class="stat">
              <span>词条</span>
              <strong>{{ totalWords.toLocaleString() }}</strong>
            </div>
            <div class="stat">
              <span>后缀</span>
              <strong>{{ suffixes.length }}</strong>
            </div>
          </div>
          <ElButton type="primary" size="large" @click="createGroup">
            <icon-mdi-plus class="mr-4px" />
            新增词典
          </ElButton>
        </div>
      </div>
    </ElCard>

    <div class="content">
      <ElCard shadow="never" class="panel dict-panel card-wrapper" body-class="dict-panel-body">
        <aside class="group-list">
          <div class="panel-title">
            词典分类
            <span class="count">{{ groups.length }}</span>
          </div>
          <ElInput v-model="groupKeyword" placeholder="搜索词典" clearable size="small" class="mb-8px">
            <template #prefix><icon-mdi-magnify /></template>
          </ElInput>
          <ElScrollbar class="group-scroll">
            <div
              v-for="group in filteredGroups"
              :key="group.id"
              class="group-item"
              :class="{ active: group.id === selectedId, off: !group.enabled }"
              @click="selectGroup(group)"
            >
              <div class="group-info">
                <span class="group-name">{{ group.name }}</span>
                <span class="group-count">{{ group.words.length.toLocaleString() }} 个词条</span>
              </div>
              <ElSwitch v-model="group.enabled" size="small" @click.stop @change="saveDomainDictionaries(groups)" />
              <ElPopconfirm :title="`确定删除词典「${group.name}」？`" width="220" @confirm="removeGroup(group)">
                <template #reference>
                  <button type="button" class="icon-btn danger" title="删除" @click.stop>
                    <icon-mdi-delete-outline />
                  </button>
                </template>
              </ElPopconfirm>
            </div>
            <ElEmpty v-if="!filteredGroups.length" :image-size="60" description="没有匹配的词典" />
          </ElScrollbar>
        </aside>

        <section class="editor">
          <template v-if="selected">
            <div class="editor-head">
              <div class="panel-title">
                词条编辑
                <ElTag v-if="dirty" type="warning" size="small" effect="plain">未保存</ElTag>
              </div>
              <ElButton type="primary" @click="saveGroup">
                <icon-mdi-content-save-outline class="mr-4px" />
                保存
              </ElButton>
            </div>
            <ElInput v-model="selected.name" placeholder="词典名称" class="mb-8px">
              <template #prepend>名称</template>
            </ElInput>
            <ElInput v-model="selected.description" placeholder="描述（可选）" class="mb-8px">
              <template #prepend>描述</template>
            </ElInput>
            <ElInput
              v-model="draftWords"
              type="textarea"
              resize="none"
              class="words-input"
              placeholder="每行一个词条，也支持逗号分隔"
            />
            <div class="editor-foot">
              <span class="muted">
                共 {{ draftList.length.toLocaleString() }} 个词条，规则中使用
                <code>{{ selectedToken }}</code>
              </span>
              <div>
                <ElButton link type="primary" @click="dedupeDraft">去重</ElButton>
                <ElButton link type="primary" @click="sortDraft">排序</ElButton>
                <ElButton link :disabled="!dirty" @click="selectGroup(selected)">还原</ElButton>
              </div>
            </div>
          </template>
          <ElEmpty v-else description="请选择或新增词典" />
        </section>
      </ElCard>

      <ElCard shadow="never" class="panel suffix-panel card-wrapper">
        <template #header>
          <div class="editor-head">
            <div class="panel-title">
              域名后缀
              <span class="count">{{ suffixes.length }}</span>
            </div>
            <ElPopconfirm title="恢复为默认后缀列表？" width="200" @confirm="resetSuffixes">
              <template #reference>
                <ElButton link>
                  <icon-mdi-restore class="mr-4px" />
                  恢复默认
                </ElButton>
              </template>
            </ElPopconfirm>
          </div>
        </template>
        <div class="suffix-toolbar">
          <ElInput
            v-model="newSuffix"
            placeholder="输入后缀，如 .com 或 xyz，空格/逗号分隔可批量添加"
            clearable
            @keyup.enter="addSuffix"
          >
            <template #append>
              <ElButton @click="addSuffix">
                <icon-mdi-plus class="mr-4px" />
                添加
              </ElButton>
            </template>
          </ElInput>
          <ElInput v-model="suffixKeyword" placeholder="搜索" clearable class="suffix-search">
            <template #prefix><icon-mdi-magnify /></template>
          </ElInput>
        </div>
        <ElScrollbar class="suffix-scroll">
          <div class="suffix-grid">
            <div v-for="suffix in filteredSuffixes" :key="suffix" class="suffix-chip">
              <span>{{ suffix }}</span>
              <button type="button" class="icon-btn" title="移除" @click="removeSuffix(suffix)">
                <icon-mdi-close />
              </button>
            </div>
          </div>
          <ElEmpty v-if="!filteredSuffixes.length" :image-size="60" description="暂无后缀" />
        </ElScrollbar>
      </ElCard>
    </div>
  </div>
</template>

<style scoped>
.dictionary-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hero {
  background: linear-gradient(135deg, var(--el-color-success-light-9), var(--el-bg-color) 70%);
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
  background: var(--el-color-success);
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
code {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--el-fill-color);
  color: var(--el-color-primary);
  font-size: 12px;
}
.hero-side {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.stats {
  display: flex;
  gap: 10px;
}
.stat {
  min-width: 96px;
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
.content {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  height: calc(100vh - 290px);
  min-height: 560px;
}
.panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.panel :deep(.el-card__body) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.dict-panel :deep(.dict-panel-body) {
  flex-direction: row;
  padding: 0;
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 500;
}
.count {
  padding: 0 8px;
  border-radius: 10px;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
  line-height: 20px;
}
.group-list {
  display: flex;
  flex-direction: column;
  width: 220px;
  flex-shrink: 0;
  padding: 16px 12px;
  border-right: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-extra-light);
}
.group-list .panel-title {
  margin-bottom: 12px;
}
.group-scroll {
  flex: 1;
  min-height: 0;
}
.group-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 8px 8px 10px;
  margin-bottom: 4px;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
}
.group-item:hover {
  background: var(--el-bg-color);
  border-color: var(--el-border-color-lighter);
}
.group-item.active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
}
.group-item.active .group-name {
  color: var(--el-color-primary);
  font-weight: 500;
}
.group-item.off .group-name {
  color: var(--el-text-color-placeholder);
  text-decoration: line-through;
}
.group-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.group-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}
.group-count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.icon-btn {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--el-text-color-secondary);
  font-size: 15px;
  cursor: pointer;
}
.icon-btn:hover {
  background: var(--el-fill-color);
  color: var(--el-text-color-primary);
}
.icon-btn.danger:hover {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}
.editor {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 16px;
}
.editor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.suffix-panel .editor-head {
  margin-bottom: 0;
}
.words-input {
  flex: 1;
  min-height: 0;
}
.words-input :deep(.el-textarea__inner) {
  height: 100%;
  font-family: ui-monospace, Consolas, monospace;
  line-height: 1.7;
}
.editor-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.suffix-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.suffix-search {
  width: 160px;
  flex-shrink: 0;
}
.suffix-scroll {
  flex: 1;
  min-height: 0;
}
.suffix-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}
.suffix-chip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  height: 32px;
  padding: 0 4px 0 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-bg-color);
  font-size: 13px;
  transition: all 0.15s;
}
.suffix-chip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.suffix-chip:hover {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
}
.suffix-chip .icon-btn {
  opacity: 0;
}
.suffix-chip:hover .icon-btn {
  opacity: 1;
}
.suffix-chip .icon-btn:hover {
  color: var(--el-color-danger);
}
@media (max-width: 1200px) {
  .content {
    grid-template-columns: 1fr;
    height: auto;
  }
  .dict-panel {
    height: 600px;
  }
  .suffix-panel {
    height: 480px;
  }
}
@media (max-width: 640px) {
  .dict-panel :deep(.dict-panel-body) {
    flex-direction: column;
  }
  .dict-panel {
    height: auto;
  }
  .group-list {
    width: auto;
    max-height: 260px;
    border-right: none;
    border-bottom: 1px solid var(--el-border-color-lighter);
  }
  .words-input :deep(.el-textarea__inner) {
    min-height: 300px;
  }
}
</style>

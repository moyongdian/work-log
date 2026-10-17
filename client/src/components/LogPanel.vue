<script setup>
import { computed, ref, watch, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";
import MarkdownEditor from "./MarkdownEditor.vue";
import { fmtShort, splitDatePrefix, plainPreview } from "../utils.js";

const props = defineProps({
  tree: { type: Object, default: null },
  logs: { type: Array, default: () => [] },
  log: { type: Object, default: null },
  autoSave: { type: Boolean, default: true },
  saving: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
});

const emit = defineEmits(["save", "new-log", "select-log", "delete-log", "dirty"]);

const title = ref("");
const content = ref("");
const keyword = ref("");
const searchInput = ref(null);
const lastSavedAt = ref(0);
let timer = null;

/* 切换日志时同步编辑区内容 */
watch(
  () => props.log && props.log.id,
  () => {
    title.value = props.log ? props.log.title : "";
    content.value = props.log ? props.log.content || "" : "";
    lastSavedAt.value = props.log ? props.log.updateTime : 0;
    clearTimeout(timer);
    emit("dirty", false);
  },
  { immediate: true }
);

/* 保存成功后刷新「已保存」时间 */
watch(
  () => (props.log ? props.log.updateTime : 0),
  (ts) => {
    if (ts && !dirty.value) lastSavedAt.value = ts;
  }
);

const dirty = computed(() => {
  if (!props.log) return !!(title.value.trim() || content.value.trim());
  return title.value !== (props.log.title || "") || content.value !== (props.log.content || "");
});

watch(dirty, (v) => emit("dirty", v));

function scheduleAutoSave() {
  if (!props.autoSave) return;
  clearTimeout(timer);
  timer = setTimeout(() => {
    if (dirty.value) doSave();
  }, 1800);
}

function onEdit() {
  scheduleAutoSave();
}

function doSave() {
  if (props.saving) return;
  const t = title.value.trim();
  if (!t && !content.value.trim()) return;
  emit("save", {
    id: props.log ? props.log.id : null,
    title: t || "未命名日志",
    content: content.value,
  });
}

function onSaved(ts) {
  lastSavedAt.value = ts;
}

defineExpose({
  doSave,
  focusSearch: () => {
    const el = searchInput.value;
    if (el) {
      el.focus();
      el.select();
    }
  },
  getDraft: () => ({
    id: props.log ? props.log.id : null,
    title: title.value.trim(),
    content: content.value,
  }),
  isDirty: () => dirty.value,
});

const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase();
  if (!k) return props.logs;
  return props.logs.filter(
    (l) =>
      (l.title || "").toLowerCase().includes(k) ||
      (l.preview || "").toLowerCase().includes(k)
  );
});

/** 列表摘要去掉 Markdown 标记，保持一行纯文本 */
const previewOf = (l) => plainPreview(l.preview, 70);

const treeTitle = computed(() => (props.tree ? splitDatePrefix(props.tree.name) : { date: "", rest: "" }));

function confirmDelete(log) {
  emit("delete-log", log);
}

onBeforeUnmount(() => clearTimeout(timer));
</script>

<template>
  <section class="main">
    <!-- 顶部：当前分支信息 -->
    <div class="main-head">
      <Icon name="folder-open" :size="15" style="color: var(--accent)" />
      <div class="main-title">
        <template v-if="tree">
          <span
            v-if="treeTitle.date"
            style="color: var(--text-3); font-family: var(--mono); font-size: 12px; margin-right: 6px"
          >
            {{ treeTitle.date }}
          </span>
          <span v-if="treeTitle.rest">{{ treeTitle.rest }}</span>
          <span v-else style="color: var(--text-3)">（未命名分支）</span>
        </template>
        <template v-else>未选择分支</template>
      </div>
      <span v-if="tree" class="main-sub">{{ logs.length }} 篇日志</span>
      <span class="grow"></span>

      <div v-if="tree" style="position: relative; display: flex; align-items: center">
        <Icon
          name="search"
          :size="13"
          style="position: absolute; left: 8px; color: var(--text-3); pointer-events: none"
        />
        <input
          ref="searchInput"
          v-model="keyword"
          class="input"
          style="height: 27px; width: 168px; padding-left: 26px"
          placeholder="搜索日志标题/内容"
        />
      </div>

      <button v-if="tree" class="btn btn-primary" @click="emit('new-log')">
        <Icon name="file-plus" :size="13" />
        新建日志
      </button>
    </div>

    <!-- 未选中分支 -->
    <div v-if="!tree" class="empty-state">
      <div class="empty-inner">
        <div class="empty-icon"><Icon name="book" :size="26" /></div>
        <h3>选择左侧的一个分支</h3>
        <p>
          左侧是按业务 / 项目划分的目录分支，支持无限级子分支。<br />
          新建分支会自动带上 <code>YYYY-MM-DD</code> 日期前缀，可直接修改。
        </p>
      </div>
    </div>

    <div v-else class="split">
      <!-- 日志列表 -->
      <div class="log-list">
        <div v-if="loading" class="list-empty">加载中…</div>
        <div v-else-if="!filtered.length" class="list-empty">
          {{ keyword ? "没有匹配的日志" : "该分支下还没有日志，点击右上角「新建日志」开始记录" }}
        </div>
        <div
          v-for="l in filtered"
          :key="l.id"
          class="log-item"
          :class="{ active: log && log.id === l.id }"
          @click="emit('select-log', l)"
        >
          <Icon name="file" :size="13" style="color: var(--text-3); flex: none" />
          <span class="log-item-title">{{ l.title }}</span>
          <span class="log-item-preview">{{ previewOf(l) }}</span>
          <span v-if="l.syncStatus === 'unsynced'" class="tag tag-warn">待同步</span>
          <span class="log-item-time">{{ fmtShort(l.updateTime) }}</span>
          <button
            class="btn btn-ghost btn-sm btn-icon"
            title="删除日志"
            @click.stop="confirmDelete(l)"
          >
            <Icon name="trash" :size="12" />
          </button>
        </div>
      </div>

      <!-- 编辑器 -->
      <div class="editor">
        <div class="editor-head">
          <input v-model="title" class="title-input" placeholder="日志标题" @input="onEdit" />
          <span v-if="dirty" class="dirty-dot" title="有未保存的修改"></span>
          <span v-if="lastSavedAt && !dirty" class="tag tag-ok">已保存 {{ fmtShort(lastSavedAt) }}</span>
          <button class="btn btn-primary" :disabled="saving || !dirty" @click="doSave">
            <Icon name="save" :size="13" />
            {{ saving ? "保存中…" : "保存" }}
          </button>
        </div>

        <div style="flex: 1; min-height: 0; display: flex; flex-direction: column">
          <MarkdownEditor
            v-model="content"
            :placeholder="`在「${treeTitle.date} ${treeTitle.rest}」下记录工作内容，支持 Markdown 语法…`"
            @update:model-value="onEdit"
            @save="doSave"
          />
        </div>
      </div>
    </div>
  </section>
</template>

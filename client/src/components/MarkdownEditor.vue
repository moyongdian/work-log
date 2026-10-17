<script setup>
import { computed, nextTick, ref } from "vue";
import { marked } from "marked";
import Icon from "./Icon.vue";

const props = defineProps({
  modelValue: { type: String, default: "" },
  placeholder: { type: String, default: "在这里用 Markdown 记录今天的工作…" },
});

const emit = defineEmits(["update:modelValue", "save"]);

marked.setOptions({ gfm: true, breaks: true });

const mode = ref("edit"); // edit | split | preview
const textarea = ref(null);

const value = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});

/** 简易 HTML 清洗，避免预览区执行脚本（单用户本地场景，做基础防护即可） */
function sanitize(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/\son\w+\s*=\s*"[^"]*"/gi, "")
    .replace(/\son\w+\s*=\s*'[^']*'/gi, "")
    .replace(/javascript:/gi, "");
}

const html = computed(() => sanitize(marked.parse(props.modelValue || "")));

const stats = computed(() => {
  const text = props.modelValue || "";
  const chars = text.replace(/\s/g, "").length;
  const lines = text ? text.split("\n").length : 0;
  return { chars, lines };
});

function focusTextarea() {
  nextTick(() => textarea.value && textarea.value.focus());
}

function replaceRange(start, end, text, cursorOffset) {
  const el = textarea.value;
  if (!el) return;
  const old = props.modelValue || "";
  const next = old.slice(0, start) + text + old.slice(end);
  emit("update:modelValue", next);
  nextTick(() => {
    el.focus();
    const pos = start + (cursorOffset == null ? text.length : cursorOffset);
    el.setSelectionRange(pos, pos);
  });
}

function wrap(before, after = before) {
  const el = textarea.value;
  if (!el) return;
  const start = el.selectionStart;
  const end = el.selectionEnd;
  const selected = (props.modelValue || "").slice(start, end) || "文本";
  replaceRange(start, end, before + selected + after, before.length + selected.length);
}

function linePrefix(prefix) {
  const el = textarea.value;
  if (!el) return;
  const old = props.modelValue || "";
  const start = el.selectionStart;
  const lineStart = old.lastIndexOf("\n", start - 1) + 1;
  replaceRange(lineStart, lineStart, prefix, prefix.length);
}

function insertBlock(block) {
  const el = textarea.value;
  if (!el) return;
  const old = props.modelValue || "";
  const start = el.selectionStart;
  const needNl = start > 0 && old[start - 1] !== "\n";
  const text = (needNl ? "\n" : "") + block;
  replaceRange(start, el.selectionEnd, text, text.length);
}

function onKeydown(e) {
  const meta = e.ctrlKey || e.metaKey;
  if (meta && e.key.toLowerCase() === "s") {
    e.preventDefault();
    emit("save");
    return;
  }
  if (meta && e.key.toLowerCase() === "b") {
    e.preventDefault();
    wrap("**");
    return;
  }
  if (meta && e.key.toLowerCase() === "i") {
    e.preventDefault();
    wrap("*");
    return;
  }
  if (e.key === "Tab") {
    e.preventDefault();
    insertBlock("  ");
  }
}

const tools = [
  { icon: "heading", title: "标题", run: () => linePrefix("## ") },
  { icon: "bold", title: "加粗 (Ctrl+B)", run: () => wrap("**") },
  { icon: "italic", title: "斜体 (Ctrl+I)", run: () => wrap("*") },
  { icon: "strike", title: "删除线", run: () => wrap("~~") },
  { sep: true },
  { icon: "list", title: "无序列表", run: () => linePrefix("- ") },
  { icon: "olist", title: "有序列表", run: () => linePrefix("1. ") },
  { icon: "quote", title: "引用", run: () => linePrefix("> ") },
  { icon: "code", title: "行内代码", run: () => wrap("`") },
  { sep: true },
  { icon: "link", title: "链接", run: () => wrap("[", "](https://)") },
  { icon: "table", title: "表格", run: () => insertBlock("| 列 1 | 列 2 |\n| --- | --- |\n| 内容 | 内容 |\n") },
  { icon: "file", title: "代码块", run: () => insertBlock("```bash\n\n```\n") },
];
</script>

<template>
  <div class="md-editor" style="display: flex; flex-direction: column; height: 100%">
    <div class="md-toolbar">
      <template v-for="(t, i) in tools" :key="i">
        <span v-if="t.sep" class="md-sep"></span>
        <button v-else class="md-btn" :title="t.title" type="button" @click="t.run">
          <Icon :name="t.icon" :size="14" />
        </button>
      </template>
      <span class="grow"></span>
      <button class="md-btn" :class="{ on: mode === 'edit' }" title="仅编辑" @click="mode = 'edit'">
        <Icon name="pen" :size="14" />
      </button>
      <button class="md-btn" :class="{ on: mode === 'split' }" title="分屏预览" @click="mode = 'split'">
        <Icon name="columns" :size="14" />
      </button>
      <button class="md-btn" :class="{ on: mode === 'preview' }" title="仅预览" @click="mode = 'preview'">
        <Icon name="eye" :size="14" />
      </button>
    </div>

    <div class="md-body">
      <div v-show="mode !== 'preview'" class="md-pane">
        <textarea
          ref="textarea"
          v-model="value"
          class="md-textarea"
          :placeholder="placeholder"
          spellcheck="false"
          @keydown="onKeydown"
          @click="focusTextarea"
        ></textarea>
      </div>
      <div v-show="mode !== 'edit'" class="md-pane md-preview" v-html="html"></div>
    </div>

    <div class="editor-foot">
      <span>{{ stats.lines }} 行</span>
      <span>{{ stats.chars }} 字</span>
      <span class="grow"></span>
      <span>Markdown · Ctrl+S 保存</span>
    </div>
  </div>
</template>

<style scoped>
.md-btn.on {
  background: #e2ebfd;
  color: var(--accent-hover);
}
</style>

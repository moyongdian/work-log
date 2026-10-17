<script setup>
import { reactive } from "vue";
import Modal from "./Modal.vue";
import { fmtFull } from "../utils.js";

const props = defineProps({
  conflicts: { type: Array, default: () => [] },
});

const emit = defineEmits(["close", "submit"]);

// 默认全部保留本地版本
const choice = reactive({});
props.conflicts.forEach((c) => {
  choice[c.id] = "local";
});

function setAll(v) {
  props.conflicts.forEach((c) => (choice[c.id] = v));
}

function submit() {
  emit(
    "submit",
    props.conflicts.map((c) => ({
      dataType: c.dataType,
      id: c.id,
      keepLocal: choice[c.id] !== "cloud",
    }))
  );
}
</script>

<template>
  <Modal title="发现数据冲突" wide :close-on-overlay="false" @close="emit('close')">
    <div class="notice notice-warn" style="margin-bottom: 12px">
      以下 {{ conflicts.length }} 条数据在本地与云端都被修改过，请逐条选择保留哪个版本。
      <strong>选择后立即生效，另一版本将被覆盖。</strong>
    </div>

    <div v-for="c in conflicts" :key="c.dataType + c.id" class="conflict-row">
      <div class="conflict-title">
        <span class="tag" :class="c.dataType === 'tree' ? 'tag-ok' : 'tag-warn'" style="margin-right: 6px">
          {{ c.dataType === "tree" ? "分支" : "日志" }}
        </span>
        {{ c.title }}
      </div>
      <div class="conflict-meta">
        本地修改：{{ fmtFull(c.localUpdateTime) }} ｜ 云端修改：{{ fmtFull(c.remoteUpdateTime) }}
      </div>
      <div class="conflict-actions">
        <button
          class="choice-btn"
          :class="{ 'sel-local': choice[c.id] === 'local' }"
          @click="choice[c.id] = 'local'"
        >
          保留本地版本
        </button>
        <button
          class="choice-btn"
          :class="{ 'sel-cloud': choice[c.id] === 'cloud' }"
          @click="choice[c.id] = 'cloud'"
        >
          采用云端版本
        </button>
      </div>
    </div>

    <template #foot>
      <button class="btn btn-sm" @click="setAll('local')">全部保留本地</button>
      <button class="btn btn-sm" @click="setAll('cloud')">全部采用云端</button>
      <span class="grow"></span>
      <button class="btn" @click="emit('close')">稍后处理</button>
      <button class="btn btn-primary" @click="submit">确认并继续同步</button>
    </template>
  </Modal>
</template>

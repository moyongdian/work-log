<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { formatCombo } from "../shortcuts.js";

const props = defineProps({
  menus: { type: Array, default: () => [] },
  shortcuts: { type: Object, default: () => ({}) },
  online: { type: Boolean, default: true },
  pending: { type: Number, default: 0 },
  syncing: { type: Boolean, default: false },
});

const emit = defineEmits(["action"]);

const openId = ref(null);

function onRootClick(menu) {
  if (!menu.items) {
    openId.value = null;
    emit("action", menu.id);
    return;
  }
  openId.value = openId.value === menu.id ? null : menu.id;
}

function onRootHover(menu) {
  if (openId.value && menu.items && openId.value !== menu.id) openId.value = menu.id;
}

function pick(item) {
  if (item.disabled) return;
  openId.value = null;
  emit("action", item.id);
}

function close() {
  openId.value = null;
}

function onKey(e) {
  if (e.key === "Escape") close();
}

onMounted(() => {
  window.addEventListener("click", close);
  window.addEventListener("keydown", onKey);
  window.addEventListener("blur", close);
});

onBeforeUnmount(() => {
  window.removeEventListener("click", close);
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("blur", close);
});
</script>

<template>
  <div class="menubar">
    <div class="menubar-brand">
      <div class="brand-logo">N</div>
      <span class="menubar-title">工作日志笔记本</span>
    </div>

    <div class="menubar-menus">
      <div
        v-for="menu in menus"
        :key="menu.id"
        class="menu-root"
        :class="{ open: openId === menu.id }"
        @click.stop="onRootClick(menu)"
        @mouseenter="onRootHover(menu)"
      >
        {{ menu.label }}
        <div v-if="openId === menu.id && menu.items" class="menu-pop" @click.stop>
          <template v-for="(item, i) in menu.items" :key="i">
            <div v-if="item.sep" class="menu-sep"></div>
            <div
              v-else
              class="menu-item"
              :class="{ disabled: item.disabled, checked: item.checked }"
              @click="pick(item)"
            >
              <span class="menu-check">{{ item.checked ? "✓" : "" }}</span>
              <span class="menu-label">{{ item.label }}</span>
              <span v-if="item.shortcut" class="menu-key">{{ formatCombo(shortcuts[item.shortcut]) }}</span>
            </div>
          </template>
        </div>
      </div>
    </div>

    <span class="grow"></span>

    <div class="menubar-right">
      <span class="net-dot" :class="online ? 'online' : 'offline'"></span>
      <span>{{ online ? "在线" : "离线" }}</span>
      <span v-if="pending > 0" class="tag tag-warn">待同步 {{ pending }}</span>
      <span v-if="syncing" class="tag tag-ok">同步中…</span>
    </div>
  </div>
</template>

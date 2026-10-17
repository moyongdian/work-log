<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import Icon from "./Icon.vue";
import TreeNode from "./TreeNode.vue";

const props = defineProps({
  trees: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
  pendingCount: { type: Number, default: 0 },
  online: { type: Boolean, default: true },
  serverReady: { type: Boolean, default: false },
  syncing: { type: Boolean, default: false },
  lastSyncText: { type: String, default: "" },
});

const emit = defineEmits(["select", "add", "rename", "remove", "reorder", "sync"]);

/* ---------------------------- 树结构组织 ---------------------------- */

const childrenMap = computed(() => {
  const map = new Map();
  const sorted = [...props.trees].sort((a, b) => a.sort - b.sort);
  for (const t of sorted) {
    const key = t.parentId || "__root__";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(t);
  }
  return map;
});

const roots = computed(() => childrenMap.value.get("__root__") || []);

/* ---------------------------- 展开状态 ---------------------------- */

const expanded = ref(new Set());

function loadExpanded() {
  try {
    const raw = localStorage.getItem("nb.expanded");
    if (raw) expanded.value = new Set(JSON.parse(raw));
  } catch {
    /* ignore */
  }
}

function persistExpanded() {
  try {
    localStorage.setItem("nb.expanded", JSON.stringify([...expanded.value]));
  } catch {
    /* ignore */
  }
}

function toggle(node) {
  const s = new Set(expanded.value);
  s.has(node.id) ? s.delete(node.id) : s.add(node.id);
  expanded.value = s;
  persistExpanded();
}

/** 展开指定节点的所有祖先，便于选中后可见 */
function expandTo(id) {
  const byId = new Map(props.trees.map((t) => [t.id, t]));
  const s = new Set(expanded.value);
  let cur = byId.get(id);
  while (cur && cur.parentId) {
    s.add(cur.parentId);
    cur = byId.get(cur.parentId);
  }
  expanded.value = s;
  persistExpanded();
}

/** 新节点默认展开其父级 */
function revealNew(id) {
  expandTo(id);
  const node = props.trees.find((t) => t.id === id);
  if (node) {
    const s = new Set(expanded.value);
    s.add(node.id);
    expanded.value = s;
    persistExpanded();
  }
}

watch(
  () => props.activeId,
  (id) => id && expandTo(id)
);

onMounted(loadExpanded);

defineExpose({ revealNew, expandTo });

/* ---------------------------- 拖拽排序 ---------------------------- */

const dragId = ref(null);
const dropTarget = ref(null);

function siblingsOf(parentId) {
  return childrenMap.value.get(parentId || "__root__") || [];
}

function isDescendant(candidateId, ancestorId) {
  const byId = new Map(props.trees.map((t) => [t.id, t]));
  let cur = byId.get(candidateId);
  while (cur && cur.parentId) {
    if (cur.parentId === ancestorId) return true;
    cur = byId.get(cur.parentId);
  }
  return false;
}

function onDragStart(node, e) {
  dragId.value = node.id;
  e.dataTransfer.effectAllowed = "move";
  try {
    e.dataTransfer.setData("text/plain", node.id);
  } catch {
    /* ignore */
  }
}

function onDragOver(node, e) {
  if (!dragId.value || dragId.value === node.id) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  const rect = e.currentTarget.getBoundingClientRect();
  const y = e.clientY - rect.top;
  let pos = y < rect.height * 0.35 ? "before" : y > rect.height * 0.65 ? "after" : "inside";
  // 不能拖入自己的子孙节点
  if (pos === "inside" && isDescendant(node.id, dragId.value)) pos = "after";
  dropTarget.value = { id: node.id, pos };
}

function onDropOnNode(node) {
  const target = dropTarget.value;
  const id = dragId.value;
  resetDrag();
  if (!id || !target || id === node.id) return;

  const dragNode = props.trees.find((t) => t.id === id);
  if (!dragNode) return;

  let parentId;
  let list;
  if (target.pos === "inside") {
    parentId = node.id;
    list = [...siblingsOf(node.id)];
  } else {
    parentId = node.parentId || null;
    list = [...siblingsOf(node.parentId)];
  }

  list = list.filter((t) => t.id !== id);
  let index;
  if (target.pos === "inside") {
    index = list.length;
  } else {
    const at = list.findIndex((t) => t.id === node.id);
    index = target.pos === "before" ? at : at + 1;
  }
  list.splice(Math.max(0, index), 0, dragNode);

  const updates = list.map((t, i) => ({ id: t.id, parentId, sort: (i + 1) * 10 }));
  emit("reorder", updates);
}

/** 拖到空白处：移动到根级末尾 */
function onDropToRoot(e) {
  const id = dragId.value;
  resetDrag();
  if (!id) return;
  const list = siblingsOf(null).filter((t) => t.id !== id);
  const dragNode = props.trees.find((t) => t.id === id);
  if (!dragNode) return;
  list.push(dragNode);
  emit("reorder", list.map((t, i) => ({ id: t.id, parentId: null, sort: (i + 1) * 10 })));
}

function resetDrag() {
  dragId.value = null;
  dropTarget.value = null;
}

/* ---------------------------- 右键菜单 ---------------------------- */

const menu = ref({ visible: false, x: 0, y: 0, node: null });

function openMenu(node, e) {
  const pad = 8;
  const w = 168;
  const h = 132;
  menu.value = {
    visible: true,
    x: Math.min(e.clientX, window.innerWidth - w - pad),
    y: Math.min(e.clientY, window.innerHeight - h - pad),
    node,
  };
}

function closeMenu() {
  menu.value.visible = false;
}

function menuAction(action) {
  const node = menu.value.node;
  closeMenu();
  if (!node) return;
  if (action === "add") emit("add", node.id);
  if (action === "rename") emit("rename", node);
  if (action === "remove") emit("remove", node);
}

onMounted(() => {
  window.addEventListener("click", closeMenu);
  window.addEventListener("blur", closeMenu);
});
onBeforeUnmount(() => {
  window.removeEventListener("click", closeMenu);
  window.removeEventListener("blur", closeMenu);
});
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-head">
      <div class="sidebar-actions">
        <button class="btn btn-primary grow" title="新建一级分支" @click="emit('add', null)">
          <Icon name="plus" :size="13" :stroke="2" />
          新建分支
        </button>
        <button
          class="btn"
          :disabled="!online || !serverReady || syncing"
          :title="
            !online
              ? '当前离线，同步不可用'
              : !serverReady
                ? '请先在菜单「设置」中配置云服务器地址与 Token'
                : '与云端同步'
          "
          @click="emit('sync')"
        >
          <Icon name="sync" :size="13" :class="{ spin: syncing }" />
          同步
        </button>
      </div>
    </div>

    <div
      class="tree-wrap"
      @dragover.prevent
      @drop.prevent="onDropToRoot"
      @click.self="emit('select', null)"
    >
      <div v-if="!roots.length" class="tree-empty">
        还没有任何分支<br />
        点击上方「新建分支」开始记录
      </div>

      <TreeNode
        v-for="node in roots"
        :key="node.id"
        :node="node"
        :depth="0"
        :children-map="childrenMap"
        :expanded="expanded"
        :active-id="activeId"
        :drag-id="dragId"
        :drop-target="dropTarget"
        @select="emit('select', $event)"
        @toggle="toggle"
        @context="openMenu"
        @dragstart="onDragStart"
        @dragover="onDragOver"
        @dragend="resetDrag"
        @drop="onDropOnNode"
      />
    </div>

    <div class="sidebar-foot">
      <span style="color: var(--text-3)">{{ lastSyncText || "尚未同步" }}</span>
      <span class="grow"></span>
      <span v-if="pendingCount > 0" class="tag tag-warn" title="待同步变更条数">待同步 {{ pendingCount }}</span>
      <span v-else class="tag tag-ok">已同步</span>
    </div>

    <teleport to="body">
      <div
        v-if="menu.visible"
        class="ctx-menu"
        :style="{ left: menu.x + 'px', top: menu.y + 'px' }"
        @click.stop
      >
        <div class="ctx-item" @click="menuAction('add')">
          <Icon name="folder-plus" :size="13" /> 新增子分支
        </div>
        <div class="ctx-item" @click="menuAction('rename')">
          <Icon name="edit" :size="13" /> 重命名
        </div>
        <div class="ctx-sep"></div>
        <div class="ctx-item danger" @click="menuAction('remove')">
          <Icon name="trash" :size="13" /> 删除分支
        </div>
      </div>
    </teleport>
  </aside>
</template>

<style scoped>
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>

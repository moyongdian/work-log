<script setup>
import { computed } from "vue";
import Icon from "./Icon.vue";

defineOptions({ name: "TreeNode" });

const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
  childrenMap: { type: Map, required: true },
  expanded: { type: Set, required: true },
  activeId: { type: String, default: null },
  dragId: { type: String, default: null },
  dropTarget: { type: Object, default: null },
});

const emit = defineEmits(["select", "toggle", "context", "dragstart", "dragover", "dragend", "drop"]);

const children = computed(() => props.childrenMap.get(props.node.id) || []);
const hasChildren = computed(() => children.value.length > 0);
const isOpen = computed(() => props.expanded.has(props.node.id));

// 目录名中「YYYY-MM-DD 」前缀用弱化样式展示，方便扫读
const dateMatch = computed(() => /^(\d{4}-\d{2}-\d{2}\s*)/.exec(props.node.name || ""));
const datePart = computed(() => (dateMatch.value ? dateMatch.value[1].trimEnd() : ""));
const nameRest = computed(() =>
  dateMatch.value ? (props.node.name || "").slice(dateMatch.value[1].length) : props.node.name || ""
);

const isDropBefore = computed(
  () => props.dropTarget && props.dropTarget.id === props.node.id && props.dropTarget.pos === "before"
);
const isDropAfter = computed(
  () => props.dropTarget && props.dropTarget.id === props.node.id && props.dropTarget.pos === "after"
);
const isDropInside = computed(
  () => props.dropTarget && props.dropTarget.id === props.node.id && props.dropTarget.pos === "inside"
);

function rowStyle() {
  return { paddingLeft: 4 + props.depth * 13 + "px" };
}
</script>

<template>
  <div>
    <div
      class="tree-row"
      :class="{
        active: node.id === activeId,
        dragging: node.id === dragId,
        'drop-inside': isDropInside,
      }"
      :style="rowStyle()"
      draggable="true"
      :title="node.name"
      @click="emit('select', node)"
      @contextmenu.prevent="emit('context', node, $event)"
      @dragstart="emit('dragstart', node, $event)"
      @dragover="emit('dragover', node, $event)"
      @drop.stop.prevent="emit('drop', node, $event)"
      @dragend="emit('dragend')"
    >
      <span
        class="caret"
        :class="{ open: isOpen, hidden: !hasChildren }"
        @click.stop="hasChildren && emit('toggle', node)"
      >
        <Icon name="chevron" :size="10" :stroke="2" />
      </span>
      <span class="tree-icon">
        <Icon :name="isOpen && hasChildren ? 'folder-open' : 'folder'" :size="13" />
      </span>
      <span class="tree-label">
        <span v-if="datePart" class="tree-date">{{ datePart }}</span
        >{{ nameRest }}
      </span>
      <span v-if="node.syncStatus === 'unsynced'" class="tree-badge" title="待同步到云端"></span>
      <span v-if="hasChildren" class="tree-count">{{ children.length }}</span>
      <div v-if="isDropBefore" class="drop-line" style="top: -1px"></div>
      <div v-if="isDropAfter" class="drop-line" style="bottom: -1px"></div>
    </div>

    <template v-if="isOpen && hasChildren">
      <TreeNode
        v-for="child in children"
        :key="child.id"
        :node="child"
        :depth="depth + 1"
        :children-map="childrenMap"
        :expanded="expanded"
        :active-id="activeId"
        :drag-id="dragId"
        :drop-target="dropTarget"
        @select="emit('select', $event)"
        @toggle="emit('toggle', $event)"
        @context="(n, e) => emit('context', n, e)"
        @dragstart="(n, e) => emit('dragstart', n, e)"
        @dragover="(n, e) => emit('dragover', n, e)"
        @dragend="emit('dragend')"
        @drop="emit('drop', $event)"
      />
    </template>
  </div>
</template>

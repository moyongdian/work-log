<script setup>
import { onMounted, onBeforeUnmount } from "vue";

const props = defineProps({
  title: { type: String, default: "" },
  wide: { type: Boolean, default: false },
  closeOnOverlay: { type: Boolean, default: true },
});

const emit = defineEmits(["close"]);

function onKey(e) {
  if (e.key === "Escape") emit("close");
}

onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <teleport to="body">
    <div class="overlay" @mousedown.self="closeOnOverlay && emit('close')">
      <div class="dialog" :class="{ wide }" role="dialog" aria-modal="true">
        <div class="dialog-head">{{ title }}</div>
        <div class="dialog-body">
          <slot />
        </div>
        <div class="dialog-foot">
          <slot name="foot" />
        </div>
      </div>
    </div>
  </teleport>
</template>

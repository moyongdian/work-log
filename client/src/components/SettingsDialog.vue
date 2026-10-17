<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import Modal from "./Modal.vue";
import Icon from "./Icon.vue";
import { saveConfig, testConnection, appInfo, backupDb, saveBackground, clearBackground, isDesktop } from "../api.js";
import { pickImage } from "../fileio.js";
import { applyTheme, normalizeTheme } from "../theme.js";
import { SHORTCUT_ACTIONS, defaultShortcuts, eventToCombo, findConflicts, formatCombo, normalizeShortcuts } from "../shortcuts.js";

const props = defineProps({
  config: { type: Object, required: true },
  online: { type: Boolean, default: true },
  initialTab: { type: String, default: "general" },
});

const emit = defineEmits(["close", "saved", "toast"]);

const tab = ref(["general", "shortcuts", "theme"].includes(props.initialTab) ? props.initialTab : "general");

/* ------------------------------ 通用 ------------------------------ */

const form = reactive({
  serverUrl: props.config.serverUrl || "",
  token: props.config.token || "",
  autoSave: props.config.autoSave !== false,
  autoDatePrefix: props.config.autoDatePrefix !== false,
});

const testing = ref(false);
const testResult = ref(null);
const info = ref({ dbPath: "", backupDir: "" });
const busy = ref(false);

appInfo()
  .then((v) => (info.value = v))
  .catch(() => {});

async function doTest() {
  testing.value = true;
  testResult.value = null;
  try {
    await saveConfig({ ...props.config, ...form });
    emit("saved", { ...props.config, ...form });
    const msg = await testConnection();
    testResult.value = { ok: true, msg };
  } catch (e) {
    testResult.value = { ok: false, msg: String(e && e.message ? e.message : e) };
  } finally {
    testing.value = false;
  }
}

async function doBackup() {
  try {
    const path = await backupDb();
    emit("toast", { type: "ok", text: "已备份到：" + path });
  } catch (e) {
    emit("toast", { type: "err", text: "备份失败：" + (e.message || e) });
  }
}

/* ------------------------------ 快捷键 ------------------------------ */

const shortcuts = reactive(normalizeShortcuts(props.config.shortcuts));
const recording = ref(null);
const conflicts = computed(() => findConflicts(shortcuts));

function startRecord(action) {
  recording.value = action;
}

function clearShortcut(action) {
  shortcuts[action] = "";
  recording.value = null;
}

function onKeydown(e) {
  if (!recording.value) return;
  e.preventDefault();
  e.stopPropagation();

  if (e.key === "Escape") {
    recording.value = null;
    return;
  }
  if (e.key === "Backspace" || e.key === "Delete") {
    shortcuts[recording.value] = "";
    recording.value = null;
    return;
  }
  const combo = eventToCombo(e);
  if (!combo) return;
  shortcuts[recording.value] = combo;
  recording.value = null;
}

function resetShortcuts() {
  Object.assign(shortcuts, defaultShortcuts());
}

/* ------------------------------ 主题 ------------------------------ */

const theme = reactive(normalizeTheme(props.config.theme));
const originalTheme = { ...normalizeTheme(props.config.theme) };
const pickingImage = ref(false);

const bgPercent = computed(() => Math.round(theme.bgOpacity * 100));

function preview() {
  applyTheme(theme);
}

function setMode(mode) {
  theme.mode = mode;
  preview();
}

function setBgOpacity(e) {
  theme.bgOpacity = Number(e.target.value) / 100;
  preview();
}

async function chooseImage() {
  pickingImage.value = true;
  try {
    const dataUrl = await pickImage();
    if (!dataUrl) return;
    const url = await saveBackground(dataUrl);
    theme.bgImage = url;
    preview();
    emit("toast", { type: "ok", text: "背景图片已应用" });
  } catch (e) {
    emit("toast", { type: "err", text: "设置背景失败：" + (e.message || e) });
  } finally {
    pickingImage.value = false;
  }
}

async function removeImage() {
  theme.bgImage = "";
  preview();
  try {
    await clearBackground();
  } catch {
    /* 忽略：文件不存在也无所谓 */
  }
}

/* ------------------------------ 保存 / 取消 ------------------------------ */

function cancel() {
  applyTheme(originalTheme);
  emit("close");
}

async function doSave() {
  busy.value = true;
  try {
    const cfg = {
      ...props.config,
      serverUrl: form.serverUrl.trim(),
      token: form.token.trim(),
      autoSave: form.autoSave,
      autoDatePrefix: form.autoDatePrefix,
      shortcuts: { ...shortcuts },
      theme: { ...theme },
    };
    const saved = await saveConfig(cfg);
    applyTheme(saved.theme || theme);
    emit("saved", saved);
    emit("toast", { type: "ok", text: "设置已保存" });
    emit("close");
  } catch (e) {
    emit("toast", { type: "err", text: "保存失败：" + (e.message || e) });
  } finally {
    busy.value = false;
  }
}

onMounted(() => window.addEventListener("keydown", onKeydown, true));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown, true));
</script>

<template>
  <Modal title="设置" wide :close-on-overlay="false" @close="cancel">
    <div class="tabs">
      <div class="tab" :class="{ active: tab === 'general' }" @click="tab = 'general'">通用</div>
      <div class="tab" :class="{ active: tab === 'shortcuts' }" @click="tab = 'shortcuts'">快捷键</div>
      <div class="tab" :class="{ active: tab === 'theme' }" @click="tab = 'theme'">主题</div>
    </div>

    <!-- ============================ 通用 ============================ -->
    <div v-show="tab === 'general'">
      <div class="field">
        <label>云服务器地址</label>
        <input
          v-model.trim="form.serverUrl"
          class="input"
          placeholder="http://1.2.3.4:8787 或 https://note.example.com"
        />
        <div class="hint">留空表示纯本地模式，不同步、不上传任何数据。</div>
      </div>

      <div class="field">
        <label>API 访问 Token</label>
        <input v-model.trim="form.token" class="input" type="password" placeholder="与服务端 API_TOKEN 保持一致" />
      </div>

      <div class="field">
        <label>本地行为</label>
        <label class="checkbox" style="margin-bottom: 8px">
          <input v-model="form.autoSave" type="checkbox" />
          <span>
            自动保存日志
            <small>停止输入约 2 秒后自动写入本地数据库（离线同样生效）</small>
          </span>
        </label>
        <label class="checkbox">
          <input v-model="form.autoDatePrefix" type="checkbox" />
          <span>
            新建目录自动添加日期前缀
            <small>预填充 <code>YYYY-MM-DD </code>，光标落在日期之后，可自由修改或删除</small>
          </span>
        </label>
      </div>

      <div v-if="testResult" class="notice" :class="testResult.ok ? 'notice-ok' : 'notice-err'" style="margin-bottom: 10px">
        {{ testResult.msg }}
      </div>

      <div class="notice notice-info" style="margin-bottom: 10px">
        <div v-if="!online" style="margin-bottom: 4px">
          <strong>当前离线</strong>，无法测试连接；已保存的配置仍会正常保留，恢复网络后再试。
        </div>
        <div v-if="isDesktop">
          <div>本地数据库：<code>{{ info.dbPath || "…" }}</code></div>
          <div>备份目录：<code>{{ info.backupDir || "…" }}</code></div>
        </div>
        <div v-else>浏览器预览模式：数据写入本地预览服务端的 SQLite 文件。</div>
      </div>

      <div class="notice notice-warn">
        提示：目录日期前缀取自<strong>本机系统时间</strong>。若系统时间不准确，新建目录的日期也会不准确。
      </div>
    </div>

    <!-- =========================== 快捷键 =========================== -->
    <div v-show="tab === 'shortcuts'">
      <div class="hint" style="margin-bottom: 10px">
        点击右侧按键区域后，直接按下想要的组合键即可录制；按 <code>Esc</code> 取消，按
        <code>Backspace</code> 清除绑定。
      </div>

      <div class="shortcut-list">
        <div v-for="a in SHORTCUT_ACTIONS" :key="a.id" class="shortcut-row">
          <div class="shortcut-info">
            <div class="shortcut-name">{{ a.name }}</div>
            <div class="shortcut-desc">{{ a.desc }}</div>
          </div>
          <button
            class="key-btn"
            :class="{
              recording: recording === a.id,
              conflict: conflicts.has(a.id),
              empty: !shortcuts[a.id],
            }"
            @click="startRecord(a.id)"
          >
            {{ recording === a.id ? "按下组合键…" : formatCombo(shortcuts[a.id]) }}
          </button>
          <button class="btn btn-ghost btn-sm btn-icon" title="清除" @click="clearShortcut(a.id)">
            <Icon name="x" :size="12" />
          </button>
        </div>
      </div>

      <div v-if="conflicts.size" class="notice notice-err" style="margin-top: 10px">
        存在重复的快捷键，请调整标红的项，否则只有第一个生效。
      </div>

      <div style="margin-top: 10px">
        <button class="btn btn-sm" @click="resetShortcuts">恢复默认</button>
      </div>
    </div>

    <!-- ============================ 主题 ============================ -->
    <div v-show="tab === 'theme'">
      <div class="field">
        <label>主题样式</label>
        <div class="theme-cards">
          <div class="theme-card" :class="{ active: theme.mode === 'light' }" @click="setMode('light')">
            <div class="theme-preview light"></div>
            <span>浅色</span>
          </div>
          <div class="theme-card" :class="{ active: theme.mode === 'dark' }" @click="setMode('dark')">
            <div class="theme-preview dark"></div>
            <span>深色</span>
          </div>
        </div>
      </div>

      <div class="field">
        <label>自定义样式 · 背景图片</label>
        <div class="bg-row">
          <div class="bg-thumb" :class="{ empty: !theme.bgImage }">
            <img v-if="theme.bgImage" :src="theme.bgImage" alt="" />
            <span v-else>无背景</span>
          </div>
          <div class="bg-actions">
            <button class="btn btn-sm" :disabled="pickingImage" @click="chooseImage">
              <Icon name="image" :size="13" />
              {{ pickingImage ? "处理中…" : theme.bgImage ? "更换图片" : "选择图片" }}
            </button>
            <button class="btn btn-sm" :disabled="!theme.bgImage" @click="removeImage">清除</button>
            <div class="hint">支持 JPG / PNG / WebP，图片保存在本地数据目录，换电脑一起带走。</div>
          </div>
        </div>
      </div>

      <div class="field">
        <label>
          背景透明度
          <span class="field-value">{{ bgPercent }}%</span>
        </label>
        <input
          class="range"
          type="range"
          min="0"
          max="100"
          step="1"
          :disabled="!theme.bgImage"
          :value="bgPercent"
          @input="setBgOpacity"
        />
        <div class="hint">
          {{
            theme.bgImage
              ? "数值越大背景越清晰，面板也会相应变透以保证背景能透出来；调到 0% 等于不显示背景。"
              : "先选择一张背景图片，再调节这一项。"
          }}
        </div>
      </div>

      <div class="notice notice-info">
        主题改动会立即预览，点「保存」后永久生效；点「取消」会恢复打开设置前的样式。
      </div>
    </div>

    <template #foot>
      <button class="btn" @click="doBackup">
        <Icon name="database" :size="13" />
        立即备份
      </button>
      <span class="grow"></span>
      <button class="btn" :disabled="testing || !online" @click="doTest">
        {{ testing ? "测试中…" : "测试连接" }}
      </button>
      <button class="btn" @click="cancel">取消</button>
      <button class="btn btn-primary" :disabled="busy" @click="doSave">保存</button>
    </template>
  </Modal>
</template>

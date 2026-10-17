<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import TreeSidebar from "./components/TreeSidebar.vue";
import LogPanel from "./components/LogPanel.vue";
import MenuBar from "./components/MenuBar.vue";
import Modal from "./components/Modal.vue";
import Icon from "./components/Icon.vue";
import SettingsDialog from "./components/SettingsDialog.vue";
import ConflictDialog from "./components/ConflictDialog.vue";
import * as api from "./api.js";
import { datePrefix, fmtRelative, splitDatePrefix, todayDate } from "./utils.js";
import { applyTheme, normalizeTheme, toggleMode } from "./theme.js";
import { matchShortcut, normalizeShortcuts } from "./shortcuts.js";
import { openTextFile, saveTextFile, closeApp } from "./fileio.js";

/* ============================== 全局状态 ============================== */

const trees = ref([]);
const activeTreeId = ref(null);
const logs = ref([]);
const activeLog = ref(null);
const config = ref({
  serverUrl: "",
  token: "",
  autoSave: true,
  autoDatePrefix: true,
  lastSyncAt: 0,
  theme: normalizeTheme(null),
  shortcuts: normalizeShortcuts(null),
});
const pending = ref(0);
const online = ref(navigator.onLine);
const reachable = ref(true);
const syncing = ref(false);
const saving = ref(false);
const loadingLogs = ref(false);
const conflicts = ref([]);
const showConflicts = ref(false);
const showSettings = ref(false);
const settingsTab = ref("general");
const showAbout = ref(false);

const sidebar = ref(null);
const panel = ref(null);

const activeTree = computed(() => trees.value.find((t) => t.id === activeTreeId.value) || null);
const serverReady = computed(() => !!config.value.serverUrl);
const lastSyncText = computed(() =>
  config.value.lastSyncAt ? `上次同步 ${fmtRelative(config.value.lastSyncAt)}` : ""
);
const shortcuts = computed(() => normalizeShortcuts(config.value.shortcuts));
const theme = computed(() => normalizeTheme(config.value.theme));

/* ============================== 提示条 ============================== */

const toasts = ref([]);
let toastSeq = 0;

function toast(text, type = "info") {
  const id = ++toastSeq;
  toasts.value.push({ id, text, type });
  setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id);
  }, 3200);
}

/* ============================== 数据加载 ============================== */

async function loadConfig() {
  try {
    config.value = { ...config.value, ...(await api.getConfig()) };
    config.value.theme = normalizeTheme(config.value.theme);
    config.value.shortcuts = normalizeShortcuts(config.value.shortcuts);
    applyTheme(config.value.theme);
  } catch (e) {
    toast("读取配置失败：" + (e.message || e), "err");
  }
}

async function loadTrees() {
  try {
    trees.value = await api.listTrees();
    if (activeTreeId.value && !trees.value.some((t) => t.id === activeTreeId.value)) {
      activeTreeId.value = null;
      logs.value = [];
      activeLog.value = null;
    }
  } catch (e) {
    toast("加载目录失败：" + (e.message || e), "err");
  }
}

async function refreshLogs() {
  if (!activeTreeId.value) {
    logs.value = [];
    return;
  }
  loadingLogs.value = true;
  try {
    logs.value = await api.listLogs(activeTreeId.value);
    // 同步刷新当前打开日志的最新状态
    if (activeLog.value) {
      const fresh = logs.value.find((l) => l.id === activeLog.value.id);
      if (fresh) activeLog.value = { ...activeLog.value, ...fresh };
    }
  } catch (e) {
    toast("加载日志失败：" + (e.message || e), "err");
  } finally {
    loadingLogs.value = false;
  }
}

async function refreshPending() {
  try {
    pending.value = await api.pendingChanges();
  } catch {
    /* 忽略 */
  }
}

async function checkNet() {
  online.value = navigator.onLine;
  if (!serverReady.value) {
    reachable.value = navigator.onLine;
    return;
  }
  try {
    const st = await api.netStatus();
    reachable.value = !!st.reachable;
    online.value = !!st.online && reachable.value;
  } catch {
    reachable.value = false;
  }
}

/* ============================== 分支操作 ============================== */

async function selectTree(tree) {
  if (tree && tree.id === activeTreeId.value) return;
  await flushDraft();
  activeTreeId.value = tree ? tree.id : null;
  activeLog.value = null;
  await refreshLogs();
}

/** 静默保存当前草稿，避免切换时丢失内容（本地写入，无网络依赖） */
async function flushDraft() {
  if (!panel.value || !panel.value.isDirty()) return;
  const draft = panel.value.getDraft();
  if (!draft.title && !draft.content.trim()) return;
  try {
    if (draft.id) {
      await api.updateLog(draft.id, draft.title, draft.content);
    } else if (activeTreeId.value) {
      await api.createLog(activeTreeId.value, draft.title, draft.content);
    }
    await refreshLogs();
    await refreshPending();
  } catch {
    /* 静默失败，用户仍可手动保存 */
  }
}

/* ---------- 新建 / 重命名 弹窗 ---------- */

const nameDlg = reactive({
  open: false,
  mode: "add",
  parentId: null,
  node: null,
  name: "",
  error: "",
});
const nameInput = ref(null);

function openAdd(parentId) {
  nameDlg.open = true;
  nameDlg.mode = "add";
  nameDlg.parentId = parentId || null;
  nameDlg.node = null;
  nameDlg.error = "";
  // 自动预填充 `YYYY-MM-DD ` 日期前缀（取自本机系统时间，离线可用）
  nameDlg.name = config.value.autoDatePrefix !== false ? datePrefix() : "";
  nextTick(() => {
    const el = nameInput.value;
    if (!el) return;
    el.focus();
    // 光标定位在日期前缀之后，用户可直接输入目录名称
    const pos = nameDlg.name.length;
    el.setSelectionRange(pos, pos);
  });
}

function openRename(node) {
  nameDlg.open = true;
  nameDlg.mode = "rename";
  nameDlg.parentId = node.parentId || null;
  nameDlg.node = node;
  nameDlg.name = node.name;
  nameDlg.error = "";
  nextTick(() => {
    const el = nameInput.value;
    if (!el) return;
    el.focus();
    el.select();
  });
}

async function submitName() {
  const name = nameDlg.name.trim();
  if (!name) {
    nameDlg.error = "名称不能为空";
    return;
  }
  try {
    if (nameDlg.mode === "add") {
      const created = await api.createTree(nameDlg.parentId, name);
      nameDlg.open = false;
      await loadTrees();
      await refreshPending();
      if (nameDlg.parentId && sidebar.value) sidebar.value.revealNew(created.id);
      activeTreeId.value = created.id;
      activeLog.value = null;
      await refreshLogs();
      toast("分支已创建（离线可用，稍后同步）", "ok");
    } else {
      await api.renameTree(nameDlg.node.id, name);
      nameDlg.open = false;
      await loadTrees();
      await refreshPending();
      toast("已重命名", "ok");
    }
  } catch (e) {
    nameDlg.error = String(e.message || e);
  }
}

/* ---------- 删除分支弹窗 ---------- */

const delDlg = reactive({ open: false, node: null, deleteLogs: true, busy: false });

function openDelete(node) {
  delDlg.open = true;
  delDlg.node = node;
  delDlg.deleteLogs = true;
  delDlg.busy = false;
}

const delInfo = computed(() => {
  if (!delDlg.node) return { subs: 0, logs: 0 };
  const ids = new Set([delDlg.node.id]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const t of trees.value) {
      if (t.parentId && ids.has(t.parentId) && !ids.has(t.id)) {
        ids.add(t.id);
        changed = true;
      }
    }
  }
  return { subs: ids.size - 1, logs: 0 };
});

async function confirmDelete() {
  if (!delDlg.node) return;
  delDlg.busy = true;
  try {
    await api.deleteTree(delDlg.node.id, delDlg.deleteLogs);
    if (activeTreeId.value === delDlg.node.id) {
      activeTreeId.value = null;
      activeLog.value = null;
      logs.value = [];
    }
    delDlg.open = false;
    await loadTrees();
    await refreshPending();
    toast(delDlg.deleteLogs ? "分支及其日志已删除" : "分支已删除，日志已移动到上一级", "ok");
  } catch (e) {
    toast("删除失败：" + (e.message || e), "err");
  } finally {
    delDlg.busy = false;
  }
}

/* ---------- 拖拽排序 ---------- */

async function onReorder(updates) {
  const map = new Map(trees.value.map((t) => [t.id, t]));
  for (const u of updates) {
    const t = map.get(u.id);
    if (t) {
      t.parentId = u.parentId;
      t.sort = u.sort;
      t.syncStatus = "unsynced";
    }
  }
  trees.value = [...trees.value];
  try {
    await api.reorderTrees(updates);
    await refreshPending();
  } catch (e) {
    toast("排序保存失败：" + (e.message || e), "err");
    await loadTrees();
  }
}

/* ============================== 日志操作 ============================== */

async function newLog() {
  await flushDraft();
  activeLog.value = null;
  nextTick(() => {
    const el = document.querySelector(".title-input");
    if (el) el.focus();
  });
}

async function openLog(log) {
  if (activeLog.value && activeLog.value.id === log.id) return;
  await flushDraft();
  try {
    activeLog.value = await api.getLog(log.id);
  } catch (e) {
    toast("打开日志失败：" + (e.message || e), "err");
  }
}

async function saveLog(payload) {
  if (saving.value) return;
  saving.value = true;
  try {
    let saved;
    if (payload.id) {
      saved = await api.updateLog(payload.id, payload.title, payload.content);
    } else {
      if (!activeTreeId.value) return;
      saved = await api.createLog(activeTreeId.value, payload.title, payload.content);
    }
    activeLog.value = saved;
    await refreshLogs();
    await refreshPending();
    toast("已保存到本地", "ok");
  } catch (e) {
    toast("保存失败：" + (e.message || e), "err");
  } finally {
    saving.value = false;
  }
}

const logDelDlg = reactive({ open: false, log: null, busy: false });

function askDeleteLog(log) {
  logDelDlg.open = true;
  logDelDlg.log = log;
}

async function confirmDeleteLog() {
  if (!logDelDlg.log) return;
  logDelDlg.busy = true;
  try {
    await api.deleteLog(logDelDlg.log.id);
    if (activeLog.value && activeLog.value.id === logDelDlg.log.id) activeLog.value = null;
    logDelDlg.open = false;
    await refreshLogs();
    await refreshPending();
    toast("日志已删除", "ok");
  } catch (e) {
    toast("删除失败：" + (e.message || e), "err");
  } finally {
    logDelDlg.busy = false;
  }
}

/* ============================== 同步 ============================== */

async function doSync() {
  if (syncing.value) return;
  if (!serverReady.value) {
    toast("请先在菜单「设置」中配置云服务器地址与 Token", "warn");
    openSettings("general");
    return;
  }
  syncing.value = true;
  try {
    await flushDraft();
    const report = await api.syncNow();

    if (report.conflicts && report.conflicts.length) {
      conflicts.value = report.conflicts.map((c) => ({
        ...c,
        title: c.dataType === "tree" ? c.localName || c.remoteName || c.id : c.localTitle || c.remoteTitle || c.id,
      }));
      showConflicts.value = true;
      toast(`同步完成，但有 ${report.conflicts.length} 条数据冲突需要确认`, "warn");
    } else {
      toast(`同步完成：上传 ${report.pushed} 条，拉取 ${report.pulled} 条`, "ok");
    }
    await afterSync();
  } catch (e) {
    toast("同步失败：" + (e.message || e) + "（本地数据不受影响）", "err");
  } finally {
    syncing.value = false;
  }
}

async function onResolveConflicts(choices) {
  showConflicts.value = false;
  syncing.value = true;
  try {
    const report = await api.resolveConflicts(choices);
    toast(`冲突已处理：覆盖 ${report.pushed} 条，采用云端 ${report.pulled} 条`, "ok");
    await afterSync();
  } catch (e) {
    toast("处理冲突失败：" + (e.message || e), "err");
  } finally {
    syncing.value = false;
  }
}

async function afterSync() {
  await loadConfig();
  await loadTrees();
  await refreshLogs();
  await refreshPending();
  await checkNet();
}

/* ============================== 导入 / 导出 ============================== */

const importDlg = reactive({ open: false, name: "", payload: null, mode: "merge", busy: false, stat: null });

async function doExport() {
  try {
    const data = await api.exportData();
    const stat = { trees: (data.trees || []).length, logs: (data.logs || []).length };
    const text = JSON.stringify(data, null, 2);
    const path = await saveTextFile(`工作日志备份-${todayDate()}.json`, text);
    if (!path) return;
    toast(`已导出 ${stat.trees} 个分支、${stat.logs} 篇日志`, "ok");
  } catch (e) {
    toast("导出失败：" + (e.message || e), "err");
  }
}

async function doImport() {
  let file;
  try {
    file = await openTextFile(".json");
  } catch (e) {
    toast("读取文件失败：" + (e.message || e), "err");
    return;
  }
  if (!file) return;

  let payload;
  try {
    payload = JSON.parse(file.content);
  } catch {
    toast("文件内容不是合法的 JSON，无法导入", "err");
    return;
  }
  if (!payload || !Array.isArray(payload.trees) || !Array.isArray(payload.logs)) {
    toast("这不是本软件导出的备份文件", "err");
    return;
  }

  importDlg.open = true;
  importDlg.name = file.name;
  importDlg.payload = payload;
  importDlg.mode = "merge";
  importDlg.busy = false;
  importDlg.stat = { trees: payload.trees.length, logs: payload.logs.length };
}

async function confirmImport() {
  if (!importDlg.payload) return;
  importDlg.busy = true;
  try {
    const res = await api.importData(importDlg.payload, importDlg.mode);
    importDlg.open = false;
    await loadTrees();
    await refreshPending();
    if (activeTreeId.value) await refreshLogs();
    toast(
      importDlg.mode === "replace"
        ? `已覆盖导入：${res.trees} 个分支、${res.logs} 篇日志`
        : `已合并导入：新增/更新 ${res.trees} 个分支、${res.logs} 篇日志`,
      "ok"
    );
  } catch (e) {
    toast("导入失败：" + (e.message || e), "err");
  } finally {
    importDlg.busy = false;
  }
}

/* ============================== 设置 / 主题 ============================== */

function openSettings(tab = "general") {
  settingsTab.value = tab;
  showSettings.value = true;
}

async function quickToggleTheme() {
  const next = toggleMode(config.value.theme);
  applyTheme(next);
  try {
    const saved = await api.saveConfig({ ...config.value, theme: next });
    config.value = { ...config.value, ...saved, theme: normalizeTheme(next) };
  } catch {
    config.value = { ...config.value, theme: next };
  }
  toast(next.mode === "dark" ? "已切换到深色主题" : "已切换到浅色主题", "ok");
}

async function doBackup() {
  try {
    const path = await api.backupDb();
    toast("已备份到：" + path, "ok");
  } catch (e) {
    toast("备份失败：" + (e.message || e), "err");
  }
}

async function doOpenDir() {
  try {
    const path = await api.openDataDir();
    toast("已打开数据目录：" + path, "ok");
  } catch (e) {
    toast("打开目录失败：" + (e.message || e), "err");
  }
}

async function doQuit() {
  try {
    await closeApp();
  } catch {
    window.close();
  }
}

/* ============================== 菜单定义 ============================== */

const menus = computed(() => [
  {
    id: "file",
    label: "文件",
    items: [
      { id: "newLog", label: "新建日志", shortcut: "newLog", disabled: !activeTreeId.value },
      { id: "newTree", label: "新建分支", shortcut: "newTree" },
      { sep: true },
      { id: "import", label: "导入数据…" },
      { id: "export", label: "导出数据…", shortcut: "exportData" },
      { sep: true },
      { id: "backup", label: "备份本地数据库" },
      { id: "openDir", label: "打开数据目录" },
      { sep: true },
      { id: "quit", label: "退出" },
    ],
  },
  {
    id: "view",
    label: "视图",
    items: [
      { id: "toggleTheme", label: "切换深浅色", shortcut: "toggleTheme" },
      { id: "search", label: "搜索日志", shortcut: "search", disabled: !activeTreeId.value },
      { sep: true },
      { id: "refresh", label: "刷新数据" },
    ],
  },
  {
    id: "settings",
    label: "设置",
    items: [
      { id: "settings", label: "打开设置…", shortcut: "settings" },
      { sep: true },
      { id: "shortcuts", label: "快捷键设置…" },
      { id: "theme", label: "主题设置…" },
    ],
  },
  {
    id: "help",
    label: "帮助",
    items: [
      { id: "about", label: "关于工作日志笔记本" },
      { id: "guide", label: "使用说明" },
    ],
  },
]);

function onMenu(id) {
  switch (id) {
    case "newLog":
      if (activeTreeId.value) newLog();
      break;
    case "newTree":
      openAdd(null);
      break;
    case "import":
      doImport();
      break;
    case "export":
      doExport();
      break;
    case "backup":
      doBackup();
      break;
    case "openDir":
      doOpenDir();
      break;
    case "quit":
      doQuit();
      break;
    case "toggleTheme":
      quickToggleTheme();
      break;
    case "search":
      if (panel.value) panel.value.focusSearch();
      break;
    case "refresh":
      refreshAll();
      break;
    case "settings":
      openSettings("general");
      break;
    case "shortcuts":
      openSettings("shortcuts");
      break;
    case "theme":
      openSettings("theme");
      break;
    case "about":
    case "guide":
      showAbout.value = true;
      break;
    default:
      break;
  }
}

async function refreshAll() {
  await loadConfig();
  await loadTrees();
  await refreshLogs();
  await refreshPending();
  await checkNet();
  toast("数据已刷新", "ok");
}

/* ============================== 快捷键 ============================== */

function runAction(action) {
  if (action === "save") {
    if (panel.value) panel.value.doSave();
    return;
  }
  if (action === "sync") {
    if (!syncing.value) doSync();
    return;
  }
  onMenu(action);
}

function isTyping(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

function anyDialogOpen() {
  return (
    showSettings.value ||
    showAbout.value ||
    showConflicts.value ||
    nameDlg.open ||
    delDlg.open ||
    logDelDlg.open ||
    importDlg.open
  );
}

function onKeydown(e) {
  if (anyDialogOpen()) return;
  const action = matchShortcut(e, shortcuts.value);
  if (!action) return;

  const hasMod = e.ctrlKey || e.metaKey || e.altKey;
  // 在输入框里只响应带修饰键的组合，避免影响正常打字
  if (!hasMod && isTyping(e.target)) return;

  e.preventDefault();
  runAction(action);
}

/* ============================== 生命周期 ============================== */

function onOnline() {
  checkNet();
  toast("网络已恢复，可以同步了", "ok");
}
function onOffline() {
  online.value = false;
  reachable.value = false;
  toast("已离线，所有操作仍可正常使用", "warn");
}

let netTimer = null;

onMounted(async () => {
  await loadConfig();
  await loadTrees();
  await refreshPending();
  await checkNet();
  if (trees.value.length) {
    const first = trees.value.find((t) => !t.parentId) || trees.value[0];
    if (first) {
      activeTreeId.value = first.id;
      await refreshLogs();
    }
  }
  window.addEventListener("online", onOnline);
  window.addEventListener("offline", onOffline);
  window.addEventListener("keydown", onKeydown);
  netTimer = setInterval(checkNet, 30000);
});

onBeforeUnmount(() => {
  window.removeEventListener("online", onOnline);
  window.removeEventListener("offline", onOffline);
  window.removeEventListener("keydown", onKeydown);
  clearInterval(netTimer);
});
</script>

<template>
  <div class="app">
    <MenuBar
      :menus="menus"
      :shortcuts="shortcuts"
      :online="online"
      :pending="pending"
      :syncing="syncing"
      @action="onMenu"
    />

    <div class="app-body">
      <TreeSidebar
        ref="sidebar"
        :trees="trees"
        :active-id="activeTreeId"
        :pending-count="pending"
        :online="online"
        :server-ready="serverReady"
        :syncing="syncing"
        :last-sync-text="lastSyncText"
        @select="selectTree"
        @add="openAdd"
        @rename="openRename"
        @remove="openDelete"
        @reorder="onReorder"
        @sync="doSync"
      />

      <LogPanel
        ref="panel"
        :tree="activeTree"
        :logs="logs"
        :log="activeLog"
        :auto-save="config.autoSave !== false"
        :saving="saving"
        :loading="loadingLogs"
        @save="saveLog"
        @new-log="newLog"
        @select-log="openLog"
        @delete-log="askDeleteLog"
      />
    </div>

    <!-- 新建 / 重命名分支 -->
    <Modal
      v-if="nameDlg.open"
      :title="nameDlg.mode === 'add' ? (nameDlg.parentId ? '新增子分支' : '新建分支') : '重命名分支'"
      @close="nameDlg.open = false"
    >
      <div class="field">
        <label>分支名称</label>
        <input
          ref="nameInput"
          v-model="nameDlg.name"
          class="input"
          placeholder="2026-10-17 项目A巡检"
          @keydown.enter.prevent="submitName"
        />
        <div v-if="nameDlg.mode === 'add'" class="hint">
          已自动填入今日日期前缀（<code>YYYY-MM-DD </code>），光标已定位在日期之后，直接输入名称即可；
          也可以修改或删除这段日期。
        </div>
        <div v-else class="hint">修改名称不会改变分支 ID，历史日志与同步关系不受影响。</div>
      </div>
      <div v-if="nameDlg.error" class="notice notice-err">{{ nameDlg.error }}</div>

      <template #foot>
        <button class="btn" @click="nameDlg.open = false">取消</button>
        <button class="btn btn-primary" @click="submitName">
          {{ nameDlg.mode === "add" ? "创建" : "保存" }}
        </button>
      </template>
    </Modal>

    <!-- 删除分支 -->
    <Modal v-if="delDlg.open" title="删除分支" @close="delDlg.open = false">
      <div class="notice notice-err" style="margin-bottom: 12px">
        即将删除分支：<strong>{{ delDlg.node && splitDatePrefix(delDlg.node.name).rest }}</strong>
        <span v-if="delInfo.subs" style="display: block; margin-top: 4px">
          该分支下还有 {{ delInfo.subs }} 个子分支，将一并删除。
        </span>
      </div>
      <label class="checkbox">
        <input v-model="delDlg.deleteLogs" type="checkbox" />
        <span>
          同时删除该分支下的所有日志
          <small>不勾选时，日志会被移动到上一级分支，避免数据丢失</small>
        </span>
      </label>
      <div class="hint">删除操作只写本地数据库，联网同步后云端才会同步删除。</div>

      <template #foot>
        <button class="btn" @click="delDlg.open = false">取消</button>
        <button class="btn btn-danger" :disabled="delDlg.busy" @click="confirmDelete">
          {{ delDlg.busy ? "删除中…" : "确认删除" }}
        </button>
      </template>
    </Modal>

    <!-- 删除日志 -->
    <Modal v-if="logDelDlg.open" title="删除日志" @close="logDelDlg.open = false">
      <div class="notice notice-err">
        确定删除日志「{{ logDelDlg.log && logDelDlg.log.title }}」吗？删除后可通过云端同步在其他设备恢复（若已同步）。
      </div>
      <template #foot>
        <button class="btn" @click="logDelDlg.open = false">取消</button>
        <button class="btn btn-danger" :disabled="logDelDlg.busy" @click="confirmDeleteLog">确认删除</button>
      </template>
    </Modal>

    <!-- 导入数据 -->
    <Modal v-if="importDlg.open" title="导入数据" @close="importDlg.open = false">
      <div class="notice notice-info" style="margin-bottom: 12px">
        已读取文件：<strong>{{ importDlg.name }}</strong>
        <div style="margin-top: 4px">
          包含 {{ importDlg.stat && importDlg.stat.trees }} 个分支、{{ importDlg.stat && importDlg.stat.logs }} 篇日志。
        </div>
      </div>

      <div class="field">
        <label>导入方式</label>
        <label class="checkbox" style="margin-bottom: 8px">
          <input v-model="importDlg.mode" type="radio" value="merge" />
          <span>
            合并（推荐）
            <small>保留现有数据，按 ID 合并；同一份数据以更新时间较新的为准</small>
          </span>
        </label>
        <label class="checkbox">
          <input v-model="importDlg.mode" type="radio" value="replace" />
          <span>
            覆盖
            <small>先清空本地全部分支与日志，再导入文件内容（本地原有数据将丢失）</small>
          </span>
        </label>
      </div>

      <div v-if="importDlg.mode === 'replace'" class="notice notice-err">
        覆盖导入不可撤销，建议先通过「文件 → 导出数据」备份当前内容。
      </div>

      <template #foot>
        <button class="btn" @click="importDlg.open = false">取消</button>
        <button
          class="btn"
          :class="importDlg.mode === 'replace' ? 'btn-danger' : 'btn-primary'"
          :disabled="importDlg.busy"
          @click="confirmImport"
        >
          {{ importDlg.busy ? "导入中…" : "开始导入" }}
        </button>
      </template>
    </Modal>

    <!-- 设置 -->
    <SettingsDialog
      v-if="showSettings"
      :config="config"
      :online="online"
      :initial-tab="settingsTab"
      @close="showSettings = false"
      @saved="(c) => (config = { ...config, ...c, theme: normalizeTheme(c.theme) })"
      @toast="(t) => toast(t.text, t.type)"
    />

    <!-- 冲突处理 -->
    <ConflictDialog
      v-if="showConflicts"
      :conflicts="conflicts"
      @close="showConflicts = false"
      @submit="onResolveConflicts"
    />

    <!-- 关于 / 使用说明 -->
    <Modal v-if="showAbout" title="关于工作日志笔记本" @close="showAbout = false">
      <div class="notice notice-info" style="margin-bottom: 10px">
        <strong>工作日志笔记本</strong> v1.0 · 离线优先的本地工作日志工具
        <div style="margin-top: 4px">数据全部保存在本机，联网同步仅在点击「同步」时发生。</div>
      </div>
      <div class="hint" style="line-height: 2">
        <div><strong>文件 → 导入/导出</strong>：把全部分支与日志导出为 JSON 备份，或从备份恢复。</div>
        <div><strong>设置 → 快捷键</strong>：点击按键区域后直接按下组合键即可录制。</div>
        <div><strong>设置 → 主题</strong>：切换浅色 / 深色样式，也可用自己的图片做背景并调节背景透明度。</div>
        <div><strong>顶部菜单</strong>右侧显示在线状态与待同步条数。</div>
      </div>
      <template #foot>
        <button class="btn btn-primary" @click="showAbout = false">知道了</button>
      </template>
    </Modal>

    <!-- 同步遮罩 -->
    <div v-if="syncing" class="sync-mask">
      <div class="spin-wrap">
        <span class="spinner"></span>
        正在与云端同步…
      </div>
    </div>

    <!-- 提示条 -->
    <div class="toast-wrap">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.type">{{ t.text }}</div>
    </div>
  </div>
</template>

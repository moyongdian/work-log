/**
 * 快捷键：定义、录制、匹配
 *
 * 组合键统一存成 "Ctrl+Shift+N" 这种字符串，便于持久化与展示。
 */

export const SHORTCUT_ACTIONS = [
  { id: "newLog", name: "新建日志", desc: "在当前分支下新建一篇日志", def: "Ctrl+N" },
  { id: "newTree", name: "新建分支", desc: "新建一个一级分支", def: "Ctrl+Shift+N" },
  { id: "save", name: "保存日志", desc: "立即保存当前编辑内容", def: "Ctrl+S" },
  { id: "sync", name: "同步到云端", desc: "推送本地变更并拉取云端数据", def: "F5" },
  { id: "search", name: "搜索日志", desc: "聚焦右上角搜索框", def: "Ctrl+F" },
  { id: "settings", name: "打开设置", desc: "打开设置窗口", def: "Ctrl+," },
  { id: "toggleTheme", name: "切换深浅色", desc: "在浅色与深色主题间切换", def: "Ctrl+Shift+L" },
  { id: "exportData", name: "导出数据", desc: "导出全部日志为 JSON", def: "Ctrl+E" },
];

const MODIFIER_KEYS = ["Control", "Shift", "Alt", "Meta"];

export function defaultShortcuts() {
  const out = {};
  for (const a of SHORTCUT_ACTIONS) out[a.id] = a.def;
  return out;
}

export function normalizeShortcuts(raw) {
  const out = defaultShortcuts();
  if (!raw || typeof raw !== "object") return out;
  for (const a of SHORTCUT_ACTIONS) {
    const v = raw[a.id];
    if (typeof v === "string" && v.trim()) out[a.id] = v.trim();
  }
  return out;
}

/** 键盘事件 → "Ctrl+Shift+N"；只按修饰键时返回 null */
export function eventToCombo(e) {
  if (MODIFIER_KEYS.includes(e.key)) return null;
  const parts = [];
  if (e.ctrlKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey) parts.push("Shift");

  let key = e.key;
  if (key === " ") key = "Space";
  else if (key === "Escape") key = "Esc";
  else if (key.length === 1) key = key.toUpperCase();
  else if (key.startsWith("Arrow")) key = key.slice(5);

  // 只按 F1~F12 时不带修饰键也算合法
  if (!parts.length && !/^F\d{1,2}$/.test(key)) {
    // 允许单字母快捷键，但要求是字母/数字
    if (!/^[A-Z0-9]$/.test(key)) return null;
  }
  parts.push(key);
  return parts.join("+");
}

/** 从快捷键表反查动作 id */
export function matchShortcut(e, shortcuts) {
  const combo = eventToCombo(e);
  if (!combo) return null;
  for (const [action, value] of Object.entries(shortcuts || {})) {
    if (value === combo) return action;
  }
  return null;
}

/** 展示用：Windows 下显示 Ctrl+Shift+N */
export function formatCombo(combo) {
  if (!combo) return "未设置";
  const isMac = navigator.platform.toLowerCase().includes("mac");
  if (!isMac) return combo;
  return combo
    .replace("Ctrl", "⌘")
    .replace("Alt", "⌥")
    .replace("Shift", "⇧")
    .replace(/\+/g, "");
}

/** 检查是否有重复绑定，返回冲突的动作 id 集合 */
export function findConflicts(shortcuts) {
  const seen = new Map();
  const dup = new Set();
  for (const [action, combo] of Object.entries(shortcuts || {})) {
    if (!combo) continue;
    if (seen.has(combo)) {
      dup.add(action);
      dup.add(seen.get(combo));
    } else {
      seen.set(combo, action);
    }
  }
  return dup;
}

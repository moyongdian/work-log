/**
 * 主题系统
 *
 * 只有两件事：
 *  1. 更换样式：浅色 / 深色
 *  2. 自定义样式：用自己的图片做背景，并可调节背景透明度
 *
 * 全部通过 CSS 变量注入，样式表只读变量，不写死颜色。
 */

export const DEFAULT_THEME = {
  mode: "light", // light | dark
  bgImage: "", // 背景图 URL（/media/xxx）
  bgOpacity: 0.6, // 背景透明度 0~1：越大背景越清晰
};

export function normalizeTheme(t) {
  const src = t && typeof t === "object" ? t : {};
  return {
    mode: src.mode === "dark" ? "dark" : "light",
    bgImage: typeof src.bgImage === "string" ? src.bgImage : "",
    bgOpacity: clamp(Number(src.bgOpacity ?? DEFAULT_THEME.bgOpacity), 0, 1),
  };
}

function clamp(v, min, max) {
  if (Number.isNaN(v)) return min;
  return Math.min(max, Math.max(min, v));
}

/**
 * 把主题写进 :root 的 CSS 变量
 *
 * 背景透明度同时驱动两件事，保证「数值调高 = 背景更清楚」符合直觉：
 *  · 背景图层自身的不透明度 → --bg-opacity
 *  · 面板的不透明度 → --panel-opacity（背景越清晰，面板越透，否则永远看不到背景）
 */
export function applyTheme(theme) {
  const t = normalizeTheme(theme);
  const root = document.documentElement;
  const hasBg = !!t.bgImage;

  root.dataset.theme = t.mode;
  root.style.setProperty("--bg-image", hasBg ? `url("${t.bgImage}")` : "none");
  root.style.setProperty("--bg-opacity", String(hasBg ? t.bgOpacity : 0));
  root.style.setProperty("--panel-opacity", String(hasBg ? 1 - t.bgOpacity * 0.45 : 1));
  root.classList.toggle("has-bg", hasBg);

  // 让窗口原生底色与主题一致，避免拖动窗口时闪白边
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", t.mode === "dark" ? "#0e141d" : "#f4f5f7");

  // 缓存一份，index.html 里的内联脚本会在首屏渲染前先套用，避免闪白
  try {
    localStorage.setItem("nb.theme", JSON.stringify(t));
  } catch {
    /* 隐私模式等场景忽略 */
  }
}

/** 生成深色/浅色切换后的主题（供快捷键使用） */
export function toggleMode(theme) {
  const t = normalizeTheme(theme);
  return { ...t, mode: t.mode === "dark" ? "light" : "dark" };
}

/** 通用工具函数 */

/** 生成本地时间戳（毫秒） */
export const now = () => Date.now();

function pad(n) {
  return n < 10 ? "0" + n : "" + n;
}

/** 取本机系统时间的 YYYY-MM-DD（离线可用，不依赖网络） */
export function todayDate() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 新建分支时的默认名称前缀：`YYYY-MM-DD `（末尾带一个空格，光标落在空格之后） */
export function datePrefix() {
  return todayDate() + " ";
}

/** 时间戳 → MM-DD HH:mm */
export function fmtShort(ts) {
  if (!ts) return "";
  const d = new Date(Number(ts));
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 时间戳 → YYYY-MM-DD HH:mm:ss */
export function fmtFull(ts) {
  if (!ts) return "";
  const d = new Date(Number(ts));
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}:${pad(d.getSeconds())}`;
}

/** 相对时间：刚刚 / 5 分钟前 / 3 小时前 / MM-DD HH:mm */
export function fmtRelative(ts) {
  if (!ts) return "";
  const diff = Date.now() - Number(ts);
  if (diff < 60_000) return "刚刚";
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)} 小时前`;
  return fmtShort(ts);
}

/** 从 Markdown 正文提取纯文本摘要 */
export function plainPreview(md, len = 60) {
  if (!md) return "";
  const text = md
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#>*`_~\-]{1,}/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > len ? text.slice(0, len) + "…" : text;
}

/** 目录名中剥离日期前缀，用于展示 */
export function splitDatePrefix(name) {
  const m = /^(\d{4}-\d{2}-\d{2}\s*)([\s\S]*)$/.exec(name || "");
  if (m) return { date: m[1].trim(), rest: m[2] };
  return { date: "", rest: name || "" };
}

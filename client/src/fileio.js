/**
 * 文件读写桥接
 *
 * 桌面端（pywebview）走原生「另存为 / 打开」对话框；
 * 浏览器预览模式回退到 Blob 下载 + input[type=file]。
 */

function bridge() {
  return (typeof window !== "undefined" && window.pywebview && window.pywebview.api) || null;
}

/** pywebview 注入 js_api 有延迟，这里等一小会儿 */
function waitBridge(timeout = 600) {
  return new Promise((resolve) => {
    if (bridge()) return resolve(bridge());
    const start = Date.now();
    const timer = setInterval(() => {
      if (bridge()) {
        clearInterval(timer);
        resolve(bridge());
      } else if (Date.now() - start > timeout) {
        clearInterval(timer);
        resolve(null);
      }
    }, 50);
  });
}

export const hasNativeDialog = () => !!bridge();

/** 保存文本文件，返回最终路径（浏览器下返回文件名） */
export async function saveTextFile(filename, text, mime = "application/json") {
  const b = await waitBridge();
  if (b && b.save_text) {
    const res = await b.save_text(filename, text);
    if (res && res.canceled) return null;
    return (res && res.path) || filename;
  }

  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  return filename;
}

/** 打开文本文件，返回 { name, content } 或 null */
export async function openTextFile(accept = ".json") {
  const b = await waitBridge();
  if (b && b.open_text) {
    const res = await b.open_text(accept);
    if (!res || res.canceled) return null;
    return { name: res.name, content: res.content };
  }

  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.onchange = () => {
      const file = input.files && input.files[0];
      if (!file) return resolve(null);
      const reader = new FileReader();
      reader.onload = () => resolve({ name: file.name, content: String(reader.result || "") });
      reader.onerror = () => resolve(null);
      reader.readAsText(file);
    };
    input.click();
  });
}

/** 选择图片，返回 dataURL 或 null */
export async function pickImage() {
  const b = await waitBridge();
  if (b && b.pick_image) {
    const res = await b.pick_image();
    if (!res || res.canceled) return null;
    return res.dataUrl || null;
  }

  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/png,image/jpeg,image/webp,image/gif";
    input.onchange = () => {
      const file = input.files && input.files[0];
      if (!file) return resolve(null);
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => resolve(null);
      reader.readAsDataURL(file);
    };
    input.click();
  });
}

/** 退出程序：桌面端走原生接口，浏览器下退化为 window.close() */
export async function closeApp() {
  const b = await waitBridge();
  if (b && b.close_app) {
    await b.close_app();
    return;
  }
  window.close();
}

"""生成 Tauri 打包所需的应用图标（纯标准库实现，无需 Pillow）。

用法：python scripts/gen_icons.py
输出：client/src-tauri/icons/*.png 与 icon.ico
"""
import os
import struct
import zlib

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client", "src-tauri", "icons")

BG_TOP = (37, 99, 235)
BG_BOTTOM = (29, 78, 216)
PAGE = (255, 255, 255)
LINE = (191, 210, 245)
SPINE = (30, 64, 175)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def rounded_rect(x, y, x0, y0, x1, y1, r):
    """判断点是否落在圆角矩形内。"""
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx = x - cx
    dy = y - cy
    return dx * dx + dy * dy <= r * r


def render(size):
    """渲染一张 RGBA 图标，返回 bytes 行序列（已带 filter 字节）。"""
    s = float(size)
    pad = s * 0.055
    radius = s * 0.22

    # 页面区域（白色笔记本）
    px0, px1 = s * 0.30, s * 0.78
    py0, py1 = s * 0.20, s * 0.80
    pr = s * 0.055

    rows = []
    for y in range(size):
        row = bytearray()
        row.append(0)  # filter type 0
        for x in range(size):
            fx, fy = x + 0.5, y + 0.5
            if not rounded_rect(fx, fy, pad, pad, s - pad, s - pad, radius):
                row.extend((0, 0, 0, 0))
                continue

            t = fy / s
            r, g, b = lerp(BG_TOP, BG_BOTTOM, t)
            a = 255

            # 书脊
            if rounded_rect(fx, fy, s * 0.24, s * 0.16, s * 0.32, s * 0.84, s * 0.03):
                r, g, b = SPINE

            # 页面
            if rounded_rect(fx, fy, px0, py0, px1, py1, pr):
                r, g, b = PAGE
                # 三条横线
                for i, ly in enumerate((0.34, 0.47, 0.60)):
                    lx1 = 0.68 if i == 2 else 0.72
                    if s * 0.37 <= fx <= s * lx1 and abs(fy - s * ly) <= max(1.0, s * 0.022):
                        r, g, b = LINE
            row.extend((r, g, b, a))
        rows.append(bytes(row))
    return b"".join(rows)


def png_bytes(size):
    raw = render(size)
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    idat = zlib.compress(raw, 9)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def write(name, size):
    path = os.path.join(OUT_DIR, name)
    with open(path, "wb") as f:
        f.write(png_bytes(size))
    print("wrote", path)


def write_ico(sizes, name="icon.ico"):
    images = [png_bytes(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", 0 if s >= 256 else s, 0 if s >= 256 else s, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path = os.path.join(OUT_DIR, name)
    with open(path, "wb") as f:
        f.write(header + entries + b"".join(images))
    print("wrote", path)


def write_icns(name="icon.icns"):
    """生成 macOS 图标（PNG 压缩的 icns，现代 macOS 直接支持）。"""
    entries = [
        (b"ic07", 128),
        (b"ic08", 256),
        (b"ic09", 512),
    ]
    body = b""
    for tag, size in entries:
        data = png_bytes(size)
        body += tag + struct.pack(">I", len(data) + 8) + data
    path = os.path.join(OUT_DIR, name)
    with open(path, "wb") as f:
        f.write(b"icns" + struct.pack(">I", len(body) + 8) + body)
    print("wrote", path)


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    write("32x32.png", 32)
    write("128x128.png", 128)
    write("128x128@2x.png", 256)
    write("icon.png", 512)
    write("Square30x30Logo.png", 30)
    write("Square44x44Logo.png", 44)
    write("Square71x71Logo.png", 71)
    write("Square89x89Logo.png", 89)
    write("Square107x107Logo.png", 107)
    write("Square142x142Logo.png", 142)
    write("Square150x150Logo.png", 150)
    write("Square284x284Logo.png", 284)
    write("Square310x310Logo.png", 310)
    write("StoreLogo.png", 50)
    write_ico([16, 32, 48, 64, 128, 256])
    write_icns()

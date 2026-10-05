#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nsfc-report-downloader（单文件版）
==================================

一条命令下载国家自然科学基金（kd.nsfc.cn）结题报告的逐页图片，并无损合成为 PDF。

用法：
    python nsfc_report.py "https://kd.nsfc.cn/finalDetails?id=xxxxxxxx"
    python nsfc_report.py xxxxxxxx -o 报告.pdf -w 12

仅依赖 Python 3.8+ 标准库，无需安装任何第三方包。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
BASE = "https://kd.nsfc.cn"
REPORT_API = BASE + "/api/baseQuery/completeProjectReport"
INFO_API = BASE + "/api/baseQuery/conclusionProjectInfo"
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
A4_W, A4_H = 595.276, 841.89  # A4 pt
_ID_RE = re.compile(r"([0-9a-fA-F]{16,64})")
_INVALID_FS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def extract_report_id(text: str) -> str:
    """从完整网址或裸 ID 中提取报告 ID。"""
    text = text.strip()
    m = re.search(r"[?&]id=([0-9a-zA-Z]+)", text)
    if m:
        return m.group(1)
    m = _ID_RE.search(text)
    if m:
        return m.group(1)
    raise ValueError(f"无法从输入中解析出报告 ID：{text!r}")


# --------------------------------------------------------------------------- #
# 接口客户端
# --------------------------------------------------------------------------- #
class NsfcClient:
    def __init__(self, report_id: str, timeout: int = 30, user_agent: str = DEFAULT_UA):
        self.report_id = report_id
        self.timeout = timeout
        self.user_agent = user_agent

    def _referer(self) -> str:
        return f"{BASE}/finalDetails?id={self.report_id}"

    def _headers(self, ajax: bool = False) -> dict:
        h = {"User-Agent": self.user_agent, "Referer": self._referer()}
        if ajax:
            h["X-Requested-With"] = "XMLHttpRequest"
        return h

    def page_url(self, index: int, retries: int = 12):
        """第 index 页图片的相对地址；无数据 -> None；失败 -> 'ERR'。

        注意：接口只接受表单编码，发 JSON 会 400。
        """
        for attempt in range(1, retries + 1):
            try:
                body = urllib.parse.urlencode({"id": self.report_id, "index": index}).encode()
                req = urllib.request.Request(REPORT_API, data=body,
                                             headers=self._headers(ajax=True), method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                data = payload.get("data") or {}
                if payload.get("code") == 200 and data.get("url"):
                    return data["url"]
                return None
            except Exception:
                time.sleep(0.6 * attempt + random.random())
        return "ERR"

    def page_exists(self, index: int, retries: int = 12):
        """True=存在；False=越界(404)；None=无法判定。"""
        for attempt in range(1, retries + 1):
            url = self.page_url(index, retries=3)
            if url is None:
                return False
            if url == "ERR":
                time.sleep(1.0)
                continue
            try:
                req = urllib.request.Request(BASE + url, headers={
                    "User-Agent": self.user_agent, "Referer": self._referer()})
                req.get_method = lambda: "HEAD"
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.headers.get("Content-Type", "").startswith("image")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return False
                time.sleep(1.0 * attempt)
            except Exception:
                time.sleep(1.0 * attempt)
        return None

    def image_bytes(self, url_path: str, retries: int = 12):
        for attempt in range(1, retries + 1):
            try:
                req = urllib.request.Request(BASE + url_path, headers={
                    "User-Agent": self.user_agent, "Referer": self._referer()})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.read()
            except Exception:
                time.sleep(0.8 * attempt + random.random())
        return None

    def project_info(self, retries: int = 6) -> dict:
        for attempt in range(1, retries + 1):
            try:
                body = urllib.parse.urlencode({"id": self.report_id}).encode()
                req = urllib.request.Request(f"{INFO_API}/{self.report_id}", data=body,
                                             headers=self._headers(ajax=True), method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("code") == 200 and payload.get("data"):
                    return payload["data"]
                return {}
            except Exception:
                time.sleep(1.0 * attempt)
        return {}


# --------------------------------------------------------------------------- #
# 探测 + 下载
# --------------------------------------------------------------------------- #
def _page_path(images_dir: str, index: int) -> str:
    return os.path.join(images_dir, f"page_{index:03d}.png")


def _valid_png(path: str) -> bool:
    try:
        if os.path.getsize(path) < 1000:
            return False
        with open(path, "rb") as fh:
            return fh.read(8) == PNG_MAGIC
    except OSError:
        return False


def find_last_page(client: NsfcClient, max_probe: int = 4000, verbose: bool = True) -> int:
    lo, hi = 1, 4
    while client.page_exists(hi) is True and hi < max_probe:
        lo, hi = hi, min(hi * 2, max_probe)
    if verbose:
        print(f"[探测] 初始区间 lo={lo}(有效) hi={hi}(越界)")
    guard = 0
    while lo + 1 < hi and guard < 40:
        guard += 1
        mid = (lo + hi) // 2
        ok = client.page_exists(mid)
        if verbose:
            print(f"[探测] index={mid} -> {ok}")
        if ok is True:
            lo = mid
        elif ok is False:
            hi = mid
        else:
            time.sleep(2.0)
    return lo


def _fetch_one(client, images_dir, index):
    path = _page_path(images_dir, index)
    if _valid_png(path):            # 断点续传
        return index, "cached"
    url = client.page_url(index)
    if not url or url == "ERR":
        return index, "nourl"
    blob = client.image_bytes(url)
    if not blob or not blob.startswith(PNG_MAGIC):
        return index, "fail"
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, path)
    return index, "ok"


def download_pages(client, images_dir, last, workers=8, verbose=True) -> list:
    os.makedirs(images_dir, exist_ok=True)
    results, t0, done = {}, time.time(), 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, client, images_dir, i): i for i in range(1, last + 1)}
        for fut in as_completed(futures):
            index, status = fut.result()
            results[index] = status
            done += 1
            if verbose and status != "cached":
                print(f"[{done:>4}/{last}] page {index:>4}: {status}  ({time.time() - t0:.1f}s)")
    bad = sorted(i for i, s in results.items() if s not in ("ok", "cached"))
    if verbose:
        print(f"[下载] 用时 {time.time() - t0:.1f}s，成功 {last - len(bad)}/{last}")
    return bad


# --------------------------------------------------------------------------- #
# 无损 PDF 合成
# --------------------------------------------------------------------------- #
def parse_png(path: str):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != PNG_MAGIC:
        return None
    pos, idat = 8, bytearray()
    width = height = bit_depth = color_type = interlace = None
    while pos < len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
            bit_depth, color_type, interlace = chunk[8], chunk[9], chunk[12]
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if width is None:
        return None
    return width, height, bit_depth, color_type, interlace, bytes(idat)


def _decode_with_pillow(path: str):
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    with Image.open(path) as im:
        if im.mode in ("L", "1"):
            im = im.convert("L"); colors = 1
        else:
            im = im.convert("RGB"); colors = 3
        return im.size[0], im.size[1], im.tobytes(), colors


def build_pdf(image_paths, out_path: str, page_w: float = A4_W, page_h: float = A4_H) -> int:
    n = len(image_paths)
    if n == 0:
        raise ValueError("没有可用的图片")
    total_objs = 3 * n + 2
    offsets = [0] * (total_objs + 1)
    with open(out_path, "wb") as out:
        def write(b): out.write(b)
        def obj(num, body):
            offsets[num] = out.tell()
            write(f"{num} 0 obj\n".encode()); write(body); write(b"\nendobj\n")

        write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        for i, path in enumerate(image_paths, 1):
            img_num, content_num, page_num = 3 * (i - 1) + 1, 3 * (i - 1) + 2, 3 * (i - 1) + 3
            info = parse_png(path)
            direct = (info is not None and info[4] == 0 and info[2] == 8 and info[3] in (0, 2))
            if direct:
                width, height, _bd, color_type, _il, idat = info
                colors = 3 if color_type == 2 else 1
                color_space = "/DeviceRGB" if color_type == 2 else "/DeviceGray"
                stream = idat
                decode_parms = (f"/DecodeParms << /Predictor 15 /Colors {colors} "
                                f"/BitsPerComponent 8 /Columns {width} >>").encode()
            else:
                decoded = _decode_with_pillow(path)
                if decoded is None:
                    raise RuntimeError(f"不支持的 PNG 格式且未安装 Pillow，无法处理：{path}")
                width, height, raw, colors = decoded
                color_space = "/DeviceRGB" if colors == 3 else "/DeviceGray"
                stream = zlib.compress(raw, 6)
                decode_parms = b""
            header = (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                      f"/ColorSpace {color_space} /BitsPerComponent 8 /Filter /FlateDecode "
                      ).encode() + decode_parms + f" /Length {len(stream)} >>\nstream\n".encode()
            obj(img_num, header + stream + b"\nendstream")
            content = f"q {page_w:.3f} 0 0 {page_h:.3f} 0 0 cm /Im0 Do Q".encode()
            obj(content_num, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
            obj(page_num, (f"<< /Type /Page /Parent {3 * n + 1} 0 R "
                           f"/MediaBox [0 0 {page_w:.3f} {page_h:.3f}] "
                           f"/Resources << /XObject << /Im0 {img_num} 0 R >> >> "
                           f"/Contents {content_num} 0 R >>").encode())
        kids = " ".join(f"{3 * (i - 1) + 3} 0 R" for i in range(1, n + 1))
        obj(3 * n + 1, f"<< /Type /Pages /Count {n} /Kids [ {kids} ] >>".encode())
        obj(3 * n + 2, f"<< /Type /Catalog /Pages {3 * n + 1} 0 R >>".encode())
        xref = out.tell()
        write(f"xref\n0 {total_objs + 1}\n".encode())
        write(b"0000000000 65535 f \n")
        for num in range(1, total_objs + 1):
            write(f"{offsets[num]:010d} 00000 n \n".encode())
        write(f"trailer\n<< /Size {total_objs + 1} /Root {3 * n + 2} 0 R >>\n"
              f"startxref\n{xref}\n%%EOF".encode())
    return n


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #
def _safe_filename(name: str, fallback: str) -> str:
    name = _INVALID_FS.sub("_", (name or "").strip()).strip("._ ")
    return name[:120] or fallback


def _build_output_name(info: dict, report_id: str) -> str:
    ratify, proj = info.get("ratifyNo") or "", info.get("projectName") or ""
    if ratify or proj:
        base = "_".join(x for x in [ratify, proj, "结题报告"] if x)
    else:
        base = f"{report_id}_结题报告"
    return _safe_filename(base, f"{report_id}_结题报告") + ".pdf"


def _setup_console() -> None:
    """Windows 控制台默认编码（如 GBK）无法表示某些字符，避免 print 抛异常；
    同时开启行缓冲，保证重定向到文件时也能实时看到进度。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace", line_buffering=True)  # type: ignore[attr-defined]
        except Exception:
            pass


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="nsfc_report.py",
        description="下载国自然（kd.nsfc.cn）结题报告逐页图片并合成为 PDF（无损）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例:\n"
                "  python nsfc_report.py https://kd.nsfc.cn/finalDetails?id=abcddb1b...\n"
                "  python nsfc_report.py abcddb1be318ac2b96badc78c9af18aa -o 报告.pdf\n"))
    p.add_argument("source", nargs="?", help="报告网址（含 id=...）或裸报告 ID；"
                                             "省略则进入交互模式（双击运行时）")
    p.add_argument("-o", "--output", help="输出 PDF 路径（默认按项目名自动命名）")
    p.add_argument("-d", "--images-dir", help="图片目录（默认临时目录）")
    p.add_argument("--keep-images", action="store_true", help="保留下载的逐页图片")
    p.add_argument("-w", "--workers", type=int, default=8, help="并发线程数（默认 8）")
    p.add_argument("--last", type=int, default=0, help="直接指定末页页码，跳过探测")
    p.add_argument("--no-pdf", action="store_true", help="只下载图片，不合成 PDF")
    p.add_argument("--no-name", action="store_true", help="用报告 ID 命名，不查询项目名")
    p.add_argument("--timeout", type=int, default=30, help="请求超时秒数（默认 30）")
    p.add_argument("-q", "--quiet", action="store_true", help="减少输出")
    return p.parse_args(argv)


def _run(args) -> int:
    """执行下载 + 合成，返回退出码。"""
    verbose = not args.quiet
    try:
        report_id = extract_report_id(args.source)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr); return 2
    if verbose:
        print(f"报告 ID：{report_id}")

    client = NsfcClient(report_id, timeout=args.timeout)
    last = args.last if args.last > 0 else find_last_page(client, verbose=verbose)
    if last < 1:
        print("错误：未找到任何页面（请检查网址或网络）。", file=sys.stderr); return 3
    if verbose:
        print(f"共 {last} 页")

    temp_dir = None
    images_dir = args.images_dir or (temp_dir := tempfile.mkdtemp(prefix="nsfc_report_"))
    try:
        bad = download_pages(client, images_dir, last, workers=args.workers, verbose=verbose)
        if bad:
            print(f"警告：有 {len(bad)} 页下载失败：{bad}", file=sys.stderr)
        if args.no_pdf:
            print(f"图片已保存到：{os.path.abspath(images_dir)}")
            return 0 if not bad else 4

        image_paths = [_page_path(images_dir, i) for i in range(1, last + 1)
                       if os.path.exists(_page_path(images_dir, i))]
        if not image_paths:
            print("错误：没有可合成的图片。", file=sys.stderr); return 5

        if args.output:
            out_path = os.path.abspath(args.output)
        else:
            info = {} if args.no_name else client.project_info()
            out_path = os.path.join(os.getcwd(), _build_output_name(info, report_id))

        pages = build_pdf(image_paths, out_path)
        print(f"[完成] 已生成 PDF：{out_path}（{pages} 页，"
              f"{os.path.getsize(out_path) / 1048576:.1f} MB）")
        return 0
    finally:
        if temp_dir and not args.keep_images:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _pause() -> None:
    try:
        input("\n按回车键退出...")
    except (EOFError, KeyboardInterrupt):
        pass


def main(argv=None) -> int:
    _setup_console()
    args = parse_args(argv)
    interactive = args.source is None

    if interactive:  # 双击运行（不带参数）时进入交互模式
        print("=" * 52)
        print("  国自然结题报告下载器")
        print("=" * 52)
        print("提示：可把网址直接粘贴到这里（鼠标右键或 Ctrl+V），然后回车。")
        try:
            args.source = input("\n请输入国自然报告网址（含 id=...）：").strip()
        except (EOFError, KeyboardInterrupt):
            args.source = ""
        if not args.source:
            print("未输入网址，已退出。")
            _pause()
            return 2

    try:
        code = _run(args)
    except KeyboardInterrupt:
        print("\n已取消。")
        code = 130
    except Exception as exc:  # 交互模式下也要让用户看到错误信息
        print(f"发生错误：{exc}", file=sys.stderr)
        code = 1
    finally:
        if interactive:
            _pause()
    return code


if __name__ == "__main__":
    raise SystemExit(main())

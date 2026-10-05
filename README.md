# nsfc-report-downloader

> 一行命令，把国家自然科学基金（NSFC）结题报告的“逐页图片”下载下来，并**无损**合成为一个 PDF。

国家自然科学基金大数据知识管理服务门户（[kd.nsfc.cn](https://kd.nsfc.cn)）的结题报告只能在线逐页翻看，无法整册下载。本项目通过逆向其分页接口，自动抓取全部页面图片并合并为 PDF。

## ✨ 特性

- **只输入一个网址即可**：自动解析报告 ID、自动探测总页数、自动命名输出文件。
- **无需任何第三方依赖**：只用 Python 标准库（`urllib` / `zlib` / `threading`），下载与合成都不依赖 `Pillow`、`img2pdf` 等。
- **无损合成**：直接把 PNG 的压缩数据嵌入 PDF（`/FlateDecode + /Predictor 15`），不解码、不重压缩像素，**画质 100% 保留**，速度快、占用内存低。
- **并发下载 + 断点续传 + 自动重试**：站点限流较严（偶发 503/502），程序会自动退避重试；已下载的页会被跳过。
- **自动命名**：默认用「批准号_项目名称_结题报告.pdf」命名。
- **免安装可执行文件**：对没有 Python 环境的用户，可直接下载打包好的 `.exe` / 可执行文件（见下方 Releases），或双击 `run.bat`。

## 🟢 免安装使用（推荐给没有 Python 环境的用户）

不需要安装 Python，直接用打包好的可执行文件：

1. 打开本项目的 **Releases** 页面，下载对应系统的文件：
   - Windows：`nsfc-report-windows.exe`
2. 运行方式（Windows 为例）：
   - **直接双击 exe**：程序会显示提示，**把网址粘贴进去后回车**，即开始下载；跑完不会自动关窗，需按回车退出。（无需懂命令行）
   - 把网址**拖拽**到 exe 图标上；
   - 或双击仓库里的 `run.bat`，同样按提示粘贴网址回车。

> 小贴士（命令行方式）：`nsfc-report-windows.exe "https://kd.nsfc.cn/finalDetails?id=xxxxxxxx"`；  
> **不带任何参数运行即进入交互模式**，任何平台的单文件版都可这样用。

> 说明：可执行文件由 PyInstaller 打包，已内置运行环境（约 10~15 MB）。首次运行稍慢属正常。  
> 仓库已内置 GitHub Actions 工作流，**打一个 `v*` 标签或手动触发即可自动构建三平台可执行文件并发布到 Releases**。

## 📦 用 Python 运行（需要 Python 3.8+，零依赖）

整个 Python 实现只有 **一个文件**：[`nsfc_report.py`](nsfc_report.py)。下载它（或克隆本仓库）即可：

```bash
git clone https://github.com/JinchaoC/nsfc-report-downloader.git
cd nsfc-report-downloader
python nsfc_report.py "https://kd.nsfc.cn/finalDetails?id=xxxxxxxx"
```

也可以安装成 `nsfc-report` 命令（可选）：

```bash
pip install .
nsfc-report "https://kd.nsfc.cn/finalDetails?id=xxxxxxxx"
```

## 🚀 使用

```bash
# 最简用法：给网址即可，输出为「批准号_项目名_结题报告.pdf」
python nsfc_report.py "https://kd.nsfc.cn/finalDetails?id=abcddb1be318ac2b96badc78c9af18aa"

# 也可以只给 ID
python nsfc_report.py abcddb1be318ac2b96badc78c9af18aa

# 不带参数运行 = 交互模式（提示你粘贴网址，跑完暂停）
python nsfc_report.py

# 指定输出路径 / 并发数
python nsfc_report.py <url> -o my_report.pdf -w 12

# 保留下载的逐页 PNG（默认用临时目录，合成后删除）
python nsfc_report.py <url> --keep-images --images-dir ./imgs

# 只下载图片，不合成 PDF
python nsfc_report.py <url> --no-pdf --keep-images
```

### 参数

| 参数                 | 说明                      |
| ------------------ | ----------------------- |
| `source`           | 报告网址（含 `id=...`）或裸报告 ID |
| `-o, --output`     | 输出 PDF 路径（默认按项目名自动命名）   |
| `-d, --images-dir` | 图片保存目录（默认临时目录，合成后自动清理）  |
| `--keep-images`    | 保留下载的逐页图片               |
| `-w, --workers`    | 并发线程数，默认 8              |
| `--last N`         | 直接指定末页页码（跳过自动探测）        |
| `--no-pdf`         | 只下载图片，不合成 PDF           |
| `--no-name`        | 用报告 ID 命名，不查询项目名        |
| `--timeout`        | 单次请求超时秒数，默认 30          |
| `-q, --quiet`      | 减少输出                    |

### 作为库调用

`nsfc_report.py` 里的函数可直接复用：

```python
from nsfc_report import extract_report_id, NsfcClient, find_last_page, download_pages, build_pdf

rid = extract_report_id("https://kd.nsfc.cn/finalDetails?id=abcddb1be318ac2b96badc78c9af18aa")
client = NsfcClient(rid)
last = find_last_page(client)
download_pages(client, "imgs", last, workers=8)
build_pdf([f"imgs/page_{i:03d}.png" for i in range(1, last + 1)], "report.pdf")
```

## 🔧 工作原理

站点是 Vue 单页应用，数据通过 `/api` 下的接口加载。经过分析（见 `nsfc_report.py`）：

- **分页接口**  
  `POST https://kd.nsfc.cn/api/baseQuery/completeProjectReport`  
  请求体为**表单编码**（`application/x-www-form-urlencoded`），字段 `id`（报告 ID）+ `index`（页码，从 1 起）。  
  返回：`{"code":200,"data":{"url":"/report/U1/<hash>"}}`，其中 `url` 即该页 PNG 地址。
  - 注意：该接口只接受表单编码，发 JSON 会返回 400。
  - 页码越界时接口仍会给出一个 url，但实际是 404 HTML（约 150 字节），据此可用 HEAD 请求二分探测真实的最后一页。
- **项目信息接口**（用于自动命名）  
  `POST https://kd.nsfc.cn/api/baseQuery/conclusionProjectInfo/<ID>`
- **PDF 合成**  
  PNG 的 IDAT 数据本质就是“zlib 压缩 + PNG 逐行预测器”，可直接作为 PDF 图像对象嵌入：
  ```
  /Filter /FlateDecode
  /DecodeParms << /Predictor 15 /Colors 3 /BitsPerComponent 8 /Columns <W> >>
  ```
  因此**无需解码再重新压缩**，既快又无损。

## 🛠 自行打包可执行文件

想自己生成免安装可执行文件（或为其它平台打包）：

```bash
pip install pyinstaller
pyinstaller --onefile --name nsfc-report nsfc_report.py
# 产物在 dist/ 下：Windows 为 nsfc-report.exe
```

也可以直接推送一个 `v*` 标签，让仓库自带的 GitHub Actions 自动构建三平台文件并发布到 Releases：

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## ❓ 常见问题

- **下载中途报 503/502？** 站点限流，程序已内置重试与退避；也可降低并发（`-w 4`）后重跑，已下载的页会自动跳过（断点续传）。
- **双击 exe 一闪就没了 / 不能输入网址？** 新版已支持：不带参数运行时进入**交互模式**——显示提示后等待你粘贴网址，运行结束也会**暂停等你按回车**再退出。若仍闪退，多为杀毒软件误报或系统权限拦截，可尝试在命令行运行以查看报错信息。
- **想重跑/更新？** 直接加 `--keep-images --images-dir <同一目录>` 再跑，即可复用已下载图片。
- **生成的 PDF 很大？** 图片本身是 200dpi 的彩色 PNG，体积≈原始图片之和。如需瘦身可自行用工具压缩。

## ⚠️ 免责声明

本项目仅供**个人学习、研究与资料整理**之用。报告内容版权归原项目承担者与相关机构所有，请勿用于商业用途或大规模抓取。使用本工具产生的一切后果由使用者自行承担；请遵守目标网站的服务条款，并合理控制请求频率。

## 📄 License

[MIT](LICENSE) © 2026 JinchaoC

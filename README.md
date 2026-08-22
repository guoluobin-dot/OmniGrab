# 抖音内容下载工具 (Douyin Downloader)

通过真实 Chrome 浏览器会话读取**可访问的公开**抖音博主作品，在应用内预览视频或图文，再按需下载。

> 默认不需要手动复制 Cookie。首次遇到平台登录/验证码限制时，在工具打开的浏览器窗口中完成一次验证；该浏览器会话会保存在本机，后续可自动复用。

## 功能

- 粘贴博主主页链接，自动读取作品列表；`0` 表示读取到页面不再提供更多公开作品。支持 **抖音** 与 **TikTok**。
- 使用浏览器已通过平台校验的网络响应，避免旧版直接请求接口时的动态签名/Cookie 失效问题；TikTok 未登录时优先读取页面内嵌的首屏数据。
- 视频、图文作品分别解析；列表展示标题、时间与互动数据。
- 双击或点击“预览”可在应用内查看图文，视频支持播放器/浏览器打开回退。
- 支持下载当前、勾选项或全部作品；视频和图文自动分类保存。
- SQLite 下载历史去重、失败重试和进度提示。
- GUI 与 CLI 均支持；GUI 默认显示浏览器，便于完成登录或验证码。

## 快速开始

### 下载即用（分享给别人）

推送 `v*` 标签后，GitHub Actions 会自动在 Windows / macOS / Linux 上构建便携版压缩包，并挂到仓库的 Releases 页面。接收方无需安装 Python：

1. 从 Releases 下载对应系统的压缩包并解压到任意目录；
2. 双击其中的 `DouyinDownloader`（Windows 为 `.exe`）即可打开；
3. 配置、下载与日志保存在程序同级目录；若该目录不可写，自动改存到系统应用数据目录。

使用前提与已知限制：

- 目标电脑需装有 **Chrome 或 Chromium**；缺失时程序会给出下载指引。首次读取需联网让 Selenium Manager 获取匹配驱动。
- 程序未做代码签名：Windows 可能出现 SmartScreen/杀软提示（选择“仍要运行”）；macOS 首次打开需**右键 → 打开**绕过 Gatekeeper。正式签名/公证可消除提示。
- Linux 解压后请先执行 `chmod +x DouyinDownloader` 再运行。
- 仅用于你有权保存的公开内容；请遵守平台规则与版权法律。

### 本地开发安装

```bash
pip install -r requirements.txt
```

系统需安装 Chrome 或 Chromium。首次启动时 Selenium 会使用本机可用的 ChromeDriver；若无缓存驱动，则由 Selenium Manager / webdriver-manager 处理。

### 图形界面（推荐）

```bash
python main.py
```

1. 粘贴抖音博主主页链接。
2. 保持“显示浏览器窗口”开启，点击“读取作品”。
3. 如果抖音显示登录或验证码，请在弹出的浏览器内完成验证；程序会自动继续。
4. 读取完成后，双击作品行预览，勾选需要的项目后下载。

浏览器登录状态保存在 `config/browser_profile/`（已忽略，不会提交到仓库）。通常不需要配置 Cookie。界面中的 Cookie 输入框仅用于导入你已有的会话，属于可选功能。

### 命令行

```bash
# 读取并列出全部可访问作品（默认显示浏览器）
python cli.py "https://www.douyin.com/user/MS4w..." --list-only

# 读取前 20 个作品并下载
python cli.py "https://www.douyin.com/user/MS4w..." --max 20 --all

# 无头模式：仅适用于已保存登录会话且无需人工验证的场景
python cli.py "https://www.douyin.com/user/MS4w..." --headless --list-only
```

常用参数：

```text
python cli.py <URL> [选项]

--max N         最多读取 N 个作品；0 表示全部可访问作品
--all            读取后下载全部读取到的作品
--list-only      仅读取并列出，不下载
--output PATH    下载保存目录（默认 downloads）
--headless       隐藏浏览器窗口（不适合首次登录/验证码）
--cookie STRING  可选：导入现有 douyin.com Cookie
```

## 目录说明

```text
douyin_core/                共享浏览器会话、解析、下载及风险加固（已合并进本仓库）
src/core/*.py               仅保留兼容导入；抓取实现统一在 douyin_core 维护
src/gui/main_window.py      读取、预览、选择下载的桌面界面
src/gui/preview_dialog.py   视频/图文预览窗口
tests/core/                 douyin_core 的单元测试
downloads/                  默认下载目录
config/browser_profile/     本机浏览器会话（自动创建，已忽略）
```

桌面端与后端共用 `douyin_core` 包；风控检测、会话失效、默认可见浏览器、受限并发、解析容错和“默认只返回 URL”的下载策略均由该包统一处理。

## 平台限制与隐私

- 工具只读取账号在浏览器中可访问的作品；私密、删除、受权限限制或平台未返回的作品无法获取。
- 若平台显示“登录后查看更多作品”，请使用可见浏览器模式完成平台要求的登录。无头模式会提示这一限制，而不会把首屏内容误报为全部作品。
- TikTok 对自动化访问更敏感：首次使用或被标记时会弹出滑块拼图验证，请在可见浏览器窗口中手动完成一次；会话保存在本机后即可正常读取。
- 浏览器会话和可选 Cookie 都只保存在本机；不要分享它们。
- 请遵守抖音平台规则、版权与适用法律，仅用于你有权保存的内容。

## 测试

```bash
python -m compileall -q main.py cli.py src douyin_core tests
python -m pytest -q
```

## 本地打包（可选）

```bash
pip install pyinstaller
pyinstaller --noconfirm --clean DouyinDownloader.spec
# 产物：dist/DouyinDownloader/，整体压缩后即可分发
```

## 许可证

MIT License

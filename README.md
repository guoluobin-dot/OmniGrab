# 抖音内容下载工具 (Douyin Downloader)

> 用户输入抖音博主的主页链接后，程序自动读取该账号的所有作品数据（包括视频和图文图片），支持单独下载和批量下载，并保存到本地。

## ✨ 功能特性

- 🔗 **链接解析** — 输入抖音博主主页链接，自动提取博主信息和所有作品数据
- 📊 **作品展示** — 列表展示所有作品，包含类型（视频/图文）、标题、发布时间、互动数据
- 📥 **灵活下载** — 支持单独下载某个作品，也支持一键批量下载全部作品
- 📁 **自动分类** — 下载的文件按类型自动分类保存（视频 → `downloads/videos/`，图文 → `downloads/images/`）
- 📈 **进度显示** — 下载过程中实时显示进度状态
- 🚫 **智能去重** — 基于 SQLite 数据库的下载历史记录，自动跳过已下载的作品
- 🖥️ **双模式** — 同时提供 GUI 图形界面和 CLI 命令行界面
- 🔄 **重试机制** — 下载失败自动重试，提高成功率

## 📦 项目结构

```
douyin-downloader/
├── main.py                 # GUI 主入口
├── cli.py                  # 命令行入口
├── requirements.txt        # Python 依赖
├── src/
│   ├── core/
│   │   ├── douyin_api.py    # 抖音 API 核心模块
│   │   └── downloader.py    # 下载器模块
│   ├── gui/
│   │   └── main_window.py   # PyQt5 GUI 主窗口
│   ├── utils/
│   │   ├── logger.py        # 日志模块
│   │   ├── database.py      # SQLite 去重数据库
│   │   └── cookie_helper.py # Cookie 管理
│   └── __init__.py
├── tests/
│   └── test_douyin_api.py  # 单元测试
├── downloads/              # 下载文件保存目录
│   ├── videos/             # 视频文件
│   └── images/             # 图集文件
├── config/
│   └── cookie.json         # Cookie 配置文件（自动生成）
└── docs/
    └── DEVELOPMENT.md      # 开发文档
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 获取 Cookie（必需）

抖音的 API 需要登录态才能访问，请按以下步骤获取 Cookie：

1. 打开浏览器（推荐 Chrome），访问 https://www.douyin.com
2. 登录你的抖音账号
3. 按 `F12` 打开开发者工具
4. 切换到 **Network**（网络）选项卡
5. 刷新页面
6. 在请求列表中找到任意请求
7. 在请求头中找到 **Cookie** 字段
8. 复制完整的 Cookie 值

### 3a. GUI 模式（推荐）

```bash
python main.py
```

在界面中：
1. 粘贴 Cookie 到 Cookie 输入框
2. 输入抖音博主主页链接
3. 点击「解析作品」
4. 选择要下载的作品或点击「一键下载全部」

### 3b. 命令行模式

```bash
# 下载全部作品
python cli.py "https://www.douyin.com/user/MS4w..." --cookie "你的Cookie" --all

# 下载前 10 个作品
python cli.py "https://www.douyin.com/user/MS4w..." --cookie "你的Cookie" --max 10

# 仅列出作品不下载
python cli.py "https://www.douyin.com/user/MS4w..." --cookie "你的Cookie" --list-only

# 指定下载目录
python cli.py "https://www.douyin.com/user/MS4w..." --cookie "你的Cookie" --all --output ./my_downloads
```

## 📋 使用流程

```
用户输入博主主页链接
        │
        ▼
程序解析链接 → 提取 sec_uid
        │
        ▼
调用抖音 API → 获取博主信息 + 作品列表
        │
        ▼
展示作品列表（类型/标题/时间/互动数据）
        │
        ▼
用户选择作品 → 单独下载 / 批量下载
        │
        ▼
按类型分类保存 → videos/ 或 images/
        │
        ▼
显示下载进度 → 完成提示
```

## 🔧 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.8+ |
| HTTP 请求 | requests |
| GUI 界面 | PyQt5 |
| 数据库 | SQLite（内置） |
| 日志 | logging |
| 命令行 | argparse |

## 📝 配置说明

### Cookie 配置

Cookie 保存路径：`config/cookie.json`

```json
{
  "cookie": "ttwid=xxx; msToken=xxx; ..."
}
```

也可通过 GUI 界面的「保存 Cookie」按钮或 `--cookie` 命令行参数配置。

### 下载目录

默认下载目录：`downloads/`

- 视频文件：`downloads/videos/{标题}_{作品ID}.mp4`
- 图集文件：`downloads/images/{标题}_{作品ID}/001.jpg, 002.jpg, ...`

## 🧪 运行测试

```bash
python -m pytest tests/
# 或
python -m unittest tests/test_douyin_api.py
```

## ⚠️ 免责声明

- 本工具仅供**学习和个人使用**
- 请遵守抖音平台的使用条款和相关法律法规
- 下载的内容版权归原创作者所有
- 不得用于商业用途或批量爬取

## 📄 开源协议

MIT License

## 🙏 鸣谢

本项目在开发过程中参考了以下优秀开源项目：

- [TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) — 抖音/TikTok 数据采集工具
- [douyin-downloader](https://github.com/jiji262/douyin-downloader) — 抖音批量下载工具
- [f2](https://github.com/Johnserf-Seed/f2) — 多平台高速下载器
- [douyin_downloader](https://github.com/renyijiu/douyin_downloader) — 用户视频下载

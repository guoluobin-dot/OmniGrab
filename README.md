# 抖音内容下载工具 (Douyin Downloader)

> 用户只需输入抖音博主主页链接，即可自动完成 Cookie 获取、作品解析和下载，全程无需手动操作。

## ✨ 功能特性

### 全自动模式（推荐）
- 🤖 **自动获取 Cookie** — 使用浏览器自动化技术，无需手动获取和配置 Cookie
- 🔗 **一键操作** — 输入博主主页链接，自动完成解析+下载全流程
- 🎯 **对用户透明** — 不需要任何技术操作，降低使用门槛

### 核心功能
- 📊 **作品展示** — 列表展示所有作品，包含类型（视频/图文）、标题、发布时间、互动数据
- 📥 **灵活下载** — 支持单独下载和一键批量下载
- 📁 **自动分类** — 视频保存到 `downloads/videos/`，图文保存到 `downloads/images/`
- 📈 **进度显示** — 下载过程中实时显示进度状态
- 🚫 **智能去重** — 基于 SQLite 的下载历史记录，自动跳过已下载的作品
- 🔄 **重试机制** — 下载失败自动重试
- 🖥️ **双模式** — GUI 图形界面 + CLI 命令行
- 🛡️ **异常处理** — Cookie 获取失败、页面结构变化等容错处理

## 📦 项目结构

```
douyin-downloader/
├── main.py                      # GUI 主入口
├── cli.py                       # 命令行入口（支持 --auto 全自动模式）
├── requirements.txt             # Python 依赖
├── src/
│   ├── core/
│   │   ├── douyin_api.py         # 抖音 API 核心模块
│   │   ├── downloader.py         # 下载器模块
│   │   ├── auto_cookie.py        # 自动化 Cookie 获取（Selenium）
│   │   └── auto_pipeline.py      # 全自动化流程控制器
│   ├── gui/
│   │   └── main_window.py        # PyQt5 GUI 主窗口
│   └── utils/
│       ├── logger.py             # 日志模块
│       ├── database.py           # SQLite 去重数据库
│       └── cookie_helper.py      # Cookie 管理
├── tests/
│   ├── test_douyin_api.py        # API 测试
│   └── test_auto_cookie.py       # 自动 Cookie 测试
└── downloads/                    # 下载文件保存目录
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2a. 全自动模式（推荐）

#### GUI 模式
```bash
python main.py
```
1. 选择「全自动模式」
2. 输入抖音博主主页链接
3. 点击「一键下载」

#### 命令行模式
```bash
# 全自动下载全部作品
python cli.py "https://www.douyin.com/user/MS4w..." --auto

# 限制下载数量
python cli.py "https://www.douyin.com/user/MS4w..." --auto --max 10

# 仅列出作品不下载
python cli.py "https://www.douyin.com/user/MS4w..." --auto --list-only
```

### 2b. 手动模式

如果已有 Cookie，可以使用手动模式：
```bash
python cli.py "https://www.douyin.com/user/MS4w..." --cookie "你的Cookie" --all
```

## 📋 全自动流程

```
用户输入博主主页链接
        │
        ▼
自动启动浏览器 → 访问抖音 → 获取 Cookie
        │
        ▼
使用 Cookie 调用抖音 API → 获取博主信息 + 作品列表
        │
        ▼
展示作品列表（类型/标题/时间/互动数据）
        │
        ▼
自动下载 → 按类型分类保存到本地
        │
        ▼
显示下载进度 → 完成提示
```

## 🛡️ 异常处理

| 场景 | 处理方式 |
|------|----------|
| Cookie 获取失败 | 自动重试（默认 3 次），支持降级为手动模式 |
| 页面结构变化 | 多策略 Cookie 提取（滚动触发、等待加载） |
| 网络超时 | 可配置超时时间，自动重试 |
| 下载失败 | 单个文件最多重试 3 次 |
| Cookie 过期 | 检测已有 Cookie 有效性，无效时自动重新获取 |
| 浏览器驱动缺失 | 使用 webdriver-manager 自动下载驱动 |

## 🔧 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.8+ |
| HTTP 请求 | requests |
| GUI 界面 | PyQt5 |
| 浏览器自动化 | Selenium + webdriver-manager |
| 数据库 | SQLite（内置） |
| 日志 | logging |

## 📝 命令行参数

```
python cli.py <URL> [选项]

必填:
  URL                    抖音博主主页链接

模式:
  --auto                 全自动模式（自动获取 Cookie）
  --cookie STRING        手动模式，提供 Cookie

选项:
  --all                  下载全部作品
  --max N                最大下载数量（0=全部）
  --output PATH          下载保存目录（默认 downloads）
  --list-only            仅列出作品不下载
  --no-headless          显示浏览器窗口
  --no-dedup             禁用去重
```

## 🧪 运行测试

```bash
python -m pytest tests/ -v
```

## ⚠️ 免责声明

- 本工具仅供**学习和个人使用**
- 请遵守抖音平台的使用条款和相关法律法规
- 下载的内容版权归原创作者所有
- 不得用于商业用途或批量爬取

## 📄 开源协议

MIT License

## 🙏 鸣谢

- [TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)
- [douyin-downloader](https://github.com/jiji262/douyin-downloader)
- [f2](https://github.com/Johnserf-Seed/f2)

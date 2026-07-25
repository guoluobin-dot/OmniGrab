# 开发文档

## 架构设计

### 模块划分

```
┌─────────────────────────────────────┐
│           GUI (PyQt5) / CLI         │  ← 用户界面层
├─────────────────────────────────────┤
│          Downloader (下载器)         │  ← 下载管理层
├─────────────────────────────────────┤
│          DouyinAPI (API层)           │  ← API 交互层
├─────────────────────────────────────┤
│    Logger / Database / CookieHelper  │  ← 工具层
└─────────────────────────────────────┘
```

### 核心流程

1. **链接解析**：`DouyinAPI.extract_sec_uid()` 从主页 URL 提取 sec_uid
2. **用户信息**：`DouyinAPI.get_user_info()` 获取博主昵称、粉丝数等
3. **作品列表**：`DouyinAPI.get_user_posts()` 分页获取所有作品
4. **作品解析**：`DouyinAPI._parse_aweme()` 标准化每个作品数据
5. **下载执行**：`Downloader.download_post()` / `download_batch()` 下载文件
6. **去重管理**：`DownloadDB` 通过 SQLite 记录下载历史

### 抖音 API 要点

- 用户作品 API: `https://www.douyin.com/aweme/v1/web/aweme/post/`
- 认证依赖: Cookie 中的 `ttwid` 和 `msToken`
- 分页参数: `max_cursor` 游标翻页
- 每页返回: 约 20 个作品
- 作品类型: `images` 字段非空则为图集，否则为视频

### 开发计划

- [x] 核心 API 模块
- [x] 下载器模块
- [x] GUI 界面
- [x] CLI 命令行
- [x] 去重数据库
- [ ] Cookie 自动获取（Selenium）
- [ ] 代理支持
- [ ] 多线程下载
- [ ] 下载速度限制

# 开发文档

## 架构设计

### 模块划分

```
┌──────────────────────────────────────────┐
│        GUI (PyQt5) / CLI (argparse)      │  ← 用户界面层
├──────────────────────────────────────────┤
│         AutoPipeline (自动化管道)         │  ← 流程控制层
├──────────────────────────────────────────┤
│  AutoCookieFetcher │ DouyinAPI │ Downloader│  ← 核心功能层
├──────────────────────────────────────────┤
│ Logger / Database / CookieHelper          │  ← 工具层
└──────────────────────────────────────────┘
```

### 全自动化流程

1. **AutoPipeline.run()** — 统一入口
2. **Cookie 获取** — 先检查已有 Cookie，无效则启动 AutoCookieFetcher
3. **AutoCookieFetcher** — 使用 Selenium 浏览器自动化获取 Cookie
   - 启动无头 Chrome/Edge
   - 访问抖音页面
   - 提取浏览器 Cookie
   - 验证 ttwid 等关键字段
   - 滚动页面触发更多 Cookie
4. **博主解析** — DouyinAPI.extract_sec_uid() + get_user_info()
5. **作品获取** — DouyinAPI.get_user_posts() 分页获取
6. **下载执行** — Downloader.download_batch() 批量下载

### 异常处理策略

| 层级 | 异常 | 处理 |
|------|------|------|
| Cookie 获取 | 浏览器驱动缺失 | 提示安装 selenium |
| Cookie 获取 | 页面加载超时 | 重试 + 降级 |
| Cookie 获取 | Cookie 不完整 | 滚动触发 + 重试 |
| API 调用 | Cookie 过期 | 重新获取 Cookie |
| API 调用 | 请求被限流 | 随机延迟 |
| 下载 | 网络失败 | 重试 3 次 |
| 下载 | 文件已存在 | 跳过（去重） |

### 抖音 API 要点

- 用户作品 API: `https://www.douyin.com/aweme/v1/web/aweme/post/`
- 认证依赖: Cookie 中的 `ttwid` 和 `msToken`
- 分页参数: `max_cursor` 游标翻页
- 每页返回: 约 20 个作品
- 作品类型: `images` 字段非空则为图集，否则为视频

### 开发计划

- [x] 核心 API 模块
- [x] 下载器模块
- [x] GUI 界面（全自动 + 手动模式）
- [x] CLI 命令行（全自动 + 手动模式）
- [x] 自动化 Cookie 获取（Selenium）
- [x] 全自动化流程控制器
- [x] 去重数据库
- [x] 异常处理机制
- [ ] 多线程并发下载
- [ ] 下载速度限制
- [ ] Cookie 过期自动检测

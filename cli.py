#!/usr/bin/env python3
"""多平台内容下载工具的命令行入口。

支持平台：抖音、TikTok、哔哩哔哩
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from threading import Event

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from douyin_core import AutoPipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="多平台内容下载工具（浏览器会话读取模式）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 抖音：读取并列出全部公开作品
  python cli.py https://www.douyin.com/user/MS4w...

  # 抖音：读取前 20 个作品并下载
  python cli.py https://www.douyin.com/user/MS4w... --max 20 --all

  # B站：读取用户主页视频并下载（内置+yt-dlp双引擎）
  python cli.py https://space.bilibili.com/123456 --max 30 --all

  # B站：仅列出不下载
  python cli.py https://space.bilibili.com/123456 --list-only

  # B站：使用 yt-dlp 引擎（支持合集、番剧、字幕、更高画质）
  python cli.py https://space.bilibili.com/123456 --max 50 --all --ytdlp

  # TikTok：读取用户主页
  python cli.py https://www.tiktok.com/@username --max 20 --all

  # 小红书：读取博主主页视频+图文并下载
  python cli.py https://www.xiaohongshu.com/user/profile/xxx --max 30 --all

  # 小红书：仅列出不下载
  python cli.py https://www.xiaohongshu.com/user/profile/xxx --list-only

  # 在无头浏览器中读取（仅适用于无需人工验证的情况）
  python cli.py https://www.douyin.com/user/MS4w... --headless --list-only
        """,
    )
    parser.add_argument("url", help="博主主页链接（支持抖音、TikTok、B站、小红书）")
    parser.add_argument("--cookie", default="", help="可选的 Cookie（对应平台域名），B站需包含 SESSDATA、bili_jct、DedeUserID")
    parser.add_argument("--max", type=int, default=0, help="最多读取数量（0=全部公开作品）")
    parser.add_argument("--output", default="downloads", help="下载保存目录")
    parser.add_argument("--list-only", action="store_true", help="仅读取并列出作品")
    parser.add_argument("--all", action="store_true", help="读取完成后下载全部读取到的作品")
    parser.add_argument("--auto", action="store_true", help="兼容旧版：等同于 --all")
    parser.add_argument("--headless", action="store_true", help="使用无头浏览器（不便于验证码处理）")
    parser.add_argument("--no-headless", action="store_true", help="兼容旧版：强制显示浏览器")
    parser.add_argument("--no-dedup", action="store_true", help="保留兼容参数（下载始终默认去重）")
    parser.add_argument("--ytdlp", action="store_true", help="B站下载优先使用 yt-dlp（支持合集/番剧/字幕/更高画质，需安装: pip install yt-dlp）")
    args = parser.parse_args()

    if args.max < 0:
        parser.error("--max 不能小于 0")

    headless = args.headless and not args.no_headless
    # 默认复用 config/browser_profile 中的浏览器会话；Cookie 仅在显式传入时使用。
    cookie = args.cookie
    auto_download = (args.all or args.auto) and not args.list_only
    pipeline = AutoPipeline(download_dir=args.output, headless=headless)
    
    # yt-dlp 选项传递给下载器
    use_ytdlp = args.ytdlp

    # 可见浏览器模式：等待登录期间按 Enter 手动继续，Ctrl+C 取消。
    continue_event: Event | None = None
    if not headless:
        continue_event = Event()

        def wait_for_enter() -> None:
            try:
                input("提示：如需登录/验证，请在浏览器窗口完成操作；完成后会自动继续，也可按 Enter 立即重试。\n")
            except EOFError:
                return
            continue_event.set()

        threading.Thread(target=wait_for_enter, daemon=True).start()

    def status(message: str) -> None:
        print(f"  {message}")

    def progress(current: int, total: int, message: str) -> None:
        if total > 0:
            print(f"\r  [{current}/{total}] {message}", end="", flush=True)

    try:
        result = pipeline.run(
            args.url,
            max_posts=args.max,
            auto_download=auto_download,
            cookie=cookie,
            deduplicate=not args.no_dedup,
            status_callback=status,
            progress_callback=progress,
            continue_event=continue_event,
            prefer_ytdlp=use_ytdlp,
        )
    except KeyboardInterrupt:
        print("\n已取消读取。")
        raise SystemExit(130)
    print()

    if not result["success"]:
        print(f"失败：{result.get('error', '未知错误')}")
        raise SystemExit(1)

    posts = result.get("posts", [])
    user = result.get("user_info", {})
    print(f"博主：{user.get('nickname', '未知')}，读取到 {len(posts)} 个作品")

    if not auto_download:
        print("-" * 80)
        print(f"{'序号':<5} {'类型':<6} {'发布时间':<20} 标题")
        print("-" * 80)
        for index, post in enumerate(posts, start=1):
            print(
                f"{index:<5} {post.get('type_str', '作品'):<6} "
                f"{post.get('create_time_str', ''):<20} {post.get('desc', '')[:45]}"
            )
        print("-" * 80)
        print("提示：添加 --all 可下载本次读取到的全部作品。")
        return

    download = result.get("download_result", {})
    print(
        "下载完成："
        f"成功 {download.get('success', 0)}，"
        f"失败 {download.get('failed', 0)}，"
        f"跳过 {download.get('skipped', 0)}"
    )


if __name__ == "__main__":
    main()

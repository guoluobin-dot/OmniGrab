#!/usr/bin/env python3
"""
抖音内容下载工具 - 命令行界面
================================
支持全自动模式（自动获取 Cookie）和手动模式。

使用方法:
    # 全自动模式（推荐）
    python cli.py <博主主页链接> --auto

    # 全自动 + 限制下载数量
    python cli.py <博主主页链接> --auto --max 10

    # 手动模式（提供 Cookie）
    python cli.py <博主主页链接> --cookie "ttwid=xxx;msToken=xxx" --all

    # 仅列出作品不下载
    python cli.py <博主主页链接> --auto --list-only
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.douyin_api import DouyinAPI
from src.core.downloader import Downloader
from src.core.auto_pipeline import AutoPipeline
from src.utils.logger import get_logger
from src.utils.cookie_helper import load_cookie

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="抖音内容下载工具 - 命令行模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 全自动模式（自动获取 Cookie）
  python cli.py https://www.douyin.com/user/MS4w... --auto

  # 全自动 + 限制下载数量
  python cli.py https://www.douyin.com/user/MS4w... --auto --max 10

  # 手动模式
  python cli.py https://www.douyin.com/user/MS4w... --cookie "ttwid=xxx" --all

  # 仅列出作品
  python cli.py https://www.douyin.com/user/MS4w... --auto --list-only
        """,
    )

    parser.add_argument("url", help="抖音博主主页链接")
    parser.add_argument("--auto", action="store_true", help="全自动模式（自动获取 Cookie）")
    parser.add_argument("--cookie", type=str, default="", help="抖音网页版 Cookie（手动模式）")
    parser.add_argument("--all", action="store_true", help="下载全部作品")
    parser.add_argument("--max", type=int, default=0, help="最大下载数量（0=全部）")
    parser.add_argument("--output", type=str, default="downloads", help="下载保存目录")
    parser.add_argument("--list-only", action="store_true", help="仅列出作品，不下载")
    parser.add_argument("--headless", action="store_true", default=True, help="浏览器无头模式")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--no-dedup", action="store_true", help="禁用去重")

    args = parser.parse_args()

    # === 全自动模式 ===
    if args.auto:
        print("\n" + "=" * 50)
        print("  抖音内容下载工具 - 全自动模式")
        print("=" * 50 + "\n")

        headless = not args.no_headless

        pipeline = AutoPipeline(
            download_dir=args.output,
            headless=headless,
        )

        def status_cb(msg):
            print(f"  {msg}")

        def progress_cb(current, total, msg):
            if total > 0:
                bar_len = 30
                filled = int(bar_len * current / total)
                bar = "█" * filled + "░" * (bar_len - filled)
                print(f"\r  [{bar}] {current}/{total} {msg}", end="", flush=True)

        auto_download = not args.list_only

        result = pipeline.run(
            profile_url=args.url,
            max_posts=args.max,
            auto_download=auto_download,
            progress_callback=progress_cb,
            status_callback=status_cb,
        )

        print()

        if result["success"]:
            if result.get("download_result"):
                dr = result["download_result"]
                print(f"\n  ✅ 下载完成！")
                print(f"     成功: {dr['success']}")
                print(f"     失败: {dr['failed']}")
                print(f"     跳过: {dr['skipped']}")
                print(f"     总计: {dr['total']}")
            else:
                print(f"\n  ✅ 获取完成，共 {len(result.get('posts', []))} 个作品")
        else:
            print(f"\n  ❌ 失败: {result.get('error', '未知错误')}")

        return

    # === 手动模式 ===
    cookie = args.cookie
    if not cookie:
        cookie = load_cookie() or ""

    if not cookie:
        logger.warning("未提供 Cookie，使用 --auto 启用全自动模式，或 --cookie 提供 Cookie")
        sys.exit(1)

    api = DouyinAPI(cookie=cookie)

    logger.info(f"正在解析链接: {args.url}")
    sec_uid = DouyinAPI.extract_sec_uid(args.url)
    if not sec_uid:
        logger.error("无法从链接中提取博主信息")
        sys.exit(1)

    logger.info("正在获取博主信息...")
    user_info = api.get_user_info(sec_uid)
    if user_info:
        print("\n" + "=" * 50)
        print(f"  博主昵称: {user_info['nickname']}")
        print(f"  作品数量: {user_info['aweme_count']}")
        print(f"  粉丝数量: {user_info['follower_count']}")
        print("=" * 50 + "\n")
    else:
        logger.error("获取博主信息失败")
        sys.exit(1)

    max_count = args.max if args.max > 0 else 0

    def fetch_callback(count, total, has_more):
        print(f"\r  已获取 {count} 个作品...", end="", flush=True)

    logger.info("正在获取作品列表...")
    posts = api.get_user_posts(sec_uid, max_count=max_count, callback=fetch_callback)
    print(f"\n  共获取到 {len(posts)} 个作品\n")

    if not posts:
        logger.warning("未获取到任何作品")
        sys.exit(0)

    print("-" * 80)
    print(f"{'序号':<5} {'类型':<6} {'发布时间':<20} {'标题'}")
    print("-" * 80)
    for i, post in enumerate(posts):
        print(f"{i+1:<5} {post['type_str']:<6} {post['create_time_str']:<20} {post['desc'][:40]}")
    print("-" * 80)

    if args.list_only:
        logger.info("仅列出模式")
        sys.exit(0)

    if args.all or args.max > 0:
        logger.info(f"开始下载到目录: {args.output}")
        downloader = Downloader(args.output)

        if args.no_dedup:
            downloader.db = None

        def download_progress(current, total, success_count, is_success):
            symbol = "✓" if is_success else "✗"
            print(f"\r  [{current}/{total}] {symbol} 成功:{success_count}", end="", flush=True)

        result = downloader.download_batch(posts, progress_callback=download_progress)
        print(f"\n\n下载完成！成功: {result['success']}, 失败: {result['failed']}, 跳过: {result['skipped']}")
    else:
        logger.info("使用 --all 下载全部，或 --max N 下载前 N 个")


if __name__ == "__main__":
    main()

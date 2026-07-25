#!/usr/bin/env python3
"""
抖音内容下载工具 - 命令行界面
================================
提供无 GUI 的命令行操作方式，适合服务器环境或批量脚本。

使用方法:
    python cli.py <博主主页链接> [选项]

示例:
    python cli.py https://www.douyin.com/user/MS4w... --all
    python cli.py https://www.douyin.com/user/MS4w... --cookie "ttwid=xxx;msToken=xxx"
    python cli.py https://www.douyin.com/user/MS4w... --max 10 --output ./downloads
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.douyin_api import DouyinAPI
from src.core.downloader import Downloader
from src.utils.logger import get_logger
from src.utils.cookie_helper import load_cookie

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="抖音内容下载工具 - 命令行模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python cli.py https://www.douyin.com/user/MS4w... --all
  python cli.py https://www.douyin.com/user/MS4w... --max 10
  python cli.py https://www.douyin.com/user/MS4w... --cookie "ttwid=xxx" --all
        """,
    )

    parser.add_argument("url", help="抖音博主主页链接")
    parser.add_argument("--cookie", type=str, default="", help="抖音网页版 Cookie 字符串")
    parser.add_argument("--all", action="store_true", help="下载全部作品")
    parser.add_argument("--max", type=int, default=0, help="最大下载数量（0=全部）")
    parser.add_argument("--output", type=str, default="downloads", help="下载保存目录")
    parser.add_argument("--list-only", action="store_true", help="仅列出作品，不下载")
    parser.add_argument("--no-dedup", action="store_true", help="禁用去重（重复也下载）")

    args = parser.parse_args()

    # 获取 Cookie
    cookie = args.cookie
    if not cookie:
        cookie = load_cookie() or ""

    if not cookie:
        logger.warning("未提供 Cookie，可能无法获取作品数据")
        logger.warning("请通过 --cookie 参数或保存 Cookie 到配置文件")

    # 初始化 API
    api = DouyinAPI(cookie=cookie)

    # 提取 sec_uid
    logger.info(f"正在解析链接: {args.url}")
    sec_uid = DouyinAPI.extract_sec_uid(args.url)
    if not sec_uid:
        logger.error("无法从链接中提取博主信息，请检查链接")
        sys.exit(1)

    # 获取用户信息
    logger.info("正在获取博主信息...")
    user_info = api.get_user_info(sec_uid)
    if user_info:
        print("\n" + "=" * 50)
        print(f"  博主昵称: {user_info['nickname']}")
        print(f"  作品数量: {user_info['aweme_count']}")
        print(f"  粉丝数量: {user_info['follower_count']}")
        print(f"  关注数量: {user_info['following_count']}")
        if user_info["signature"]:
            print(f"  简介: {user_info['signature']}")
        print("=" * 50 + "\n")
    else:
        logger.error("获取博主信息失败")
        sys.exit(1)

    # 获取作品列表
    max_count = args.max if args.max > 0 else 0

    def fetch_callback(count, total, has_more):
        print(f"\r  已获取 {count} 个作品...", end="", flush=True)

    logger.info("正在获取作品列表...")
    posts = api.get_user_posts(sec_uid, max_count=max_count, callback=fetch_callback)
    print(f"\n  共获取到 {len(posts)} 个作品\n")

    if not posts:
        logger.warning("未获取到任何作品")
        sys.exit(0)

    # 显示作品列表
    print("-" * 80)
    print(f"{'序号':<5} {'类型':<6} {'发布时间':<20} {'标题'}")
    print("-" * 80)
    for i, post in enumerate(posts):
        print(f"{i+1:<5} {post['type_str']:<6} {post['create_time_str']:<20} {post['desc'][:40]}")
    print("-" * 80)

    if args.list_only:
        logger.info("仅列出模式，不下载")
        sys.exit(0)

    # 下载
    if args.all or args.max > 0:
        logger.info(f"开始下载到目录: {args.output}")

        downloader = Downloader(args.output)

        if args.no_dedup:
            # 临时禁用去重
            downloader.db = None

        def download_progress(current, total, success_count, is_success):
            symbol = "✓" if is_success else "✗"
            print(f"\r  [{current}/{total}] {symbol} 成功:{success_count}", end="", flush=True)

        result = downloader.download_batch(posts, progress_callback=download_progress)

        print(f"\n\n下载完成！")
        print(f"  成功: {result['success']}")
        print(f"  失败: {result['failed']}")
        print(f"  跳过: {result['skipped']}")
        print(f"  总计: {result['total']}")
    else:
        logger.info("未指定 --all 参数，使用 --all 下载全部，或 --max N 下载前 N 个")


if __name__ == "__main__":
    main()

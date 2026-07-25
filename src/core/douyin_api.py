"""
抖音 API 核心模块
================
负责与抖音 Web API 交互，解析用户主页链接、获取作品列表、提取无水印下载链接。

核心流程:
1. 用户输入博主主页链接 -> 提取 sec_uid
2. 使用 sec_uid 调用抖音 API 获取作品列表（分页）
3. 解析每个作品的详细信息（类型、标题、发布时间、下载链接）
4. 提取无水印视频/图片下载地址
"""

import re
import time
import json
import random
import requests
from typing import Optional
from urllib.parse import quote

from src.utils.logger import get_logger

logger = get_logger(__name__)


class DouyinAPI:
    """抖音 Web API 交互核心类"""

    # 抖音主页链接中提取 sec_uid 的正则
    SEC_UID_PATTERN = re.compile(r'sec_uid=([A-Za-z0-9_\-=]+)')

    # 抖音短链接解析后重定向的长链接中也可能包含 sec_uid
    SHORT_URL_PATTERN = re.compile(r'v\d+\.douyin\.com')

    # 用户作品列表 API（aweme post 即用户发布的作品）
    USER_POST_API = "https://www.douyin.com/aweme/v1/web/aweme/post/"

    # 用户详细信息 API
    USER_DETAIL_API = "https://www.douyin.com/aweme/v1/web/user/profile/other/"

    # 默认请求头
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    def __init__(self, cookie: str = "", proxy: Optional[dict] = None):
        """
        初始化抖音 API 客户端

        Args:
            cookie: 抖音网页版 cookie 字符串，用于认证
            proxy: 代理设置，如 {"https": "http://127.0.0.1:7890"}
        """
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        if cookie:
            self.session.headers["Cookie"] = cookie
        if proxy:
            self.session.proxies.update(proxy)

        # 从 cookie 中提取 ttwid 和 msToken（关键认证参数）
        self._extract_cookie_tokens()

    def _extract_cookie_tokens(self):
        """从 cookie 中提取关键 token"""
        cookie_str = self.session.headers.get("Cookie", "")
        self.ttwid = ""
        self.ms_token = ""

        # 提取 ttwid
        ttwid_match = re.search(r'ttwid=([^;]+)', cookie_str)
        if ttwid_match:
            self.ttwid = ttwid_match.group(1)

        # 提取 msToken
        mstoken_match = re.search(r'msToken=([^;]+)', cookie_str)
        if mstoken_match:
            self.ms_token = mstoken_match.group(1)

    @staticmethod
    def extract_sec_uid(url: str) -> Optional[str]:
        """
        从抖音主页链接中提取 sec_uid

        支持的链接格式:
        - https://www.douyin.com/user/MS4wLjABAAAA...  (sec_uid 在 URL 中)
        - https://v.douyin.com/xxxxx/  (短链接，需要重定向解析)

        Args:
            url: 抖音博主主页链接

        Returns:
            sec_uid 字符串，提取失败返回 None
        """
        logger.info(f"正在解析链接: {url}")

        # 尝试直接从 URL 中提取 sec_uid
        match = DouyinAPI.SEC_UID_PATTERN.search(url)
        if match:
            sec_uid = match.group(1)
            logger.info(f"直接提取 sec_uid 成功: {sec_uid[:20]}...")
            return sec_uid

        # 如果是短链接，尝试跟随重定向
        if DouyinAPI.SHORT_URL_PATTERN.search(url):
            logger.info("检测到短链接，正在解析重定向...")
            try:
                resp = requests.get(
                    url,
                    headers=DouyinAPI.DEFAULT_HEADERS,
                    allow_redirects=True,
                    timeout=10,
                )
                final_url = resp.url
                match = DouyinAPI.SEC_UID_PATTERN.search(final_url)
                if match:
                    sec_uid = match.group(1)
                    logger.info(f"短链接解析 sec_uid 成功: {sec_uid[:20]}...")
                    return sec_uid
            except Exception as e:
                logger.error(f"短链接解析失败: {e}")

        # 尝试从 URL 路径中提取（某些格式 sec_uid 直接在路径中）
        # 格式: https://www.douyin.com/user/MS4wLjABAAAA...
        path_match = re.search(r'/user/([A-Za-z0-9_\-=]+)', url)
        if path_match:
            sec_uid = path_match.group(1)
            logger.info(f"从路径提取 sec_uid: {sec_uid[:20]}...")
            return sec_uid

        logger.error("无法从链接中提取 sec_uid")
        return None

    def get_user_info(self, sec_uid: str) -> Optional[dict]:
        """
        获取博主用户信息

        Args:
            sec_uid: 用户安全 ID

        Returns:
            用户信息字典，包含昵称、简介、粉丝数等
        """
        params = {
            "sec_user_id": sec_uid,
            "device_platform": "webapp",
            "aid": "6383",
        }

        try:
            resp = self.session.get(
                self.USER_DETAIL_API,
                params=params,
                timeout=15,
            )
            data = resp.json()

            if data.get("status_code") == 0:
                user = data.get("user", {})
                return {
                    "nickname": user.get("nickname", "未知"),
                    "sec_uid": sec_uid,
                    "uid": user.get("uid", ""),
                    "follower_count": user.get("follower_count", 0),
                    "following_count": user.get("following_count", 0),
                    "aweme_count": user.get("aweme_count", 0),
                    "favoriting_count": user.get("favoriting_count", 0),
                    "signature": user.get("signature", ""),
                    "avatar": user.get("avatar_thumb", {}).get("url_list", [""])[0]
                    if user.get("avatar_thumb")
                    else "",
                }
            else:
                logger.error(f"获取用户信息失败: {data.get('status_msg', '未知错误')}")
                return None

        except Exception as e:
            logger.error(f"获取用户信息异常: {e}")
            return None

    def get_user_posts(
        self,
        sec_uid: str,
        max_count: int = 0,
        callback=None,
    ) -> list:
        """
        获取博主的所有作品列表（分页获取）

        Args:
            sec_uid: 用户安全 ID
            max_count: 最大获取数量，0 表示获取全部
            callback: 回调函数，每获取一页数据后调用 callback(count, total)

        Returns:
            作品列表，每个元素是一个作品信息字典
        """
        all_posts = []
        max_cursor = 0
        page = 1

        while True:
            logger.info(f"正在获取第 {page} 页作品...")

            params = {
                "sec_user_id": sec_uid,
                "count": 20,
                "max_cursor": max_cursor,
                "device_platform": "webapp",
                "aid": "6383",
                "version_code": "170400",
                "version_name": "17.4.0",
            }

            # 如果有 msToken，添加到参数中
            if self.ms_token:
                params["msToken"] = self.ms_token

            try:
                resp = self.session.get(
                    self.USER_POST_API,
                    params=params,
                    timeout=15,
                )
                data = resp.json()

                # 检查是否有作品数据
                aweme_list = data.get("aweme_list", [])

                if not aweme_list:
                    logger.info("没有更多作品了")
                    break

                # 解析每个作品
                for aweme in aweme_list:
                    post_info = self._parse_aweme(aweme)
                    if post_info:
                        all_posts.append(post_info)

                logger.info(f"第 {page} 页获取到 {len(aweme_list)} 个作品，累计 {len(all_posts)} 个")

                # 回调通知进度
                if callback:
                    has_more = data.get("has_more", 0)
                    total = data.get("total", 0) or len(all_posts)
                    callback(len(all_posts), total, has_more == 1)

                # 检查是否还有更多
                has_more = data.get("has_more", 0)
                if has_more != 1:
                    logger.info("已获取全部作品")
                    break

                # 更新游标
                max_cursor = data.get("max_cursor", 0)
                if max_cursor == 0:
                    logger.info("游标为 0，结束获取")
                    break

                # 检查最大数量限制
                if max_count > 0 and len(all_posts) >= max_count:
                    logger.info(f"已达到最大数量限制 {max_count}")
                    all_posts = all_posts[:max_count]
                    break

                page += 1
                # 随机延迟，避免请求过快
                time.sleep(random.uniform(0.5, 1.5))

            except requests.exceptions.JSONDecodeError:
                logger.error("返回数据不是 JSON 格式，可能需要更新 cookie 或被限流")
                break
            except Exception as e:
                logger.error(f"获取作品列表异常: {e}")
                break

        return all_posts

    @staticmethod
    def _parse_aweme(aweme: dict) -> Optional[dict]:
        """
        解析单个作品信息

        Args:
            aweme: API 返回的原始作品数据

        Returns:
            标准化的作品信息字典
        """
        try:
            # 基本信息
            aweme_id = aweme.get("aweme_id", "")
            desc = aweme.get("desc", "无标题")
            create_time = aweme.get("create_time", 0)

            # 判断作品类型：视频 or 图集
            images = aweme.get("images", [])
            is_image_set = len(images) > 0

            post_info = {
                "aweme_id": aweme_id,
                "desc": desc,
                "create_time": create_time,
                "create_time_str": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(create_time)
                )
                if create_time
                else "未知",
                "type": "image" if is_image_set else "video",
                "type_str": "图文" if is_image_set else "视频",
                "duration": 0,
                "cover": "",
                "video_url": "",
                "image_urls": [],
                "music_url": "",
                "stats": {
                    "digg_count": aweme.get("statistics", {}).get("digg_count", 0),
                    "comment_count": aweme.get("statistics", {}).get("comment_count", 0),
                    "share_count": aweme.get("statistics", {}).get("share_count", 0),
                    "play_count": aweme.get("statistics", {}).get("play_count", 0),
                },
            }

            # 提取视频信息
            if not is_image_set:
                video = aweme.get("video", {})
                play_addr = video.get("play_addr", {})
                post_info["video_url"] = (
                    play_addr.get("url_list", [""])[0] if play_addr.get("url_list") else ""
                )
                post_info["duration"] = video.get("duration", 0)
                cover = video.get("cover", {})
                post_info["cover"] = (
                    cover.get("url_list", [""])[0] if cover.get("url_list") else ""
                )

            # 提取图集信息
            if is_image_set:
                for img in images:
                    url_list = img.get("url_list", [])
                    if url_list:
                        post_info["image_urls"].append(url_list[-1])  # 取最高质量

            # 提取音乐信息
            music = aweme.get("music", {})
            play_url = music.get("play_url", {})
            if play_url.get("url_list"):
                post_info["music_url"] = play_url["url_list"][0]

            return post_info

        except Exception as e:
            logger.error(f"解析作品信息失败: {e}")
            return None

    @staticmethod
    def get_no_watermark_url(video_url: str) -> str:
        """
        获取无水印视频链接

        抖音的视频链接中，将 playwm 替换为 play 即可获取无水印版本

        Args:
            video_url: 带水印的视频链接

        Returns:
            无水印视频链接
        """
        if not video_url:
            return ""
        # 替换水印标识
        no_wm = video_url.replace("playwm", "play")
        return no_wm

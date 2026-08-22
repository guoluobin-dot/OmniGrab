from __future__ import annotations

from pydantic import BaseModel, Field


class Author(BaseModel):
    nickname: str = ""
    sec_uid: str = ""
    uid: str = ""


class Stats(BaseModel):
    digg_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    play_count: int = 0


class PostItem(BaseModel):
    aweme_id: str = ""
    desc: str = "无标题"
    create_time: int = 0
    create_time_str: str = "未知"
    type: str = "video"
    type_str: str = "视频"
    duration: int = 0
    cover: str = ""
    video_url: str = ""
    image_urls: list[str] = Field(default_factory=list)
    music_url: str = ""
    web_url: str = ""
    author: Author = Field(default_factory=Author)
    stats: Stats = Field(default_factory=Stats)


class NoteItem(PostItem):
    type: str = "image"
    type_str: str = "图文"

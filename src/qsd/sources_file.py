"""`sources.txt`: a hand-made list of things to read, one per line (idea adapted from the user's Idea Extractor).

    # comments and blank lines are ignored
    https://www.youtube.com/watch?v=XXXXXXXXXXX           a YouTube video (title + description + linked papers)
    https://www.youtube.com/@SomeChannel | n:25           a channel: its 25 newest videos not read before
    https://arxiv.org/abs/2101.00001                      a paper / web page / PDF link
    D:\\Books\\some_book.epub | My Custom Title            a local file, with a title override
    D:\\Papers                                            a folder: every supported file inside (sub-folders too)

Anything after `|` is an option: `n:25` (channel: how many new videos) or free text (title override).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

YOUTUBE_VIDEO = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})")
YOUTUBE_CHANNEL = re.compile(r"youtube\.com/(@[\w.\-]+|channel/UC[\w\-]{22}|c/[\w.\-]+|user/[\w.\-]+)", re.I)
DEFAULT_CHANNEL_VIDEOS = 10
MAX_CHANNEL_VIDEOS = 200


@dataclass
class SourceItem:
    line: int
    kind: str          # youtube_video | youtube_channel | url | path
    target: str        # video id, channel spec (@handle / channel/UC.. / c/.. / user/..), URL or path
    title: str | None = None
    n: int | None = None


def parse_sources_text(text: str) -> tuple[list[SourceItem], list[str]]:
    items: list[SourceItem] = []
    problems: list[str] = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        main, *opts = [p.strip() for p in line.split("|")]
        title, n = None, None
        for opt in opts:
            m = re.fullmatch(r"n\s*:\s*(\d+)", opt, re.I)
            if m:
                n = max(1, min(MAX_CHANNEL_VIDEOS, int(m.group(1))))
            elif opt:
                title = opt[:300]
        main = main.strip().strip('"')
        if not main:
            problems.append(f"line {no}: empty source")
            continue
        if re.match(r"https?://", main, re.I):
            if m := YOUTUBE_VIDEO.search(main):
                items.append(SourceItem(no, "youtube_video", m.group(1), title))
            elif m := YOUTUBE_CHANNEL.search(main):
                items.append(SourceItem(no, "youtube_channel", m.group(1), title, n or DEFAULT_CHANNEL_VIDEOS))
            else:
                items.append(SourceItem(no, "url", main, title))
        elif re.match(r"^[a-z][a-z0-9+.-]*://", main, re.I):
            problems.append(f"line {no}: only http(s) links are supported: {main[:80]}")
        else:
            items.append(SourceItem(no, "path", main, title))
    return items, problems


def read_sources_file(path: Path) -> tuple[list[SourceItem], list[str]]:
    if not path.exists():
        return [], [f"{path} not found"]
    return parse_sources_text(path.read_text(encoding="utf-8-sig", errors="replace"))

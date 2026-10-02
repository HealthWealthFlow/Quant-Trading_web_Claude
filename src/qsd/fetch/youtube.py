"""YouTube transcript retrieval (spec §86 stage B input).

Until now a video was read through its title and description only. That is why the user's campaign 3 read 150 video
descriptions and produced **zero** strategies: descriptions are schedules, links and marketing, while the strategy
itself is spoken. This module retrieves the spoken content as text, with timestamps, so the ordinary extraction and
grounding pipeline can read a video like a paper.

Compliance (user decision, DECISIONS D29):
- **Subtitles only.** Media is never downloaded: the yt-dlp invocation passes `--skip-download` and writes only
  caption files.
- The whole feature sits behind `discovery.youtube_transcripts`; when it is off, or yt-dlp is absent, the code path
  is exactly the previous metadata-only behaviour.
- Transcripts are untrusted text like any other fetched content and are wrapped before reaching a model.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

CAPTION_FACT = "TRANSCRIPT"

_TIMESTAMP = re.compile(r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{3})")
_TAGS = re.compile(r"<[^>]+>")
_CUE_SETTINGS = re.compile(r"\s+(?:align|position|line|size|region|vertical):[^\s]+")
_LANG_TOKEN = re.compile(r"\.([a-z]{2}(?:-[A-Za-z]{2,4})?)\.", re.I)


class TranscriptError(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    language: str
    source: str  # "auto" or "manual"


def yt_dlp_path() -> str | None:
    """The yt-dlp executable, if this machine has one. Absence is not an error, just no transcripts."""
    return shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")


def _seconds(match: re.Match) -> int:
    hours = int(match.group(1) or 0)
    return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3))


def _clean_line(line: str) -> str:
    line = _TAGS.sub("", line)
    line = _CUE_SETTINGS.sub("", line)
    return (line.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&#39;", "'").strip())


def vtt_to_text(raw: str) -> str:
    """VTT/SRT → `[t=HH:MM:SS] sentence` lines, de-duplicated.

    YouTube's auto-captions emit rolling windows: the previous cue's tail reappears at the start of the next cue.
    A plain line-by-line comparison does not catch that (the cue text has more than the repeat), so the repeated
    tail is stripped before a cue is kept — otherwise the same sentence is stored, read and paid for several times.
    """
    out: list[str] = []
    previous = ""
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        stamp = next((ln for ln in lines if "-->" in ln), None)
        if stamp is None:
            continue  # header, NOTE block, or a bare cue identifier
        match = _TIMESTAMP.search(stamp)
        if match is None:
            continue
        text = " ".join(_clean_line(ln) for ln in lines if "-->" not in ln and not ln.strip().isdigit())
        text = re.sub(r"\s+", " ", text).strip()
        if text and previous and text.startswith(previous):
            text = text[len(previous):].strip()  # drop the repeated tail of the previous cue
        if not text or text == previous:
            continue
        previous = text
        second = _seconds(match)
        hours, remainder = divmod(second, 3600)
        minutes, secs = divmod(remainder, 60)
        out.append(f"[t={hours:02d}:{minutes:02d}:{secs:02d}] {text}")
    return "\n".join(out)


def _sentence_stream(lines: list[str], max_chars: int) -> str:
    """Re-flow timestamped caption lines into a few large, timestamp-anchored blocks.

    Captions break every 2-6 seconds, so a 20-minute video arrives as several hundred very short blocks. Scoring and
    windowing happen per block, so that shape is pathological: a character budget spent on tiny blocks can run out
    before the middle of the video, where the actual rules usually are. Grouping them into ~1200-character paragraphs
    keeps every timestamp in place (grounding still validates against the real text) while making the block shape
    resemble a document.
    """
    if not lines:
        return ""
    out: list[str] = []
    buffer: list[str] = []
    size = 0
    for line in lines:
        buffer.append(line)
        size += len(line) + 1
        if size >= max_chars:
            out.append("\n".join(buffer))
            buffer, size = [], 0
    if buffer:
        out.append("\n".join(buffer))
    return "\n\n".join(out)


def fetch_transcript(video_id: str, languages: list[str] | None = None, timeout: int = 120,
                     base_dir: str | Path | None = None, reflow_chars: int = 1200) -> Transcript | None:
    """Download captions for `video_id` and return them as text; None when the video has none.

    `base_dir` only decides where yt-dlp writes the temporary caption file; it is useful when the system temp
    directory is not writable. Nothing but caption text is ever written, and the directory is removed afterwards.
    """
    exe = yt_dlp_path()
    if not exe:
        return None
    langs = languages or ["en", "en-US", "en-GB"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    if base_dir is not None:
        Path(base_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qsd-subs-", dir=str(base_dir) if base_dir else None) as tmp:
        outtmpl = str(Path(tmp) / "%(id)s.%(ext)s")
        cmd = [exe, "--skip-download", "--no-playlist", "--no-warnings", "--quiet",
               "--write-subs", "--write-auto-subs", "--sub-langs", ",".join(langs), "--sub-format", "vtt",
               "-o", outtmpl, url]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise TranscriptError(f"yt-dlp failed for {video_id}: {type(e).__name__}") from None
        captions = sorted(Path(tmp).glob("*.vtt"))
        if not captions:
            if proc.returncode != 0:
                raise TranscriptError(f"yt-dlp exited {proc.returncode} for {video_id}: "
                                      f"{(proc.stderr or '').strip()[-200:]}")
            return None
        chosen = captions[0]
        raw = chosen.read_text(encoding="utf-8", errors="replace")
        filename = chosen.name
    parsed = vtt_to_text(raw)
    if not parsed.strip():
        return None
    # yt-dlp names the original track `<id>.<lang>-orig.vtt`: `-orig` marks it as auto-generated, so it counts as
    # automatic too. Without a language token there is nothing to trust either way, so it is treated as automatic.
    lang = _LANG_TOKEN.search(filename) or re.search(r"\.([a-z]{2,3})[-.]", filename, re.I)
    auto = lang is None or any(token in filename.lower() for token in (".auto.", "-auto-", "-orig."))
    return Transcript(text=_sentence_stream(parsed.splitlines(), reflow_chars),
                      language=lang.group(1) if lang else "unknown",
                      source="auto" if auto else "manual")

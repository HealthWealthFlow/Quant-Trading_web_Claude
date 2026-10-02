"""YouTube transcripts: a video's spoken content is what a strategy actually lives in (DECISIONS D29)."""

from __future__ import annotations

import subprocess

import pytest

from qsd.fetch import youtube as yt

SAMPLE_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:03.000 align:start position:0%
hello and welcome back

00:00:03.000 --> 00:00:06.000 align:start position:0%
hello and welcome back
today I want to show you a strategy

00:00:06.000 --> 00:00:09.000
today I want to show you a strategy
we buy when price closes above the 20 day high
"""


def test_vtt_becomes_timestamped_lines_without_repeats():
    text = yt.vtt_to_text(SAMPLE_VTT)
    lines = text.split("\n")
    assert lines[0] == "[t=00:00:00] hello and welcome back"
    # the rolling caption repeats the previous line; it must not be stored twice
    assert sum("today I want to show you a strategy" in ln for ln in lines) == 1
    assert lines[-1] == "[t=00:00:06] we buy when price closes above the 20 day high"
    assert "WEBVTT" not in text and "-->" not in text


def test_vtt_strips_inline_markup_and_cue_settings():
    raw = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000 position:0%\n"
           "<c.colorE5E5E5>buy</c> when <00:00:01.500><c>RSI</c> is below 30\n")
    assert yt.vtt_to_text(raw) == "[t=00:00:01] buy when RSI is below 30"


def test_srt_style_timestamps_are_accepted():
    raw = "1\n00:00:10,500 --> 00:00:12,000\nenter on a breakout\n"
    assert yt.vtt_to_text(raw) == "[t=00:00:10] enter on a breakout"


# ---- re-flow into document-shaped blocks --------------------------------------------------------------
# Measured on a real 20-minute video: captions arrive as 413 tiny blocks, and a character-budgeted window over tiny
# blocks can run out before the middle of the video, where the rules usually are. Grouping must lose nothing.

def test_reflow_groups_caption_lines_without_losing_any_text():
    lines = [f"[t=00:{i // 60:02d}:{i % 60:02d}] caption number {i}" for i in range(200)]
    grouped = yt._sentence_stream(lines, max_chars=1200)
    blocks = grouped.split("\n\n")
    assert len(blocks) > 1 and len(blocks) < len(lines)  # actually grouped
    assert all(len(b) <= 1800 for b in blocks)          # no runaway block
    # every caption still present, in order, and every block starts on a timestamp
    assert [ln for b in blocks for ln in b.splitlines()] == lines
    assert all(b.startswith("[t=") for b in blocks)


def test_reflow_keeps_short_transcripts_in_one_block():
    assert "\n\n" not in yt._sentence_stream(["[t=00:00:01] buy", "[t=00:00:02] sell"], max_chars=1200)


def test_captions_are_never_downloaded_with_media(monkeypatch, tmp_path):
    """Compliance: the yt-dlp call must skip the media and write captions only."""
    seen: dict = {}
    subs = tmp_path / "abc123.en.vtt"
    subs.write_text(SAMPLE_VTT, encoding="utf-8")

    class FakeTmp:
        def __enter__(self):
            return str(tmp_path)

        def __exit__(self, *a):
            return False

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(yt, "yt_dlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(yt.tempfile, "TemporaryDirectory", lambda **k: FakeTmp())
    monkeypatch.setattr(yt.subprocess, "run", fake_run)

    got = yt.fetch_transcript("abc123")
    assert got is not None and "20 day high" in got.text
    cmd = seen["cmd"]
    assert "--skip-download" in cmd            # no media is ever fetched
    assert "--write-auto-subs" in cmd and "--sub-format" in cmd and "vtt" in cmd
    assert "https://www.youtube.com/watch?v=abc123" in cmd
    assert not any(flag in cmd for flag in ("-f", "--format", "-x", "--extract-audio"))


def test_missing_yt_dlp_is_not_an_error(monkeypatch):
    monkeypatch.setattr(yt, "yt_dlp_path", lambda: None)
    assert yt.fetch_transcript("abc123") is None


def test_video_without_captions_returns_none(monkeypatch, tmp_path):
    class FakeTmp:
        def __enter__(self):
            return str(tmp_path)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(yt, "yt_dlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(yt.tempfile, "TemporaryDirectory", lambda **k: FakeTmp())
    monkeypatch.setattr(yt.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert yt.fetch_transcript("nocaptions") is None


def test_yt_dlp_failure_raises_a_typed_error(monkeypatch, tmp_path):
    class FakeTmp:
        def __enter__(self):
            return str(tmp_path)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(yt, "yt_dlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(yt.tempfile, "TemporaryDirectory", lambda **k: FakeTmp())
    monkeypatch.setattr(yt.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "ERROR: unavailable"))
    with pytest.raises(yt.TranscriptError):
        yt.fetch_transcript("broken")


def test_transcripts_are_off_by_default():
    """Default behaviour must stay metadata-only until the operator opts in."""
    from qsd.config import DEFAULT_CONFIG, load_settings

    settings = load_settings(DEFAULT_CONFIG, None, environ={})
    assert settings.discovery.youtube_transcripts is False
    assert settings.discovery.youtube_caption_languages == ["en", "en-US", "en-GB"]

"""Plain text and Markdown (also used for JSON/XML/source code, which are read as text, never executed)."""

from __future__ import annotations

import re

from .base import ENCODING_FALLBACK, HandlerResult, Location, SourceHandler, TextBlock

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")


def decode_text(data: bytes, result: HandlerResult) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and "\x00" in text:
                continue
            return text
        except UnicodeDecodeError:
            continue
    result.add_limitation(ENCODING_FALLBACK, "decoded as cp1252 with replacement characters")
    return data.decode("cp1252", errors="replace")


class PlainTextHandler(SourceHandler):
    name = "PlainTextHandler"
    version = "1"
    formats = ("txt", "md", "json", "xml", "code")

    def extract(self, data: bytes, fmt: str, base_url: str | None = None) -> HandlerResult:
        result = self.new_result(data, fmt)
        text = decode_text(data, result)
        if fmt == "md":
            self._markdown_blocks(text, result)
        else:
            result.blocks.append(TextBlock(text))
        result.links.extend(_URL.findall(text))
        return result.finalize()

    @staticmethod
    def _markdown_blocks(text: str, result: HandlerResult) -> None:
        section: str | None = None
        buf: list[str] = []

        def flush() -> None:
            if "".join(buf).strip():
                result.blocks.append(TextBlock("\n".join(buf).strip(), Location(section=section)))
            buf.clear()

        for line in text.splitlines():
            m = _MD_HEADING.match(line)
            if m:
                flush()
                section = m.group(2)
                if result.metadata["title"] == "UNKNOWN" and len(m.group(1)) == 1:
                    result.set_meta("title", section)
            buf.append(line)
        flush()

import json
import logging

from qsd.logging_setup import REDACTED, JsonFormatter, redact


def test_redacts_headers_and_tokens():
    samples = [
        "Authorization: Bearer abc.def.ghi",
        "Cookie: sessionid=xyz123",
        'headers={"X-API-Key": "k-999"}',
        "url?api_key=SECRET123&q=momentum",
        "csrf_token=tok42",
        "key sk-abcdefghijklmnopqrstuvwx used",
    ]
    for s in samples:
        out = redact(s)
        for secret in ("abc.def.ghi", "xyz123", "k-999", "SECRET123", "tok42", "sk-abcdefghijklmnopqrstuvwx"):
            assert secret not in out, (s, out)
        assert REDACTED in out


def test_non_secret_text_untouched():
    text = "Cross-sectional momentum, 12-1 lookback UNKNOWN, page 14"
    assert redact(text) == text


def test_formatter_emits_json_and_redacts():
    rec = logging.LogRecord("qsd", logging.INFO, __file__, 1, "Set-Cookie: sid=zzz", None, None)
    rec.stage = "fetch"
    payload = json.loads(JsonFormatter().format(rec))
    assert payload["stage"] == "fetch"
    assert "zzz" not in payload["msg"]

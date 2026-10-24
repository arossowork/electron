"""Log redaction keeps the formatting contract of every formatter (uvicorn's access log unpacks record.args)."""

import logging

from hwplatform.logredact import install_log_redaction

KEY = "sk-ant-api03-" + "Z9y8X7w6" * 4


def _make(name, msg, args):
    install_log_redaction()
    return logging.getLogRecordFactory()(name, logging.INFO, __file__, 1, msg, args, None)


def test_uvicorn_access_format_survives_and_is_redacted():
    from uvicorn.logging import AccessFormatter

    fmt = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    rec = _make(
        "uvicorn.access",
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", f"/api/v1/x?key={KEY}", "1.1", 200),
    )
    assert isinstance(rec.args, tuple) and len(rec.args) == 5
    out = fmt.format(rec)
    assert "GET /api/v1/x?key=" in out and "200" in out
    assert KEY not in out and "sk-ant-***" in out


def test_args_are_redacted_and_message_formats_normally():
    rec = _make("t", "user %s sent %d keys: %s", ("u1", 2, KEY))
    msg = logging.Formatter("%(message)s").format(rec)
    assert msg == "user u1 sent 2 keys: sk-ant-***"
    rec = _make("t", "mapping %(k)s", ({"k": KEY},))
    assert logging.Formatter("%(message)s").format(rec) == "mapping sk-ant-***"
    rec = _make("t", f"plain {KEY}", ())
    assert KEY not in logging.Formatter("%(message)s").format(rec)

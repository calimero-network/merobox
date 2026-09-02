"""stdout/stderr must be able to carry merobox's own output.

Windows defaults them to the ANSI code page, which cannot encode the emoji
merobox prints — so it crashes on its banner before doing any work. These run
on every platform by faking the stream, so the Windows case is covered on the
Linux runners that gate this repository.
"""

from unittest import mock

from merobox.cli import _use_utf8_stdio


class _Stream:
    """A stdout/stderr stand-in that records how it was reconfigured."""

    def __init__(self, encoding):
        self.encoding = encoding
        self.reconfigured = None

    def reconfigure(self, **kwargs):
        self.reconfigured = kwargs
        self.encoding = kwargs.get("encoding", self.encoding)


def test_a_cp1252_stream_is_switched_to_utf8():
    out, err = _Stream("cp1252"), _Stream("cp1252")
    with (
        mock.patch("merobox.cli.sys.stdout", out),
        mock.patch("merobox.cli.sys.stderr", err),
    ):
        _use_utf8_stdio()

    for s in (out, err):
        assert s.reconfigured == {"encoding": "utf-8", "errors": "replace"}


def test_errors_replace_so_a_stray_glyph_cannot_kill_a_workflow():
    out = _Stream("cp1252")
    with (
        mock.patch("merobox.cli.sys.stdout", out),
        mock.patch("merobox.cli.sys.stderr", _Stream("utf-8")),
    ):
        _use_utf8_stdio()

    assert out.reconfigured["errors"] == "replace"


def test_a_utf8_stream_is_left_alone():
    """Every POSIX host. Reconfiguring it would be churn, not a fix."""
    out, err = _Stream("utf-8"), _Stream("UTF-8")
    with (
        mock.patch("merobox.cli.sys.stdout", out),
        mock.patch("merobox.cli.sys.stderr", err),
    ):
        _use_utf8_stdio()

    assert out.reconfigured is None
    assert err.reconfigured is None, "UTF-8 spelled with a dash is still UTF-8"


def test_a_stream_that_cannot_be_reconfigured_is_not_fatal():
    """Captured or redirected streams have no encoding to change. Refusing to
    run because of that would be worse than the glyph."""

    class _Captured:
        encoding = "cp1252"

        def reconfigure(self, **kwargs):
            raise ValueError("cannot reconfigure")

    with (
        mock.patch("merobox.cli.sys.stdout", _Captured()),
        mock.patch("merobox.cli.sys.stderr", object()),
    ):
        _use_utf8_stdio()  # must not raise

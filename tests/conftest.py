# tests/conftest.py — shared fixtures and helpers
import os
import sys
import tempfile
import textwrap

import pytest

# Make sure project root is importable regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HELLO_SRC = os.path.join(
    os.path.dirname(__file__),
    "..", "lab", "sources", "hello-package", "hello-2.10",
)
HELLO_SRC = os.path.normpath(HELLO_SRC)


@pytest.fixture()
def tmp_pkg(tmp_path):
    """Return a minimal package source tree under a temp directory."""
    debian = tmp_path / "debian"
    debian.mkdir()
    (debian / "control").write_text(
        textwrap.dedent("""\
            Source: mypkg
            Section: devel
            Priority: optional
            Maintainer: Test User <test@example.com>
            Standards-Version: 4.7.0
            Build-Depends: debhelper-compat (= 13)

            Package: mypkg
            Architecture: any
            Depends: ${shlibs:Depends}
            Description: A test package
        """),
        encoding="utf-8",
    )
    (debian / "changelog").write_text(
        "mypkg (1.0-1) noble; urgency=medium\n\n"
        "  * Initial release.\n\n"
        " -- Test User <test@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n",
        encoding="utf-8",
    )
    return tmp_path

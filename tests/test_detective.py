# tests/test_detective.py — unit tests for agents/detective.py
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.detective import (
    _is_stdlib,
    _should_skip_dir,
    _PLATFORM_SKIP,
    scan_build_system,
    scan_autoconf_deps,
)


class TestIsStdlib:
    def test_os_is_stdlib(self):
        assert _is_stdlib("os")

    def test_sys_is_stdlib(self):
        assert _is_stdlib("sys")

    def test_requests_is_not_stdlib(self):
        assert not _is_stdlib("requests")

    def test_numpy_is_not_stdlib(self):
        assert not _is_stdlib("numpy")


class TestShouldSkipDir:
    def test_skips_tests(self):
        assert _should_skip_dir("tests")

    def test_skips_docs(self):
        assert _should_skip_dir("docs")

    def test_skips_win32(self):
        assert _should_skip_dir("win32")

    def test_skips_hidden(self):
        assert _should_skip_dir(".git")

    def test_does_not_skip_src(self):
        assert not _should_skip_dir("src")

    def test_case_insensitive(self):
        assert _should_skip_dir("Tests")
        assert _should_skip_dir("WIN32")


class TestPlatformSkip:
    def test_windows_header_skipped(self):
        assert _PLATFORM_SKIP.match("windows.h")

    def test_winsock_skipped(self):
        assert _PLATFORM_SKIP.match("winsock2.h")

    def test_stdio_skipped(self):
        assert _PLATFORM_SKIP.match("stdio.h")

    def test_stdlib_header_skipped(self):
        assert _PLATFORM_SKIP.match("stdlib.h")

    def test_linux_specific_not_skipped(self):
        # curl.h, openssl/ssl.h etc. should NOT be in the skip list
        assert not _PLATFORM_SKIP.match("curl/curl.h")
        assert not _PLATFORM_SKIP.match("openssl/ssl.h")
        assert not _PLATFORM_SKIP.match("zlib.h")


class TestScanBuildSystem:
    def test_detects_autoconf(self, tmp_path):
        (tmp_path / "configure.ac").write_text("AC_INIT([pkg],[1.0])")
        tools = scan_build_system(str(tmp_path))
        assert "autoconf" in tools
        assert "automake" in tools

    def test_detects_cmake(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)")
        tools = scan_build_system(str(tmp_path))
        assert "cmake" in tools

    def test_detects_meson(self, tmp_path):
        (tmp_path / "meson.build").write_text("project('pkg', 'c')")
        tools = scan_build_system(str(tmp_path))
        assert "meson" in tools
        assert "ninja-build" in tools

    def test_detects_quilt(self, tmp_path):
        patches = tmp_path / "debian" / "patches"
        patches.mkdir(parents=True)
        (patches / "series").write_text("fix-something.patch\n")
        tools = scan_build_system(str(tmp_path))
        assert "quilt" in tools

    def test_returns_empty_for_bare_dir(self, tmp_path):
        tools = scan_build_system(str(tmp_path))
        assert tools == []

    def test_no_duplicates(self, tmp_path):
        (tmp_path / "configure.ac").write_text("")
        (tmp_path / "Makefile.am").write_text("")
        tools = scan_build_system(str(tmp_path))
        assert len(tools) == len(set(tools))


class TestScanAutoconfDeps:
    def test_pkg_check_modules_found(self, tmp_path):
        (tmp_path / "configure.ac").write_text(
            textwrap.dedent("""\
                AC_INIT([mypkg],[1.0])
                PKG_CHECK_MODULES([ZLIB], [zlib >= 1.2])
            """)
        )
        results = scan_autoconf_deps(str(tmp_path))
        keys = list(results.keys())
        assert any("zlib" in k.lower() for k in keys), \
            f"Expected zlib in results, got: {keys}"

    def test_ac_check_lib_found(self, tmp_path):
        (tmp_path / "configure.ac").write_text(
            textwrap.dedent("""\
                AC_INIT([mypkg],[1.0])
                AC_CHECK_LIB([z], [deflate])
            """)
        )
        results = scan_autoconf_deps(str(tmp_path))
        keys = list(results.keys())
        assert any("z" in k for k in keys), f"Expected 'z' lib in results, got: {keys}"

    def test_cmake_find_package(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(
            "find_package(OpenSSL REQUIRED)\n"
            "find_package(ZLIB)\n"
        )
        results = scan_autoconf_deps(str(tmp_path))
        keys = list(results.keys())
        assert any("openssl" in k.lower() or "zlib" in k.lower() for k in keys), \
            f"Expected OpenSSL/ZLIB in results, got: {keys}"

    def test_returns_empty_for_no_build_files(self, tmp_path):
        results = scan_autoconf_deps(str(tmp_path))
        assert results == {}

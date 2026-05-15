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
    _rank_header_candidates,
    _is_blocked_package,
    _python_dedup,
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


class TestRankHeaderCandidates:
    """Tests for _rank_header_candidates path-based ranking."""

    def test_exact_path_kept(self):
        cands = [("libssl-dev", "/usr/include/openssl/ssl.h")]
        result = _rank_header_candidates(cands, "openssl/ssl.h")
        assert result == ["libssl-dev"]

    def test_bundled_copy_dropped_when_exact_exists(self):
        cands = [
            ("libssl-dev", "/usr/include/openssl/ssl.h"),
            ("apache2-dev", "/usr/include/apache2/openssl/ssl.h"),
            ("dovecot-dev", "/usr/include/dovecot/openssl/ssl.h"),
        ]
        result = _rank_header_candidates(cands, "openssl/ssl.h")
        assert result == ["libssl-dev"]
        assert "apache2-dev" not in result
        assert "dovecot-dev" not in result

    def test_multiarch_path_tier1(self):
        cands = [("libffi-dev", "/usr/include/x86_64-linux-gnu/ffi.h")]
        result = _rank_header_candidates(cands, "ffi.h")
        assert result == ["libffi-dev"]

    def test_multiarch_preferred_over_deeper(self):
        cands = [
            ("libffi-dev", "/usr/include/x86_64-linux-gnu/ffi.h"),
            ("some-other-dev", "/usr/include/compat/ffi.h"),
        ]
        result = _rank_header_candidates(cands, "ffi.h")
        assert result[0] == "libffi-dev"
        assert "some-other-dev" not in result  # dropped because tier1 exists

    def test_fallback_to_deeper_when_no_canonical(self):
        # mysql.h lives under /usr/include/mysql/mysql.h — no exact match
        cands = [("libmysqlclient-dev", "/usr/include/mysql/mysql.h")]
        result = _rank_header_candidates(cands, "mysql.h")
        assert result == ["libmysqlclient-dev"]

    def test_non_include_path_dropped(self):
        cands = [("libfoo-dev", "/usr/lib/libfoo.so")]
        result = _rank_header_candidates(cands, "foo.h")
        assert result == []

    def test_deduplication(self):
        cands = [
            ("libssl-dev", "/usr/include/openssl/ssl.h"),
            ("libssl-dev", "/usr/include/openssl/ssl.h"),
        ]
        result = _rank_header_candidates(cands, "openssl/ssl.h")
        assert result == ["libssl-dev"]

    def test_empty_input(self):
        assert _rank_header_candidates([], "openssl/ssl.h") == []

    def test_simple_header_exact(self):
        cands = [
            ("zlib1g-dev", "/usr/include/zlib.h"),
            ("lib32z1-dev", "/usr/lib32/usr/include/zlib.h"),
        ]
        result = _rank_header_candidates(cands, "zlib.h")
        assert result == ["zlib1g-dev"]


class TestIsBlockedPackage:
    def test_android_blocked(self):
        assert _is_blocked_package("android-liblog-dev")

    def test_mingw_blocked(self):
        assert _is_blocked_package("mingw-w64-x86-64-dev")

    def test_wine_blocked(self):
        assert _is_blocked_package("wine-dev")

    def test_libwine_blocked(self):
        assert _is_blocked_package("libwine-dev")

    def test_golang_blocked(self):
        assert _is_blocked_package("golang-github-foo-dev")

    def test_libc6_dev_blocked(self):
        assert _is_blocked_package("libc6-dev")

    def test_linux_libc_dev_blocked(self):
        assert _is_blocked_package("linux-libc-dev")

    def test_libssl_dev_not_blocked(self):
        assert not _is_blocked_package("libssl-dev")

    def test_zlib_not_blocked(self):
        assert not _is_blocked_package("zlib1g-dev")

    def test_libcurl_not_blocked(self):
        assert not _is_blocked_package("libcurl4-openssl-dev")


class TestPythonDedup:
    def test_removes_duplicates(self):
        result = _python_dedup(["libssl-dev", "libssl-dev", "zlib1g-dev"])
        assert result == ["libssl-dev", "zlib1g-dev"]

    def test_removes_blocked(self):
        result = _python_dedup(["libssl-dev", "libc6-dev", "android-liblog-dev"])
        assert "libc6-dev" not in result
        assert "android-liblog-dev" not in result
        assert "libssl-dev" in result

    def test_applies_name_corrections(self):
        result = _python_dedup(["lib32z1-dev", "libssl-dev"])
        assert "lib32z1-dev" not in result
        assert "zlib1g-dev" in result

    def test_skips_skip_marker(self):
        # SKIP is filtered upstream but corrections/blocklist shouldn't crash on it
        result = _python_dedup(["libssl-dev", "zlib1g-dev"])
        assert result == ["libssl-dev", "zlib1g-dev"]

    def test_sorted_output(self):
        result = _python_dedup(["zlib1g-dev", "libssl-dev", "libcurl4-openssl-dev"])
        assert result == sorted(result)

    def test_empty_input(self):
        assert _python_dedup([]) == []


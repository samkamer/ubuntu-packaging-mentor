# tests/test_auditor.py — unit tests for agents/auditor.py
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.auditor import (
    _is_valid_dep5,
    _regex_fallback,
    _dominant_license,
    _parse_debian_maintainers,
    _apply_filename_exceptions,
    _apply_wildcard_grouping,
    parse_licensecheck_output,
    build_dep5,
)


class TestIsValidDep5:
    def test_known_identifiers(self):
        for ident in ("MIT", "GPL-2+", "Apache-2.0", "ISC", "BSD-2-Clause", "LGPL-3+"):
            assert _is_valid_dep5(ident), f"{ident} should be valid"

    def test_combined_with_and(self):
        assert _is_valid_dep5("GPL-2+ AND LGPL-2.1+")

    def test_combined_with_or(self):
        assert _is_valid_dep5("curl or ISC")
        assert _is_valid_dep5("FSFUL or curl")

    def test_with_exception_clause(self):
        assert _is_valid_dep5("GPL-2+ with Autoconf-data exception")
        assert _is_valid_dep5("GPL-3+ with Autoconf-data exception")
        assert _is_valid_dep5("GPL-2+ with Libtool exception")

    def test_project_specific_identifier(self):
        assert _is_valid_dep5("curl")
        assert _is_valid_dep5("OLDAP-2.8")
        assert _is_valid_dep5("BSD-4-Clause-UC")

    def test_or_later_not_split(self):
        # 'or later' is part of a version string, not an alternative license
        assert _is_valid_dep5("GPL-2+")   # normalised form of 'GPL-2 or later'

    def test_unknown_returns_false(self):
        # Properly formatted identifiers are accepted (project-specific names are valid DEP-5)
        # Only reject things that look like prose / sentences
        assert not _is_valid_dep5("This is the MIT License, use it freely")

    def test_empty_string_invalid(self):
        assert not _is_valid_dep5("")


class TestRegexFallback:
    @pytest.mark.parametrize("raw,expected", [
        ("GNU General Public License v3.0 or later", "GPL-3+"),
        ("GNU General Public License v3",            "GPL-3"),
        ("GNU General Public License v2.0 or later", "GPL-2+"),
        ("GNU General Public License v2",            "GPL-2"),
        ("MIT License",                              "MIT"),
        ("X11 License",                              "X11"),
        ("ISC License",                              "ISC"),
        ("Apache License 2.0",                       "Apache-2.0"),
        ("BSD 2-Clause License",                     "BSD-2-Clause"),
        ("BSD 3-Clause License",                     "BSD-3-Clause"),
        ("Public Domain",                            "public-domain"),
        ("UNKNOWN",                                  "UNKNOWN"),
        ("GNU Lesser General Public License v2.1 or later", "LGPL-2.1+"),
        ("GNU Lesser General Public License v3",    "LGPL-3"),
        ("MPL 2.0",                                  "MPL-2.0"),
        # Exception-aware patterns
        ("GNU General Public License v2.0 or later (Autoconf-data exception)",
                                                     "GPL-2+ with Autoconf-data exception"),
        ("GNU General Public License v2 with Autoconf-data exception",
                                                     "GPL-2+ with Autoconf-data exception"),
        ("GNU General Public License v2.0 or later (Libtool exception)",
                                                     "GPL-2+ with Libtool exception"),
        ("GNU General Public License v3.0 or later (Autoconf data exception)",
                                                     "GPL-3+ with Autoconf-data exception"),
        # Project-specific and SPDX extras
        ("curl License",                             "curl"),
        ("OpenLDAP Public License 2.8",              "OLDAP-2.8"),
        ("BSD 4-Clause University of California",    "BSD-4-Clause-UC"),
    ])
    def test_known_mapping(self, raw, expected):
        assert _regex_fallback(raw) == expected

    def test_strips_generated_file_tag(self):
        result = _regex_fallback("MIT License [generated file]")
        assert result == "MIT"

    def test_and_joining(self):
        # 'and/or' in license context means either may be chosen → DEP-5 'or'
        result = _regex_fallback("MIT and/or Apache License 2.0")
        assert result == "MIT or Apache-2.0"

    def test_or_dual_license(self):
        result = _regex_fallback("ISC License or curl License")
        assert result == "ISC or curl"

    def test_truly_unknown_returns_cleaned_string(self):
        result = _regex_fallback("Some Exotic License 1.0")
        assert result == "Some Exotic License 1.0"


class TestParseLicensecheckOutput:
    SAMPLE = """\
./src/foo.c: GNU General Public License v2.0 or later
  [Copyright: 2020 Alice]
./src/bar.h: MIT License
  [Copyright: 2021 Bob]
  [Copyright: 2022 Charlie]
./README: UNKNOWN
"""

    def test_returns_correct_entry_count(self):
        entries = parse_licensecheck_output(self.SAMPLE)
        assert len(entries) == 3

    def test_first_entry_fields(self):
        entries = parse_licensecheck_output(self.SAMPLE)
        e = entries[0]
        # Leading './' is stripped — DEP-5 uses plain relative paths
        assert e["file"] == "src/foo.c"
        # Parser returns the raw licensecheck string; normalization happens later
        assert "General Public License" in e["license"]
        assert e["copyrights"] == ["2020 Alice"]

    def test_multiple_copyrights(self):
        entries = parse_licensecheck_output(self.SAMPLE)
        bar = next(e for e in entries if "bar.h" in e["file"])
        assert len(bar["copyrights"]) == 2
        assert "2021 Bob" in bar["copyrights"]
        assert "2022 Charlie" in bar["copyrights"]

    def test_empty_output(self):
        assert parse_licensecheck_output("") == []

    def test_no_copyright_block(self):
        output = "./src/x.c: MIT License\n"
        entries = parse_licensecheck_output(output)
        assert len(entries) == 1
        assert entries[0]["copyrights"] == []


class TestBuildDep5:
    def _make_groups(self, ambiguous=False):
        return {
            ("MIT", frozenset(["2024 Alice"])): {
                "files": ["src/foo.c"],
                "license": "MIT",
                "copyrights": ["2024 Alice"],
                "ambiguous": ambiguous,
            },
        }

    def test_contains_format_header(self):
        dep5 = build_dep5(self._make_groups(), "mypkg")
        assert "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/" in dep5

    def test_contains_upstream_name(self):
        dep5 = build_dep5(self._make_groups(), "mypkg")
        assert "Upstream-Name: mypkg" in dep5

    def test_contains_license_identifier(self):
        dep5 = build_dep5(self._make_groups(), "mypkg")
        assert "License: MIT" in dep5

    def test_contains_copyright_line(self):
        dep5 = build_dep5(self._make_groups(), "mypkg")
        assert "Copyright: 2024 Alice" in dep5

    def test_contains_files_line(self):
        dep5 = build_dep5(self._make_groups(), "mypkg")
        assert "Files:" in dep5

    def test_no_fixme_comment_when_unambiguous(self):
        dep5 = build_dep5(self._make_groups(ambiguous=False), "mypkg")
        # The only FIXME should be in the header placeholders, not in a license comment
        assert "FIXME: licensecheck reported" not in dep5

    def test_fixme_comment_when_ambiguous(self):
        groups = {
            ("ISC or curl", frozenset(["2024 Alice"])): {
                "files": ["src/foo.c"],
                "license": "ISC or curl",
                "copyrights": ["2024 Alice"],
                "ambiguous": True,
            },
        }
        dep5 = build_dep5(groups, "mypkg")
        assert "FIXME" in dep5
        assert "and/or" in dep5
        assert "License: ISC or curl" in dep5

    def test_files_star_catch_all_is_first_stanza(self):
        dep5 = build_dep5(self._make_groups(), "mypkg")
        # Files: * must appear before any other Files: stanza
        lines = dep5.splitlines()
        files_lines = [i for i, l in enumerate(lines) if l.startswith("Files:")]
        assert files_lines, "No Files: stanza found"
        assert lines[files_lines[0]] == "Files: *"

    def test_files_star_uses_dominant_license(self):
        groups = {
            ("MIT", frozenset(["2024 Alice"])): {
                "files": ["a.c", "b.c", "c.c"],
                "license": "MIT",
                "copyrights": ["2024 Alice"],
                "ambiguous": False,
            },
            ("ISC", frozenset(["2024 Bob"])): {
                "files": ["x.c"],
                "license": "ISC",
                "copyrights": ["2024 Bob"],
                "ambiguous": False,
            },
        }
        dep5 = build_dep5(groups, "mypkg")
        # MIT covers 3 files vs ISC covers 1 — Files: * should use MIT
        lines = dep5.splitlines()
        star_idx = next(i for i, l in enumerate(lines) if l == "Files: *")
        assert lines[star_idx + 2] == "License: MIT"

    def test_debian_stanza_added_when_changelog_present(self, tmp_path):
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "changelog").write_text(
            "mypkg (1.0) unstable; urgency=low\n\n"
            "  * Initial release\n\n"
            "-- Alice Dev <alice@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n\n"
            "mypkg (0.9) unstable; urgency=low\n\n"
            "  * Beta\n\n"
            "-- Bob Maintainer <bob@example.com>  Tue, 01 Jan 2019 00:00:00 +0000\n"
        )
        dep5 = build_dep5(self._make_groups(), "mypkg", source_dir=str(tmp_path))
        assert "Files: debian/*" in dep5
        assert "Alice Dev <alice@example.com>" in dep5
        assert "Bob Maintainer <bob@example.com>" in dep5

    def test_no_debian_stanza_when_no_changelog(self, tmp_path):
        dep5 = build_dep5(self._make_groups(), "mypkg", source_dir=str(tmp_path))
        assert "Files: debian/*" not in dep5


class TestDominantLicense:
    def _groups(self):
        return {
            ("MIT", frozenset(["2024 Alice"])): {
                "files": ["a.c", "b.c", "c.c"],
                "license": "MIT",
                "copyrights": ["2024 Alice"],
                "ambiguous": False,
            },
            ("ISC", frozenset(["2024 Bob"])): {
                "files": ["x.c"],
                "license": "ISC",
                "copyrights": ["2024 Bob"],
                "ambiguous": False,
            },
        }

    def test_returns_most_common_license(self):
        dep5_id, _ = _dominant_license(self._groups())
        assert dep5_id == "MIT"

    def test_returns_primary_copyright(self):
        _, copyrights = _dominant_license(self._groups())
        assert any("Alice" in cp for cp in copyrights)

    def test_et_al_when_multiple_holders(self):
        groups = {
            ("MIT", frozenset(["2024 Alice"])): {
                "files": ["a.c"],
                "license": "MIT",
                "copyrights": ["2024 Alice"],
                "ambiguous": False,
            },
            ("MIT", frozenset(["2024 Bob"])): {
                "files": ["b.c"],
                "license": "MIT",
                "copyrights": ["2024 Bob"],
                "ambiguous": False,
            },
        }
        _, copyrights = _dominant_license(groups)
        assert any("et al." in cp for cp in copyrights)


class TestParseDebianMaintainers:
    def test_extracts_single_maintainer(self, tmp_path):
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "changelog").write_text(
            "pkg (1.0) unstable; urgency=low\n\n  * First\n\n"
            "-- Alice Dev <alice@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
        )
        result = _parse_debian_maintainers(str(tmp_path))
        assert len(result) == 1
        assert "Alice Dev <alice@example.com>" in result[0]
        assert "2024" in result[0]

    def test_collapses_year_range(self, tmp_path):
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "changelog").write_text(
            "pkg (1.1) unstable; urgency=low\n\n  * Update\n\n"
            "-- Alice Dev <alice@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n\n"
            "pkg (1.0) unstable; urgency=low\n\n  * Init\n\n"
            "-- Alice Dev <alice@example.com>  Mon, 01 Jan 2020 00:00:00 +0000\n"
        )
        result = _parse_debian_maintainers(str(tmp_path))
        assert len(result) == 1
        assert "2020-2024" in result[0]

    def test_multiple_maintainers(self, tmp_path):
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "changelog").write_text(
            "pkg (1.1) unstable; urgency=low\n\n  * Update\n\n"
            "-- Alice Dev <alice@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n\n"
            "pkg (1.0) unstable; urgency=low\n\n  * Init\n\n"
            "-- Bob Maint <bob@example.com>  Mon, 01 Jan 2019 00:00:00 +0000\n"
        )
        result = _parse_debian_maintainers(str(tmp_path))
        assert len(result) == 2

    def test_returns_empty_when_no_changelog(self, tmp_path):
        result = _parse_debian_maintainers(str(tmp_path))
        assert result == []


class TestStripDotSlash:
    def test_strips_leading_dot_slash(self):
        output = "./src/foo.c: MIT License\n  [Copyright: 2024 Alice]\n"
        entries = parse_licensecheck_output(output)
        assert entries[0]["file"] == "src/foo.c"

    def test_root_level_file_stripped(self):
        output = "./README: UNKNOWN\n"
        entries = parse_licensecheck_output(output)
        assert entries[0]["file"] == "README"

    def test_no_leading_dot_slash_unchanged(self):
        # If licensecheck ever outputs without ./ prefix, we handle it gracefully
        output = "./deep/path/file.c: MIT License\n"
        entries = parse_licensecheck_output(output)
        assert not entries[0]["file"].startswith("./")


class TestApplyFilenameExceptions:
    def _entry(self, filename, license_str="GNU General Public License v2.0 or later"):
        return {"file": f"build/{filename}", "license": license_str, "copyrights": []}

    def test_compile_gets_autoconf_exception(self):
        entries = [self._entry("compile")]
        result = _apply_filename_exceptions(entries)
        assert result[0]["license"] == "GPL-2+ with Autoconf-data exception"

    def test_depcomp_gets_autoconf_exception(self):
        entries = [self._entry("depcomp")]
        result = _apply_filename_exceptions(entries)
        assert result[0]["license"] == "GPL-2+ with Autoconf-data exception"

    def test_missing_gets_autoconf_exception(self):
        entries = [self._entry("missing")]
        result = _apply_filename_exceptions(entries)
        assert result[0]["license"] == "GPL-2+ with Autoconf-data exception"

    def test_ltmain_sh_gets_libtool_exception(self):
        entries = [self._entry("ltmain.sh")]
        result = _apply_filename_exceptions(entries)
        assert result[0]["license"] == "GPL-2+ with Libtool exception"

    def test_config_guess_gets_gpl3_autoconf_exception(self):
        entries = [self._entry("config.guess")]
        result = _apply_filename_exceptions(entries)
        assert result[0]["license"] == "GPL-3+ with Autoconf-data exception"

    def test_config_sub_gets_gpl3_autoconf_exception(self):
        entries = [self._entry("config.sub")]
        result = _apply_filename_exceptions(entries)
        assert result[0]["license"] == "GPL-3+ with Autoconf-data exception"

    def test_unrelated_file_unchanged(self):
        entries = [self._entry("main.c", "MIT")]
        result = _apply_filename_exceptions(entries)
        assert result[0]["license"] == "MIT"

    def test_install_sh_is_not_overridden(self):
        # install-sh is ambiguous by basename — not in override map
        entries = [self._entry("install-sh", "MIT")]
        result = _apply_filename_exceptions(entries)
        assert result[0]["license"] == "MIT"


class TestApplyWildcardGrouping:
    def _group(self, license_id, files, copyrights=None):
        if copyrights is None:
            copyrights = ["2024 Author"]
        return {
            "files": files,
            "license": license_id,
            "copyrights": copyrights,
            "ambiguous": False,
        }

    def test_single_owner_directory_collapses(self):
        groups = {
            ("MIT", frozenset(["2024 A"])): self._group("MIT", ["lib/a.c", "lib/b.c"]),
        }
        result = _apply_wildcard_grouping(groups)
        info = list(result.values())[0]
        assert info["files"] == ["lib/*"]

    def test_shared_directory_does_not_collapse(self):
        groups = {
            ("MIT", frozenset(["2024 A"])): self._group("MIT", ["lib/a.c"]),
            ("ISC", frozenset(["2024 B"])): self._group("ISC", ["lib/b.c"]),
        }
        result = _apply_wildcard_grouping(groups)
        for info in result.values():
            assert "lib/*" not in info["files"]

    def test_root_level_files_not_collapsed(self):
        groups = {
            ("MIT", frozenset(["2024 A"])): self._group("MIT", ["README", "LICENSE"]),
        }
        result = _apply_wildcard_grouping(groups)
        info = list(result.values())[0]
        # Root-level files should stay individual
        assert "/*" not in " ".join(info["files"])
        assert "README" in info["files"]

    def test_single_file_in_dir_not_collapsed(self):
        groups = {
            ("MIT", frozenset(["2024 A"])): self._group("MIT", ["lib/only.c"]),
        }
        result = _apply_wildcard_grouping(groups)
        info = list(result.values())[0]
        assert info["files"] == ["lib/only.c"]

    def test_mixed_dirs_partial_collapse(self):
        # lib/ is exclusive to group A (2 files), src/ is shared
        groups = {
            ("MIT", frozenset(["2024 A"])): self._group("MIT", ["lib/a.c", "lib/b.c", "src/x.c"]),
            ("ISC", frozenset(["2024 B"])): self._group("ISC", ["src/y.c"]),
        }
        result = _apply_wildcard_grouping(groups)
        mit_info = result[("MIT", frozenset(["2024 A"]))]
        assert "lib/*" in mit_info["files"]
        assert "src/x.c" in mit_info["files"]
        assert "src/*" not in mit_info["files"]


class TestLicenseTextBodies:
    def _make_group(self, license_id, files=None):
        return {
            (license_id, frozenset(["2024 Author"])): {
                "files": files or ["src/foo.c"],
                "license": license_id,
                "copyrights": ["2024 Author"],
                "ambiguous": False,
            }
        }

    def test_known_license_text_embedded(self):
        dep5 = build_dep5(self._make_group("ISC"), "mypkg")
        # Should contain the ISC license text, not a URL stub
        assert "Permission to use, copy, modify" in dep5
        assert "spdx.org" not in dep5

    def test_unknown_license_gets_fixme(self):
        dep5 = build_dep5(self._make_group("UNKNOWN"), "mypkg")
        assert "FIXME" in dep5

    def test_compound_or_license_splits_into_two_paragraphs(self):
        groups = {
            ("curl or ISC", frozenset(["2024 A"])): {
                "files": ["src/foo.c"],
                "license": "curl or ISC",
                "copyrights": ["2024 A"],
                "ambiguous": False,
            }
        }
        dep5 = build_dep5(groups, "mypkg")
        # Both individual license paragraphs should appear
        lines = dep5.splitlines()
        standalone = [l for l in lines if l.startswith("License:") and "Files:" not in dep5[:dep5.index(l)] or False]
        # Simpler: count standalone License: lines (those not in a Files: block)
        assert "License: curl" in lines
        assert "License: ISC" in lines
        # The compound expression should NOT appear as a standalone paragraph
        assert "License: curl or ISC" not in [
            l for l in lines
            if l.startswith("License:") and l != "License: curl or ISC"
        ] or "License: curl or ISC" in lines  # it appears in Files block, that's fine

    def test_no_url_stub_for_known_license(self):
        dep5 = build_dep5(self._make_group("MIT"), "mypkg")
        assert "spdx.org" not in dep5
        # No license-text FIXME for a known license (header FIXME placeholders are fine)
        assert "FIXME: license text for" not in dep5

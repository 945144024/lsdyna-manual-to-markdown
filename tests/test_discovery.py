"""Tests for manual filename parsing and volume discovery."""

from pathlib import Path

import pytest

from lsdyna_manual.parser.discovery import (
    DiscoveryError,
    discover_volumes,
    parse_manual_filename,
)


def test_filename_variants_across_releases():
    cases = {
        "LS-DYNA_Manual_Volume_I_R13.pdf": (1, "R13"),
        "LS-DYNA_Manual_Volume_II_R14.pdf": (2, "R14"),
        "LS-DYNA_Manual_Vol_III_R17.pdf": (3, "R17"),
        "LS-DYNA_manual_Vol_II_R7.1.pdf": (2, "R7.1"),
        "ls-dyna_manual_volume_i_r12.pdf": (1, "R12"),
    }
    for name, (volume, release) in cases.items():
        info = parse_manual_filename(Path(name))
        assert info is not None, name
        assert info.volume == volume, name
        assert info.release == release, name


def test_non_keyword_manuals_are_rejected():
    names = [
        "LS-DYNA_Manual_Theory_R16.pdf",
        "DRAFT_Vol_I.pdf",
        "notes.pdf",
        "LS-DYNA_Manual_Volume_IV_R13.pdf",
    ]
    for name in names:
        assert parse_manual_filename(Path(name)) is None, name


def test_discover_selects_expected_release(tmp_path):
    for name in [
        "LS-DYNA_Manual_Volume_I_R12.pdf",
        "LS-DYNA_Manual_Volume_I_R13.pdf",
        "LS-DYNA_Manual_Volume_II_R13.pdf",
    ]:
        (tmp_path / name).touch()
    infos = discover_volumes(tmp_path, expected_release="R13")
    assert [info.volume for info in infos] == [1, 2]
    assert all(info.release == "R13" for info in infos)


def test_discover_without_filter_ignores_other_names(tmp_path):
    (tmp_path / "LS-DYNA_Manual_Volume_I_R13.pdf").touch()
    (tmp_path / "LS-DYNA_Manual_Theory_R13.pdf").touch()
    infos = discover_volumes(tmp_path)
    assert [info.volume for info in infos] == [1]


def test_discover_mixed_releases_for_same_volume(tmp_path):
    (tmp_path / "LS-DYNA_Manual_Volume_I_R12.pdf").touch()
    (tmp_path / "LS-DYNA_Manual_Volume_I_R13.pdf").touch()
    with pytest.raises(DiscoveryError):
        discover_volumes(tmp_path)


def test_discover_no_match_for_release(tmp_path):
    (tmp_path / "LS-DYNA_Manual_Volume_I_R13.pdf").touch()
    with pytest.raises(DiscoveryError):
        discover_volumes(tmp_path, expected_release="R99")

"""Runtime preflight tests."""

from pathlib import Path

import lsdyna_manual.preflight as preflight


def _write_remote_config(tmp_path: Path, *, api_key: str | None) -> Path:
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    (manuals / "LS-DYNA_Manual_Vol_I_R17.pdf").write_bytes(b"synthetic")
    key_line = f'"{api_key}"' if api_key else "null"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""manual:
  release: R17
  manuals_dir: "{manuals.as_posix()}"
parser:
  provider: paddleocr-vl-remote
  api_key: {key_line}
output:
  corpus_dir: "{(tmp_path / 'corpus').as_posix()}"
""",
        encoding="utf-8",
    )
    return config


def test_preflight_warns_for_missing_remote_key(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "pdftotext")
    result = preflight.run_preflight(
        _write_remote_config(tmp_path, api_key=None), log=lambda _msg: None
    )

    assert not result.failed
    assert result.warning
    assert any(
        check.name == "remote credentials" and check.status == "warning"
        for check in result.checks
    )


def test_preflight_passes_without_starting_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "pdftotext")
    result = preflight.run_preflight(
        _write_remote_config(tmp_path, api_key="secret"), log=lambda _msg: None
    )

    assert not result.failed
    assert all(check.status == "ok" for check in result.checks)

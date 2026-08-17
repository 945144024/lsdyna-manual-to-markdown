"""Local PaddleOCR-VL runtime preparation and llama-server lifecycle.

The runtime manager is deliberately conservative: it only mutates the local
Python environment or downloads model/binary artifacts when the caller passes
an explicit installation authorization. Normal parsing only validates an
already prepared runtime.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import subprocess
import tarfile
import time
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

from lsdyna_manual.config import LocalProviderConfig
from lsdyna_manual.providers.base import ProviderError


class LocalRuntimeError(ProviderError):
    """Raised when the local PaddleOCR-VL runtime is unavailable."""


@dataclass(frozen=True)
class LocalRuntimePaths:
    llama_server: Path
    model: Path
    mmproj: Path
    paddleocr_python: Path
    paddlex_cache: Path
    layout_model: Path


def _default_model_paths(config: LocalProviderConfig) -> tuple[Path, Path]:
    model_dir = config.runtime_dir / "models" / "paddleocr-vl-1.6-gguf"
    return (
        model_dir / "PaddleOCR-VL-1.6-GGUF.gguf",
        model_dir / "PaddleOCR-VL-1.6-GGUF-mmproj.gguf",
    )


class LocalRuntimeManager:
    """Validate, optionally prepare, and manage the local inference server."""

    def __init__(self, config: LocalProviderConfig) -> None:
        self.config = config
        self._server_process: subprocess.Popen[bytes] | None = None
        self._server_log = None
        self._file_hashes: dict[Path, str] = {}

    def _llama_server_path(self) -> Path | None:
        if self.config.llama_server_path is not None:
            return Path(self.config.llama_server_path)
        bundled = self.config.runtime_dir / "bin" / "llama-server"
        if bundled.is_file():
            return bundled
        found = shutil.which("llama-server")
        return Path(found) if found else None

    def paths(self) -> LocalRuntimePaths:
        model_default, mmproj_default = _default_model_paths(self.config)
        paddlex_cache = Path(
            self.config.paddlex_cache_dir
            or self.config.runtime_dir / "paddlex"
        )
        return LocalRuntimePaths(
            llama_server=self._llama_server_path() or self.config.runtime_dir / "bin" / "llama-server",
            model=Path(self.config.model_path or model_default),
            mmproj=Path(self.config.mmproj_path or mmproj_default),
            paddleocr_python=Path(
                self.config.paddleocr_python
                or self.config.runtime_dir / "venv" / "bin" / "python"
            ),
            paddlex_cache=paddlex_cache,
            layout_model=paddlex_cache / "official_models" / "PP-DocLayoutV3",
        )

    @staticmethod
    def _layout_model_ready(path: Path) -> bool:
        return all(
            (path / filename).is_file()
            for filename in ("inference.yml", "inference.json", "inference.pdiparams")
        )

    @staticmethod
    def _modules_available(python: Path) -> bool:
        if not python.is_file():
            return False
        try:
            result = subprocess.run(
                [str(python), "-c", "import paddle, paddleocr"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            return False
        return result.returncode == 0

    def _install_python_dependencies(self, python: Path) -> None:
        if not python.is_file():
            python.parent.parent.mkdir(parents=True, exist_ok=True)
            try:
                venv.EnvBuilder(with_pip=True).create(python.parent.parent)
            except OSError as exc:
                raise LocalRuntimeError(
                    f"failed to create local PaddleOCR environment: {python.parent.parent}"
                ) from exc
        command = [str(python), "-m", "pip", "install", "-U", self.config.paddlepaddle_package]
        if self.config.paddlepaddle_index_url:
            command.extend(["-i", self.config.paddlepaddle_index_url])
        self._run_install(command)
        self._run_install(
            [str(python), "-m", "pip", "install", "-U", self.config.paddleocr_package]
        )

    @staticmethod
    def _run_install(command: list[str]) -> None:
        try:
            subprocess.run(command, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LocalRuntimeError(
                "local runtime installation failed: " + " ".join(command)
            ) from exc

    @staticmethod
    def _download(url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            with requests.get(url, stream=True, timeout=(30, 300)) as response:
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            temporary.replace(destination)
        except (OSError, requests.RequestException) as exc:
            temporary.unlink(missing_ok=True)
            raise LocalRuntimeError(f"failed to download local runtime artifact: {url}") from exc

    def _download_models(self, paths: LocalRuntimePaths) -> None:
        revision = self.config.model_revision or "main"
        base = f"https://huggingface.co/{self.config.model_repo}/resolve/{revision}"
        if not paths.model.is_file():
            self._download(f"{base}/PaddleOCR-VL-1.6-GGUF.gguf", paths.model)
        if not paths.mmproj.is_file():
            self._download(f"{base}/PaddleOCR-VL-1.6-GGUF-mmproj.gguf", paths.mmproj)

    def _download_layout_model(self, paths: LocalRuntimePaths) -> None:
        archive = paths.paddlex_cache / ".PP-DocLayoutV3_infer.tar.part"
        self._download_archive(
            self.config.layout_model_archive_url,
            archive,
            paths.layout_model.parent,
        )
        archive.unlink(missing_ok=True)
        if not self._layout_model_ready(paths.layout_model):
            raise LocalRuntimeError(
                "PaddleX layout-model archive was extracted but required files "
                f"are missing: {paths.layout_model}"
            )

    def _download_llama_server(self, path: Path) -> None:
        direct_url = self.config.llama_server_download_url
        archive_url = self.config.llama_server_archive_url
        if not direct_url and not archive_url:
            raise LocalRuntimeError(
                "llama-server is missing; set parser.local.llama_server_path "
                "or parser.local llama-server download/archive URL"
            )
        if direct_url:
            self._download(direct_url, path)
            if path.suffix.casefold() != ".exe":
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            return
        archive = path.parent / ".llama-server.archive.part"
        self._download_archive(archive_url, archive, path.parent)
        archive.unlink(missing_ok=True)

        if self.config.llama_cuda_archive_url:
            cuda_archive = path.parent / ".llama-cuda.archive.part"
            self._download_archive(
                self.config.llama_cuda_archive_url,
                cuda_archive,
                path.parent,
            )
            cuda_archive.unlink(missing_ok=True)
        if not path.is_file():
            raise LocalRuntimeError(
                f"llama-server archive extracted successfully but expected executable is missing: {path}"
            )
        if path.suffix.casefold() != ".exe":
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    @staticmethod
    def _download_archive(url: str | None, archive: Path, destination: Path) -> None:
        if not url:
            raise LocalRuntimeError("runtime archive URL must not be empty")
        LocalRuntimeManager._download(url, archive)
        destination.mkdir(parents=True, exist_ok=True)
        try:
            if zipfile.is_zipfile(archive):
                with zipfile.ZipFile(archive) as handle:
                    LocalRuntimeManager._extract_zip_safely(handle, destination)
                return
            if tarfile.is_tarfile(archive):
                with tarfile.open(archive) as handle:
                    LocalRuntimeManager._extract_tar_safely(handle, destination)
                return
        except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
            raise LocalRuntimeError(f"failed to extract runtime archive: {archive}") from exc
        raise LocalRuntimeError(f"unsupported runtime archive format: {archive}")

    @staticmethod
    def _extract_zip_safely(handle: zipfile.ZipFile, destination: Path) -> None:
        root = destination.resolve()
        for member in handle.infolist():
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise LocalRuntimeError("runtime archive contains an unsafe path")
        handle.extractall(destination)

    @staticmethod
    def _extract_tar_safely(handle: tarfile.TarFile, destination: Path) -> None:
        root = destination.resolve()
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if root != target and root not in target.parents:
                raise LocalRuntimeError("runtime archive contains an unsafe path")
        handle.extractall(destination)

    def ensure_ready(self, *, allow_install: bool = False) -> LocalRuntimePaths:
        paths = self.paths()
        if not self._modules_available(paths.paddleocr_python):
            if not (self.config.auto_prepare_runtime and allow_install):
                raise LocalRuntimeError(
                    "local PaddleOCR is not installed; install the local runtime "
                    "or rerun with parser.local.auto_prepare_runtime=true and "
                    "--allow-runtime-install"
                )
            self._install_python_dependencies(paths.paddleocr_python)
            if not self._modules_available(paths.paddleocr_python):
                raise LocalRuntimeError("PaddleOCR installation completed but import still fails")

        if not paths.model.is_file() or not paths.mmproj.is_file():
            if not (self.config.auto_prepare_runtime and allow_install):
                raise LocalRuntimeError(
                    f"local GGUF artifacts are missing: {paths.model} and {paths.mmproj}"
                )
            self._download_models(paths)

        if not paths.llama_server.is_file():
            if not (self.config.auto_prepare_runtime and allow_install):
                raise LocalRuntimeError(
                    f"llama-server not found: {paths.llama_server}; configure its path "
                    "or provide an explicit download URL"
                )
            self._download_llama_server(paths.llama_server)

        if not self._layout_model_ready(paths.layout_model):
            if not (self.config.auto_prepare_runtime and allow_install):
                raise LocalRuntimeError(
                    "local PaddleX layout model is missing: "
                    f"{paths.layout_model}; prepare the runtime or rerun with "
                    "parser.local.auto_prepare_runtime=true and --allow-runtime-install"
                )
            self._download_layout_model(paths)

        return paths

    def _health_url(self) -> str:
        parsed = urlsplit(self.config.llama_server_url)
        if not parsed.scheme or not parsed.netloc:
            raise LocalRuntimeError(
                f"invalid parser.local.llama_server_url: {self.config.llama_server_url}"
            )
        return f"{parsed.scheme}://{parsed.netloc}/health"

    def is_healthy(self) -> bool:
        try:
            response = requests.get(self._health_url(), timeout=2)
        except requests.RequestException:
            return False
        return response.status_code == 200

    def start(self, paths: LocalRuntimePaths) -> None:
        if self.is_healthy():
            return
        model_arg = self._path_argument(paths.llama_server, paths.model)
        mmproj_arg = self._path_argument(paths.llama_server, paths.mmproj)
        command = [
            str(paths.llama_server),
            "-m",
            model_arg,
            "--mmproj",
            mmproj_arg,
            "--host",
            self.config.llama_server_host,
            "--port",
            str(self.config.llama_server_port),
            "--temp",
            "0",
            *self.config.llama_server_args,
        ]
        log_dir = self.config.runtime_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._server_log = (log_dir / "llama-server.log").open("ab")
        try:
            self._server_process = subprocess.Popen(
                command,
                stdout=self._server_log,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            self._close_server_log()
            raise LocalRuntimeError(f"failed to start llama-server: {paths.llama_server}") from exc

        deadline = time.monotonic() + self.config.health_timeout_seconds
        while time.monotonic() < deadline:
            if self.is_healthy():
                return
            if self._server_process.poll() is not None:
                self._close_server_log()
                raise LocalRuntimeError(
                    "llama-server exited before its health endpoint became ready; "
                    f"inspect {log_dir / 'llama-server.log'}"
                )
            time.sleep(0.5)
        self.stop()
        raise LocalRuntimeError(
            f"llama-server did not become ready within {self.config.health_timeout_seconds}s"
        )

    def stop(self) -> None:
        process = self._server_process
        self._server_process = None
        if process is None or process.poll() is not None:
            self._close_server_log()
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self._close_server_log()

    @staticmethod
    def _path_argument(executable: Path, path: Path) -> str:
        if executable.suffix.casefold() != ".exe":
            return str(path)
        try:
            result = subprocess.run(
                ["wslpath", "-w", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LocalRuntimeError(f"failed to convert WSL path for {executable}") from exc
        return result.stdout.strip()

    def _close_server_log(self) -> None:
        if self._server_log is not None:
            self._server_log.close()
            self._server_log = None

    def close(self) -> None:
        self.stop()

    def fingerprint(self, paths: LocalRuntimePaths) -> str:
        values = [
            "paddleocr-vl-local",
            self.config.pipeline_version,
            self.config.model_repo,
            self.config.model_revision or "main",
            str(paths.model),
            str(paths.mmproj),
            str(paths.layout_model),
            self.config.llama_server_url,
        ]
        identity_files = (
            paths.model,
            paths.mmproj,
            paths.layout_model / "inference.yml",
            paths.layout_model / "inference.json",
            paths.layout_model / "inference.pdiparams",
        )
        for path in identity_files:
            if path.is_file():
                values.append(self._sha256(path))
        return ":".join(values)

    def _sha256(self, path: Path) -> str:
        cached = self._file_hashes.get(path)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                digest.update(chunk)
        value = digest.hexdigest()
        self._file_hashes[path] = value
        return value

"""
Fetch and cache Calimero NEAR contracts from GitHub releases.

When using near_devnet, merobox can automatically download the context config
and proxy WASM contracts from the calimero-network/contracts release so that
users do not need to run a separate download script or provide --contracts-dir.
"""

import hashlib
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

import requests
from rich.console import Console
from tqdm import tqdm

<<<<<<< bounty-fix/validate-tarfile-extraction-paths-to-pre-mlrug3j3
from .utils import safe_tar_extract
=======
from merobox.commands.constants import (
    CONTRACT_DOWNLOAD_LOCK_TIMEOUT,
    CONTRACT_DOWNLOAD_TIMEOUT,
)
>>>>>>> master

try:
    from filelock import FileLock
except ImportError:
    FileLock = None

console = Console()


_filelock_warning_shown = False


def _warn_no_filelock():
    global _filelock_warning_shown
    if FileLock is None and not _filelock_warning_shown:
        _filelock_warning_shown = True
        console.print(
            "[yellow]Warning: filelock not available; concurrent downloads may corrupt the cache. "
            "Install with: pip install filelock[/yellow]"
        )


CONTRACTS_REPO_OWNER = "calimero-network"
CONTRACTS_REPO_NAME = "contracts"
NEAR_ASSET_NAME = "near.tar.gz"
CONFIG_WASM = "calimero_context_config_near.wasm"
PROXY_WASM = "calimero_context_proxy_near.wasm"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB cap to avoid disk exhaustion


def _safe_unlink(path: Path) -> None:
    """Remove file at path if it exists; ignore OSError."""
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _contracts_cache_base():
    return Path.home() / ".merobox" / "contracts"


def _dir_has_contracts(dir_path: Path) -> bool:
    return (dir_path / CONFIG_WASM).is_file() and (dir_path / PROXY_WASM).is_file()


def _get_checksum_url(assets: list) -> str | None:
    """Return browser_download_url for NEAR_ASSET_NAME.sha256 if present."""
    for a in assets:
        if a.get("name") == f"{NEAR_ASSET_NAME}.sha256":
            return a.get("browser_download_url")
    return None


def _verify_sha256(file_path: Path, expected_hex: str) -> None:
    """Raise RuntimeError if file_path's SHA256 does not match expected_hex."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    if h.hexdigest().lower() != expected_hex.lower():
        raise RuntimeError(
            f"SHA256 mismatch for {file_path.name}: expected {expected_hex}, got {h.hexdigest()}"
        )


def _locate_contracts_after_extract(
    extract_base: Path, cache_dir: Path, names: list[str]
) -> Path:
    """
    Find contracts in the extracted tree and normalize into cache_dir so
    subsequent calls hit the cache. Returns cache_dir (always the same path).
    """
    if _dir_has_contracts(cache_dir):
        return cache_dir
    if _dir_has_contracts(extract_base):
        cache_dir.mkdir(parents=True, exist_ok=True)
        for f in list(extract_base.iterdir()):
            if f.is_dir() or f.name == cache_dir.name:
                continue
            if f.is_file() and f.name != NEAR_ASSET_NAME and not f.name.startswith("."):
                dest = cache_dir / f.name
                if dest.exists():
                    dest.unlink()
                f.rename(dest)
        return cache_dir
    for name in names:
        if "/" not in name.strip("/"):
            candidate = extract_base / name.strip("/")
            if candidate.is_dir() and _dir_has_contracts(candidate):
                if candidate.resolve() == cache_dir.resolve():
                    return cache_dir
                cache_dir.mkdir(parents=True, exist_ok=True)
                for f in list(candidate.iterdir()):
                    if f.is_file():
                        dest = cache_dir / f.name
                        if dest.exists():
                            dest.unlink()
                        f.rename(dest)
                return cache_dir
    raise RuntimeError(
        f"Extracted {NEAR_ASSET_NAME} does not contain {CONFIG_WASM} and {PROXY_WASM}"
    )


def ensure_calimero_near_contracts(version: str = "0.6.0") -> str:
    """
    Ensure NEAR context contracts are available; download from GitHub release if needed.

    Returns the path to a directory containing calimero_context_config_near.wasm
    and calimero_context_proxy_near.wasm. Uses ~/.merobox/contracts/<version>/near
    as cache. Version can be overridden via env CALIMERO_CONTRACTS_VERSION.
    """
    version = os.environ.get("CALIMERO_CONTRACTS_VERSION", version)
    cache_dir = _contracts_cache_base() / version / "near"

    if _dir_has_contracts(cache_dir):
        return str(cache_dir)

    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir.parent / ".contracts.lock"

    def _download_and_extract() -> str:
        # Re-check after acquiring lock (another process may have finished)
        if _dir_has_contracts(cache_dir):
            return str(cache_dir)
        return _download_and_extract_impl(cache_dir, version)

    if FileLock is not None:
        with FileLock(lock_path, timeout=CONTRACT_DOWNLOAD_LOCK_TIMEOUT):
            return _download_and_extract()
    _warn_no_filelock()
    return _download_and_extract()


def _download_and_extract_impl(cache_dir: Path, version: str) -> str:
    """Download and extract contracts; caller should hold lock if available."""
    if _dir_has_contracts(cache_dir):
        return str(cache_dir)

    api_url = (
        f"https://api.github.com/repos/{CONTRACTS_REPO_OWNER}/{CONTRACTS_REPO_NAME}"
        f"/releases/tags/{version}"
    )
    if version == "latest":
        api_url = (
            f"https://api.github.com/repos/{CONTRACTS_REPO_OWNER}/{CONTRACTS_REPO_NAME}"
            "/releases/latest"
        )

    console.print(f"[yellow]Fetching Calimero NEAR contracts {version}...[/yellow]")
    try:
        r = requests.get(api_url, timeout=CONTRACT_DOWNLOAD_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch release {version}: {e}") from e

    assets = data.get("assets") or []
    download_url = None
    for a in assets:
        if a.get("name") == NEAR_ASSET_NAME:
            download_url = a.get("browser_download_url")
            break
    if not download_url:
        raise RuntimeError(
            f"Release {version} has no asset '{NEAR_ASSET_NAME}'. "
            f"Available: {[a.get('name') for a in assets]}"
        )

    checksum_url = _get_checksum_url(assets)
    tar_path = cache_dir.parent / NEAR_ASSET_NAME
    console.print(f"[yellow]Downloading {NEAR_ASSET_NAME}...[/yellow]")
    try:
        with requests.get(download_url, stream=True, timeout=(10, 60)) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0) or 0)
            if total > MAX_DOWNLOAD_BYTES:
                raise RuntimeError(
                    f"Contract tarball too large ({total} bytes > {MAX_DOWNLOAD_BYTES} limit). "
                    "Refusing to download to avoid disk exhaustion."
                )
            downloaded = 0
            with (
                open(tar_path, "wb") as f,
                tqdm(
                    desc="Downloading",
                    total=total if total > 0 else None,
                    unit="iB",
                    unit_scale=True,
                    unit_divisor=1024,
                ) as bar,
            ):
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_BYTES:
                            _safe_unlink(tar_path)
                            raise RuntimeError(
                                f"Contract tarball exceeds size limit (received {downloaded} bytes > {MAX_DOWNLOAD_BYTES} limit). "
                                "Refusing to continue to avoid disk exhaustion."
                            )
                        f.write(chunk)
                        bar.update(len(chunk))
    except requests.RequestException as e:
        _safe_unlink(tar_path)
        raise RuntimeError(
            f"Failed to download contracts ({type(e).__name__}): {e}"
        ) from e

    skip_checksum = os.environ.get(
        "MEROBOX_SKIP_CONTRACTS_CHECKSUM", ""
    ).strip().lower() in ("1", "true", "yes")
    if skip_checksum and checksum_url:
        console.print(
            "[bold red]SECURITY: MEROBOX_SKIP_CONTRACTS_CHECKSUM is set; downloaded contracts are NOT verified. "
            "Tampered or compromised WASM could be executed. Use only in trusted environments.[/bold red]"
        )
    if checksum_url and not skip_checksum:
        try:
            with requests.get(
                checksum_url, timeout=CONTRACT_DOWNLOAD_TIMEOUT
            ) as cs_resp:
                cs_resp.raise_for_status()
                line = (cs_resp.text or "").strip().split()
                expected_hex = line[0] if line else ""
                if len(expected_hex) != 64 or not all(
                    c in "0123456789abcdefABCDEF" for c in expected_hex
                ):
                    raise RuntimeError(
                        "Checksum file format invalid (expected 64 hex chars), cannot verify contracts"
                    )
                _verify_sha256(tar_path, expected_hex)
        except requests.RequestException as e:
            _safe_unlink(tar_path)
            raise RuntimeError(
                f"Checksum verification failed ({type(e).__name__}): {e}. "
                "Set MEROBOX_SKIP_CONTRACTS_CHECKSUM=1 to skip (not recommended; see security warning)."
            ) from e
        except RuntimeError:
            _safe_unlink(tar_path)
            raise
    elif checksum_url is None:
        console.print(
            "[yellow]Warning: No checksum file in release; contract integrity not verified[/yellow]"
        )

    console.print("[yellow]Extracting...[/yellow]")
    temp_dir = Path(tempfile.mkdtemp(dir=cache_dir.parent))
    try:
        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
            safe_tar_extract(tar, temp_dir)
        _safe_unlink(tar_path)
        result = _locate_contracts_after_extract(temp_dir, cache_dir, names)
    except (tarfile.TarError, OSError, RuntimeError) as e:
        _safe_unlink(tar_path)
        raise RuntimeError(
            f"Failed to extract contracts ({type(e).__name__}): {e}"
        ) from e
    except Exception as e:
        _safe_unlink(tar_path)
        raise RuntimeError(
            f"Unexpected error after extract ({type(e).__name__}): {e}"
        ) from e
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    console.print("[green]✓ Calimero NEAR contracts ready[/green]")
    return str(result)

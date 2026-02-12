"""
Fetch and cache Calimero NEAR contracts from GitHub releases.

When using near_devnet, merobox can automatically download the context config
and proxy WASM contracts from the calimero-network/contracts release so that
users do not need to run a separate download script or provide --contracts-dir.
"""

import hashlib
import os
import tarfile
from pathlib import Path

import requests
from rich.console import Console
from tqdm import tqdm

try:
    from filelock import FileLock
except ImportError:
    FileLock = None

console = Console()


def _warn_no_filelock():
    if FileLock is None:
        console.print(
            "[yellow]Warning: filelock not available, concurrent downloads may conflict[/yellow]"
        )


CONTRACTS_REPO_OWNER = "calimero-network"
CONTRACTS_REPO_NAME = "contracts"
NEAR_ASSET_NAME = "near.tar.gz"
CONFIG_WASM = "calimero_context_config_near.wasm"
PROXY_WASM = "calimero_context_proxy_near.wasm"


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


def _safe_tar_extract(tar: tarfile.TarFile, extract_base: Path) -> None:
    """Extract tar members into extract_base, rejecting path traversal and symlinks."""
    base_resolved = extract_base.resolve()
    for member in tar.getmembers():
        if member.issym() or member.islnk():
            continue  # Skip symlinks/hardlinks; only regular files/dirs needed
        dest = (extract_base / member.name).resolve()
        try:
            dest.relative_to(base_resolved)
        except ValueError:
            raise RuntimeError(
                f"Rejected path traversal in archive: member name {member.name!r}"
            ) from None
        tar.extract(member, path=extract_base)


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
        with FileLock(lock_path, timeout=300):
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
        r = requests.get(api_url, timeout=30)
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
        with requests.get(download_url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            with (
                open(tar_path, "wb") as f,
                tqdm(
                    desc="Downloading",
                    total=total,
                    unit="iB",
                    unit_scale=True,
                    unit_divisor=1024,
                ) as bar,
            ):
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        bar.update(len(chunk))
    except requests.RequestException as e:
        if tar_path.exists():
            try:
                tar_path.unlink()
            except OSError:
                pass
        raise RuntimeError(
            f"Failed to download contracts ({type(e).__name__}): {e}"
        ) from e

    if checksum_url:
        try:
            with requests.get(checksum_url, timeout=30) as cs_resp:
                cs_resp.raise_for_status()
                line = (cs_resp.text or "").strip().split()
                expected_hex = line[0] if line else ""
                if len(expected_hex) != 64 or not all(
                    c in "0123456789abcdefABCDEF" for c in expected_hex
                ):
                    console.print(
                        "[yellow]Warning: checksum file format invalid, proceeding without verification[/yellow]"
                    )
                else:
                    _verify_sha256(tar_path, expected_hex)
        except requests.RequestException as e:
            console.print(
                "[yellow]Warning: Could not verify checksum, proceeding without verification[/yellow]"
            )
            console.print(f"[dim]{type(e).__name__}: {e}[/dim]")
        except RuntimeError:
            if tar_path.exists():
                try:
                    tar_path.unlink()
                except OSError:
                    pass
            raise

    console.print("[yellow]Extracting...[/yellow]")
    try:
        extract_base = Path(cache_dir.parent)
        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
            _safe_tar_extract(tar, extract_base)
        tar_path.unlink(missing_ok=True)
    except (tarfile.TarError, OSError, RuntimeError) as e:
        if tar_path.exists():
            try:
                tar_path.unlink()
            except OSError:
                pass
        raise RuntimeError(
            f"Failed to extract contracts ({type(e).__name__}): {e}"
        ) from e

    # Find dir that contains both wasm files; normalize to cache_dir so next call hits cache
    try:
        if _dir_has_contracts(cache_dir):
            pass
        elif _dir_has_contracts(extract_base):
            cache_dir.mkdir(parents=True, exist_ok=True)
            for f in extract_base.iterdir():
                # Only move regular files; skip dirs (e.g. "near") to avoid moving X into X/near
                if f.is_dir() or f.name == cache_dir.name:
                    continue
                if (
                    f.is_file()
                    and f.name != NEAR_ASSET_NAME
                    and not f.name.startswith(".")
                ):
                    dest = cache_dir / f.name
                    if not dest.exists():
                        f.rename(dest)
        else:
            # Check for single top-level folder (e.g. "near" or "near-0.5.0")
            for name in names:
                if "/" not in name.strip("/"):
                    candidate = extract_base / name.strip("/")
                    if candidate.is_dir() and _dir_has_contracts(candidate):
                        cache_dir = candidate
                        break
            else:
                raise RuntimeError(
                    f"Extracted {NEAR_ASSET_NAME} does not contain "
                    f"{CONFIG_WASM} and {PROXY_WASM}"
                )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            f"Unexpected error after extract ({type(e).__name__}): {e}"
        ) from e

    console.print("[green]✓ Calimero NEAR contracts ready[/green]")
    return str(cache_dir)

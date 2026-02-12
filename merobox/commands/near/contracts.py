"""
Fetch and cache Calimero NEAR contracts from GitHub releases.

When using near_devnet, merobox can automatically download the context config
and proxy WASM contracts from the calimero-network/contracts release so that
users do not need to run a separate download script or provide --contracts-dir.
"""

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

CONTRACTS_REPO_OWNER = "calimero-network"
CONTRACTS_REPO_NAME = "contracts"
NEAR_ASSET_NAME = "near.tar.gz"
CONFIG_WASM = "calimero_context_config_near.wasm"
PROXY_WASM = "calimero_context_proxy_near.wasm"


def _contracts_cache_base():
    return Path.home() / ".merobox" / "contracts"


def _dir_has_contracts(dir_path: Path) -> bool:
    return (dir_path / CONFIG_WASM).is_file() and (dir_path / PROXY_WASM).is_file()


def _safe_tar_extract(tar: tarfile.TarFile, extract_base: Path) -> None:
    """Extract tar members into extract_base, rejecting path traversal (tar slip)."""
    base_resolved = extract_base.resolve()
    for member in tar.getmembers():
        # Resolve destination path and ensure it stays under extract_base
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

    console.print(f"[yellow]Downloading {NEAR_ASSET_NAME}...[/yellow]")
    try:
        resp = requests.get(download_url, stream=True, timeout=60)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        tar_path = cache_dir.parent / NEAR_ASSET_NAME
        with open(tar_path, "wb") as f, tqdm(
            desc="Downloading",
            total=total,
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
        ) as bar:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    bar.update(len(chunk))

        console.print("[yellow]Extracting...[/yellow]")
        extract_base = Path(cache_dir.parent)
        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
            _safe_tar_extract(tar, extract_base)
        tar_path.unlink(missing_ok=True)

        # Find dir that contains both wasm files
        if _dir_has_contracts(cache_dir):
            pass
        elif _dir_has_contracts(extract_base):
            cache_dir = extract_base
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

        console.print("[green]✓ Calimero NEAR contracts ready[/green]")
        return str(cache_dir)
    except Exception as e:
        if cache_dir.parent.exists():
            try:
                import shutil
                shutil.rmtree(cache_dir.parent, ignore_errors=True)
            except Exception:
                pass
        raise RuntimeError(f"Failed to download or extract contracts: {e}") from e

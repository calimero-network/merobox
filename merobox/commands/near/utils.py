"""Shared utilities for the NEAR commands module."""

import tarfile
from pathlib import Path

from rich.console import Console

console = Console()


def safe_tar_extract(tar: tarfile.TarFile, extract_base: Path) -> None:
    """Extract tar members into extract_base, rejecting path traversal and special files.

    Validates each member before extraction to prevent zip-slip vulnerabilities
    where malicious archives contain paths like '../../../etc/passwd'.

    Only regular files and directories are extracted. Symlinks, hardlinks,
    device files, and FIFOs are skipped for security.

    Args:
        tar: The tarfile object to extract from.
        extract_base: The base directory to extract files into.

    Raises:
        RuntimeError: If a member attempts path traversal outside the extraction directory.
    """
    base_resolved = extract_base.resolve()
    for member in tar.getmembers():
        # Only allow regular files and directories
        if not (member.isfile() or member.isdir()):
            console.print(
                f"[yellow]Warning: skipping special file in archive: {member.name!r}[/yellow]"
            )
            continue
        dest = (extract_base / member.name).resolve()
        try:
            dest.relative_to(base_resolved)
        except ValueError:
            raise RuntimeError(
                f"Rejected path traversal in archive: member name {member.name!r}"
            ) from None
        tar.extract(member, path=extract_base)

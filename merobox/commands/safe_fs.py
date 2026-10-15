"""Host writes and deletes merobox makes for a run, refusing the symlinks a
checked-out repository can plant in the paths it writes through."""

import os
import shutil
from typing import IO, Optional, Union

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)  # absent on Windows

PathLike = Union[str, "os.PathLike[str]"]


class SymlinkedPathError(OSError):
    """A path merobox was about to write or delete runs through a symlink."""


def refuse_symlinks(
    path: PathLike, root: Optional[PathLike] = None, final: bool = True
) -> None:
    """Raise SymlinkedPathError if a component of `path` below the cwd or `root`
    is a symlink; `final=False` skips the last component. Call right before use."""
    absolute = os.path.abspath(path)
    bases = {os.getcwd()} | ({os.path.abspath(root)} if root else set())
    for base in bases:
        if not absolute.startswith(base.rstrip(os.sep) + os.sep):
            continue
        parts = os.path.relpath(absolute, base).split(os.sep)
        current = base
        for part in parts if final else parts[:-1]:
            current = os.path.join(current, part)
            if os.path.islink(current):
                raise SymlinkedPathError(
                    f"refusing to use {path}: {current} is a symlink"
                )


def open_for_write(
    path: PathLike,
    binary: bool = False,
    perms: int = 0o666,
    root: Optional[PathLike] = None,
    append: bool = False,
) -> IO:
    """Open `path` to write (truncating, or appending), never through a symlink."""
    refuse_symlinks(path, root, final=False)
    flags = os.O_WRONLY | os.O_CREAT | O_NOFOLLOW
    fd = os.open(path, flags | (os.O_APPEND if append else os.O_TRUNC), perms)
    mode = ("a" if append else "w") + ("b" if binary else "")
    return os.fdopen(fd, mode) if binary else os.fdopen(fd, mode, encoding="utf-8")


def copy_file(src: PathLike, dst: PathLike, root: Optional[PathLike] = None) -> None:
    with open(src, "rb") as source, open_for_write(dst, binary=True, root=root) as out:
        shutil.copyfileobj(source, out)


def remove(path: PathLike, root: Optional[PathLike] = None) -> None:
    """Remove `path`; a symlink there is removed itself, never its target."""
    refuse_symlinks(path, root, final=False)
    os.remove(path)


def rmtree(path: PathLike, root: Optional[PathLike] = None) -> None:
    refuse_symlinks(path, root)
    shutil.rmtree(path)

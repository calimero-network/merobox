"""Host writes and deletes merobox makes for a run, refusing the symlinks a
checked-out repository (or a node writing its mounted data dir) can plant."""

import contextlib
import os
import shutil
import stat
from collections.abc import Iterator
from typing import IO, Optional, Union

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)  # absent on Windows
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
FD_WALK = os.open in os.supports_dir_fd  # False on Windows: fall back to lstat checks

PathLike = Union[str, "os.PathLike[str]"]


class SymlinkedPathError(OSError):
    """A path merobox was about to write or delete runs through a symlink."""


def refuse_symlinks(
    path: PathLike, root: Optional[PathLike] = None, final: bool = True
) -> None:
    """Raise SymlinkedPathError if a component of `path` below the cwd or `root`
    is a symlink; `final=False` skips the last component."""
    for base, parts in _below_bases(path, root):
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
    flags = os.O_WRONLY | os.O_CREAT | O_NOFOLLOW
    flags |= os.O_APPEND if append else os.O_TRUNC
    with _parent(path, root) as (parent_fd, name):
        fd = os.open(name, flags, perms, dir_fd=parent_fd)
    mode = ("a" if append else "w") + ("b" if binary else "")
    return os.fdopen(fd, mode) if binary else os.fdopen(fd, mode, encoding="utf-8")


def copy_file(src: PathLike, dst: PathLike, root: Optional[PathLike] = None) -> None:
    with open(src, "rb") as source, open_for_write(dst, binary=True, root=root) as out:
        shutil.copyfileobj(source, out)


def makedirs(
    path: PathLike, mode: int = 0o777, root: Optional[PathLike] = None
) -> None:
    """os.makedirs that refuses to create through, or end at, a symlink."""
    with _parent(path, root, create=True, mode=mode) as (parent_fd, name):
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, mode, dir_fd=parent_fd)
        if not stat.S_ISDIR(
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode
        ):
            raise SymlinkedPathError(f"refusing to use {path}: not a plain directory")


def remove(path: PathLike, root: Optional[PathLike] = None) -> None:
    """Remove `path`; a symlink there is removed itself, never its target."""
    with _parent(path, root) as (parent_fd, name):
        os.unlink(name, dir_fd=parent_fd)


def rmtree(path: PathLike, root: Optional[PathLike] = None) -> None:
    """shutil.rmtree that never follows a symlink, at the top or inside."""
    with _parent(path, root) as (parent_fd, name):
        if stat.S_ISLNK(os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode):
            raise SymlinkedPathError(f"refusing to delete {path}: it is a symlink")
        if parent_fd is None:
            shutil.rmtree(path)
            return
        for _, dirs, files, dir_fd in os.fwalk(name, topdown=False, dir_fd=parent_fd):
            for entry in files + dirs:
                mode = os.stat(entry, dir_fd=dir_fd, follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    os.rmdir(entry, dir_fd=dir_fd)
                else:
                    os.unlink(entry, dir_fd=dir_fd)
        os.rmdir(name, dir_fd=parent_fd)


@contextlib.contextmanager
def _parent(
    path: PathLike,
    root: Optional[PathLike],
    create: bool = False,
    mode: int = 0o777,
) -> Iterator[tuple[Optional[int], str]]:
    """Yield (fd of `path`'s directory, its last name), opening each directory
    below the cwd or `root` with O_NOFOLLOW so none can be a symlink."""
    below = next(iter(_below_bases(path, root)), None)
    if below is None or not FD_WALK:
        refuse_symlinks(path, root, final=False)
        yield None, os.fspath(path)
        return
    base, parts = below
    fd = os.open(base, os.O_RDONLY | O_DIRECTORY)
    try:
        for part in parts[:-1]:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, mode, dir_fd=fd)
            try:
                next_fd = os.open(
                    part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=fd
                )
            except OSError as error:
                raise SymlinkedPathError(
                    f"refusing to use {path}: {part} is a symlink or not a directory"
                ) from error
            os.close(fd)
            fd = next_fd
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _below_bases(path: PathLike, root: Optional[PathLike]):
    """(base, components of `path` below it) for the cwd and `root` that hold it."""
    absolute = os.path.abspath(path)
    bases = [os.getcwd()] + ([os.path.abspath(root)] if root else [])
    for base in bases:
        if absolute.startswith(base.rstrip(os.sep) + os.sep):
            yield base, os.path.relpath(absolute, base).split(os.sep)

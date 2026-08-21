#!/usr/bin/env python3
"""
Setup script for merobox package.
"""

import re
from pathlib import Path

from setuptools import find_packages, setup

# Read the README file
with open("README.md", encoding="utf-8") as fh:
    long_description = fh.read()

# Read version from merobox/__init__.py
init_file = Path(__file__).parent / "merobox" / "__init__.py"
version_match = re.search(
    r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', init_file.read_text(), re.MULTILINE
)
if not version_match:
    raise RuntimeError("Unable to find version string in merobox/__init__.py")
version = version_match.group(1)

setup(
    name="merobox",
    version=version,
    author="Merobox Team",
    author_email="team@merobox.com",
    description="A Python CLI tool for managing Calimero nodes in Docker containers",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/merobox/merobox",
    packages=find_packages(include=["merobox*"]),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Systems Administration",
        "Topic :: Utilities",
    ],
    python_requires=">=3.9",
    # Dependencies are declared ONLY in pyproject.toml.
    #
    # This file used to carry a second copy in `install_requires`, which the
    # build backend ignores (PEP 621: with a [project] table, pyproject wins).
    # The copy drifted anyway — at the 0.6.41 release it pinned
    # calimero-client-py>=0.6.11 while pyproject said >=0.6.19, and it still
    # listed ed25519 and py-near, which nothing imports and no release has
    # shipped since the move to pyproject. Dead metadata that disagrees with
    # the real thing is worse than none: it invites a fix to the wrong file.
    extras_require={
        "dev": [
            "build",
            "twine",
            "pytest",
            "pytest-asyncio",
            "black",
            "flake8",
            "mypy",
        ],
    },
    entry_points={
        "console_scripts": [
            "merobox=merobox.cli:main",
        ],
    },
    include_package_data=True,
    package_data={},
    exclude_package_data={
        "*": [
            "*.pyc",
            "__pycache__",
            "*.pyo",
            "*.pyd",
            ".git*",
            "venv*",
            ".venv*",
            "data*",
        ],
    },
    zip_safe=False,
)

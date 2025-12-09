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
    python_requires=">=3.9,<3.12",
    install_requires=[
        "click>=8.0.0",
        "docker>=6.0.0",
        "rich>=13.0.0",
        "PyYAML>=6.0.0",
        "calimero-client-py==0.2.7",
        "aiohttp>=3.8.0",
        "toml>=0.10.2",
        "base58>=2.1.0",
        "ed25519>=1.5",
        "py-near>=1.1.0",
        "requests>=2.31.0",
        "tqdm>=4.65.0",
    ],
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

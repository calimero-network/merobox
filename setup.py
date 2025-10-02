#!/usr/bin/env python3
"""
Setup script for merobox package.
"""

from setuptools import setup, find_packages
import toml

# Read the README file
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# Read version from pyproject.toml
with open("pyproject.toml", "r", encoding="utf-8") as fh:
    pyproject = toml.load(fh)
    version = pyproject["project"]["version"]

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
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Systems Administration",
        "Topic :: Utilities",
    ],
    python_requires=">=3.9",
    install_requires=[
        "click>=8.0.0",
        "docker>=6.0.0",
        "rich>=13.0.0",
        "PyYAML>=6.0.0",
        "calimero-client-py==0.2.3",
        "aiohttp>=3.8.0",
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

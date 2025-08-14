#!/usr/bin/env python3
"""
Setup script for Merobox package.
"""

from setuptools import setup, find_packages
import os

# Read the README file
def read_readme():
    with open("README.md", "r", encoding="utf-8") as fh:
        return fh.read()

# Read requirements
def read_requirements():
    with open("requirements.txt", "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="merobox",
    version="0.1.4",
    author="Merobox Team",
    author_email="team@merobox.com",
    description="A Python CLI tool for managing Calimero nodes in Docker containers",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/merobox/merobox",
    packages=find_packages(include=["commands*"]),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Systems Administration",
        "Topic :: Utilities",
    ],
    python_requires=">=3.8",
    install_requires=read_requirements(),
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
            "merobox=merobox.cli:cli",
        ],
    },
    include_package_data=True,
    package_data={
        "": ["*.wasm", "*.yml", "*.md"],
    },
    exclude_package_data={
        "": ["*.pyc", "__pycache__", "*.pyo", "*.pyd", ".git*", "venv*", ".venv*", "data*"],
    },
    keywords="calimero,blockchain,docker,cli,workflow",
    project_urls={
        "Homepage": "https://github.com/merobox/merobox",
        "Documentation": "https://github.com/merobox/merobox#readme",
        "Repository": "https://github.com/merobox/merobox",
        "Issues": "https://github.com/merobox/merobox/issues",
        "Changelog": "https://github.com/merobox/merobox/blob/main/CHANGELOG.md",
    },
)

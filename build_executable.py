#!/usr/bin/env python3
"""
Build script for creating merobox executables with PyInstaller.
"""

import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path

def get_platform_info():
    """Get platform information for naming."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    if system == "darwin":
        if machine in ["arm64", "aarch64"]:
            return "darwin-arm64"
        else:
            return "darwin-x64"
    elif system == "linux":
        if machine in ["arm64", "aarch64"]:
            return "linux-arm64"
        else:
            return "linux-x64"
    else:
        return f"{system}-{machine}"

def build_executable():
    """Build the merobox executable."""
    print("Building merobox executable...")
    
    # Clean previous builds
    if os.path.exists("dist"):
        shutil.rmtree("dist")
    if os.path.exists("build"):
        shutil.rmtree("build")
    
    # Build with PyInstaller
    cmd = [
        "python3", "-m", "pyinstaller",
        "--onefile",
        "--name", "merobox",
        "--specpath", ".",
        "merobox/cli.py"
    ]
    
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True)
    
    # Test the executable
    platform_info = get_platform_info()
    exe_name = "merobox"
    exe_path = Path("dist") / exe_name
    
    if exe_path.exists():
        print(f"Testing executable: {exe_path}")
        test_result = subprocess.run([str(exe_path), "--version"], 
                                   capture_output=True, text=True)
        if test_result.returncode == 0:
            print(f"✓ Build successful: {test_result.stdout.strip()}")
        else:
            print(f"✗ Build test failed: {test_result.stderr}")
            return False
    else:
        print(f"✗ Executable not found: {exe_path}")
        return False
    
    # Generate checksum
    import hashlib
    with open(exe_path, 'rb') as f:
        content = f.read()
        checksum = hashlib.sha256(content).hexdigest()
    
    checksum_file = Path("dist") / f"merobox-{platform_info}.sha256"
    with open(checksum_file, 'w') as f:
        f.write(f"{checksum}  {exe_name}\n")
    
    print(f"✓ Checksum generated: {checksum_file}")
    print(f"✓ Build complete for {platform_info}")
    return True

if __name__ == "__main__":
    success = build_executable()
    sys.exit(0 if success else 1)

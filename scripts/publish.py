#!/usr/bin/env python3
"""
PyPI Publishing Script for Merobox
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path

def run_command(cmd, check=True, capture_output=False):
    """Run a shell command."""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, check=check, capture_output=capture_output, text=True)
    if capture_output:
        return result.stdout.strip()
    return result

def clean_build_files():
    """Clean up build artifacts."""
    print("🧹 Cleaning build files...")
    run_command("rm -rf build/ dist/ *.egg-info/")

def build_package():
    """Build the package."""
    print("🔨 Building package...")
    run_command("python -m build")

def check_package():
    """Check the built package."""
    print("✅ Checking package...")
    run_command("twine check dist/*")

def upload_to_test_pypi():
    """Upload to TestPyPI."""
    print("🚀 Uploading to TestPyPI...")
    run_command("twine upload --repository testpypi dist/*")

def upload_to_pypi():
    """Upload to PyPI."""
    print("🚀 Uploading to PyPI...")
    run_command("twine upload dist/*")

def main():
    parser = argparse.ArgumentParser(description="Publish Merobox to PyPI")
    parser.add_argument("--test", action="store_true", help="Upload to TestPyPI only")
    parser.add_argument("--clean", action="store_true", help="Clean build files before building")
    parser.add_argument("--check-only", action="store_true", help="Only check the package, don't upload")
    
    args = parser.parse_args()
    
    # Ensure we're in the right directory
    script_dir = Path(__file__).parent
    project_dir = script_dir.parent
    os.chdir(project_dir)
    
    print(f"📦 Publishing Merobox from {project_dir}")
    
    try:
        if args.clean:
            clean_build_files()
        
        build_package()
        check_package()
        
        if args.check_only:
            print("✅ Package check completed successfully!")
            return
        
        if args.test:
            upload_to_test_pypi()
            print("✅ Package uploaded to TestPyPI successfully!")
        else:
            # Ask for confirmation before uploading to PyPI
            response = input("Are you sure you want to upload to PyPI? (y/N): ")
            if response.lower() in ['y', 'yes']:
                upload_to_pypi()
                print("✅ Package uploaded to PyPI successfully!")
            else:
                print("❌ Upload cancelled")
                
    except subprocess.CalledProcessError as e:
        print(f"❌ Command failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n❌ Publishing cancelled by user")
        sys.exit(1)

if __name__ == "__main__":
    main()

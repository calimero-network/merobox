#!/bin/bash
# Build .deb package from PyInstaller binary
# Usage: ./build-deb.sh <version> <binary-path> <architecture>

set -euo pipefail

VERSION="${1:?Version required (e.g., 1.2.3)}"
BINARY_PATH="${2:?Binary path required}"
ARCH="${3:?Architecture required (amd64 or arm64)}"

DEB_REVISION="1"
DEB_VERSION="${VERSION}-${DEB_REVISION}"
PACKAGE_NAME="merobox"
MAINTAINER="Calimero Ltd."
HOMEPAGE="https://github.com/calimero-network/merobox"
DESCRIPTION="A CLI tool for managing Calimero nodes"
OUTPUT_DIR="${OUTPUT_DIR:-.}"

STAGING_DIR=$(mktemp -d)
trap "rm -rf ${STAGING_DIR}" EXIT

mkdir -p "${STAGING_DIR}/DEBIAN"
mkdir -p "${STAGING_DIR}/usr/bin"
mkdir -p "${STAGING_DIR}/usr/share/doc/${PACKAGE_NAME}"

cp "${BINARY_PATH}" "${STAGING_DIR}/usr/bin/${PACKAGE_NAME}"
chmod 755 "${STAGING_DIR}/usr/bin/${PACKAGE_NAME}"

cat > "${STAGING_DIR}/DEBIAN/control" << EOF
Package: ${PACKAGE_NAME}
Version: ${DEB_VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: ${MAINTAINER}
Homepage: ${HOMEPAGE}
Depends: libc6
Description: ${DESCRIPTION}
 Merobox is a command-line tool for managing Calimero network nodes.
 It provides commands for installing,
 configuring, and managing node lifecycles.
EOF

cat > "${STAGING_DIR}/usr/share/doc/${PACKAGE_NAME}/copyright" << EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ${PACKAGE_NAME}
Upstream-Contact: ${MAINTAINER}
Source: ${HOMEPAGE}

Files: *
Copyright: $(date +%Y) Calimero Ltd.
License: MIT

License: MIT
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all
 copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
EOF

cat > "${STAGING_DIR}/usr/share/doc/${PACKAGE_NAME}/changelog.Debian" << EOF
${PACKAGE_NAME} (${DEB_VERSION}) stable; urgency=medium

  * Release version ${VERSION}

 -- ${MAINTAINER}  $(date -R)
EOF
gzip -9 "${STAGING_DIR}/usr/share/doc/${PACKAGE_NAME}/changelog.Debian"

DEB_FILE="${OUTPUT_DIR}/${PACKAGE_NAME}_${DEB_VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "${STAGING_DIR}" "${DEB_FILE}"

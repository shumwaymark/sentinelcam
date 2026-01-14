#!/bin/bash
# Download Coral EdgeTPU packages from feranick's GitHub releases
# 
# This script downloads the pre-built libedgetpu, pycoral, and tflite_runtime
# packages for arm64/Python 3.11 from feranick's community ports.
#
# Usage:
#   ./download_coral_packages.sh [destination_dir]
#
# Default destination: /home/ops/sentinelcam/model_registry/coral_packages
#
# Package Sources:
#   - https://github.com/feranick/libedgetpu
#   - https://github.com/feranick/pycoral
#   - https://github.com/feranick/TFlite-builds

set -e

# Configuration - these should match model_registry.yaml
TF_VERSION="2.17.1"
PYTHON_VERSION="3.11"

# libedgetpu packages
LIBEDGETPU_VERSION="16.0tf2.17.1-1"
LIBEDGETPU_RELEASE="16.0TF2.17.1-1"
LIBEDGETPU_DEB="libedgetpu1-std_${LIBEDGETPU_VERSION}.bookworm_arm64.deb"
LIBEDGETPU_DEV_DEB="libedgetpu-dev_${LIBEDGETPU_VERSION}.bookworm_arm64.deb"
LIBEDGETPU_URL="https://github.com/feranick/libedgetpu/releases/download/${LIBEDGETPU_RELEASE}"

# pycoral package  
PYCORAL_VERSION="2.0.3"
PYCORAL_RELEASE="2.0.3TF2.17.1"
PYCORAL_WHEEL="pycoral-${PYCORAL_VERSION}-cp311-cp311-linux_aarch64.whl"
PYCORAL_URL="https://github.com/feranick/pycoral/releases/download/${PYCORAL_RELEASE}"

# tflite_runtime package
TFLITE_VERSION="2.17.1"
TFLITE_RELEASE="v${TFLITE_VERSION}"
TFLITE_WHEEL="tflite_runtime-${TFLITE_VERSION}-cp311-cp311-linux_aarch64.whl"
TFLITE_URL="https://github.com/feranick/TFlite-builds/releases/download/${TFLITE_RELEASE}"

# Destination directory
DEST_DIR="${1:-/home/ops/sentinelcam/model_registry/coral_packages}"

echo "========================================"
echo "Coral EdgeTPU Package Downloader"
echo "========================================"
echo "TensorFlow Version: ${TF_VERSION}"
echo "Python Version: ${PYTHON_VERSION}"
echo "Destination: ${DEST_DIR}"
echo ""

# Create destination directory
mkdir -p "${DEST_DIR}"
cd "${DEST_DIR}"

echo "Downloading libedgetpu runtime..."
curl -L -O "${LIBEDGETPU_URL}/${LIBEDGETPU_DEB}"

echo "Downloading libedgetpu-dev..."
curl -L -O "${LIBEDGETPU_URL}/${LIBEDGETPU_DEV_DEB}"

echo "Downloading pycoral wheel..."
curl -L -O "${PYCORAL_URL}/${PYCORAL_WHEEL}"

echo "Downloading tflite_runtime wheel..."
curl -L -O "${TFLITE_URL}/${TFLITE_WHEEL}"

# Create manifest
cat > manifest.yaml << EOF
# Coral EdgeTPU Packages Manifest
# Downloaded: $(date -Iseconds)
# TensorFlow Version: ${TF_VERSION}
# Python Version: ${PYTHON_VERSION}
# Architecture: arm64 (aarch64)
# OS: Debian Bookworm

libedgetpu:
  version: "${LIBEDGETPU_VERSION}"
  runtime: "${LIBEDGETPU_DEB}"
  dev: "${LIBEDGETPU_DEV_DEB}"
  source: "https://github.com/feranick/libedgetpu"

pycoral:
  version: "${PYCORAL_VERSION}"
  wheel: "${PYCORAL_WHEEL}"
  source: "https://github.com/feranick/pycoral"

tflite_runtime:
  version: "${TFLITE_VERSION}"
  wheel: "${TFLITE_WHEEL}"
  source: "https://github.com/feranick/TFlite-builds"

installation:
  libedgetpu: |
    sudo dpkg -i ${LIBEDGETPU_DEB}
    sudo udevadm control --reload-rules && sudo udevadm trigger
  
  python_packages: |
    pip install ${TFLITE_WHEEL}
    pip install ${PYCORAL_WHEEL}
    
  verify: |
    python -c "import tflite_runtime; print(f'tflite_runtime: {tflite_runtime.__version__}')"
    python -c "import pycoral; print('pycoral: imported successfully')"
EOF

echo ""
echo "========================================"
echo "Download Complete!"
echo "========================================"
echo ""
ls -la "${DEST_DIR}"
echo ""
echo "Files downloaded to: ${DEST_DIR}"
echo ""
echo "To install on a target node:"
echo "  1. Copy files to target: scp -r ${DEST_DIR} user@target:/tmp/"
echo "  2. Install libedgetpu:   sudo dpkg -i /tmp/coral_packages/${LIBEDGETPU_DEB}"
echo "  3. Reload udev:          sudo udevadm control --reload-rules && sudo udevadm trigger"
echo "  4. Install Python pkgs:  pip install /tmp/coral_packages/${TFLITE_WHEEL}"
echo "                           pip install /tmp/coral_packages/${PYCORAL_WHEEL}"
echo ""
echo "Or use Ansible: ansible-playbook playbooks/deploy-coral-packages.yaml"

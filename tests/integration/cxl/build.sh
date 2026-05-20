#!/usr/bin/env bash
# Build the QEMU+CXL workload image used by the FpgaSubstrate-style CXL
# boot-flow fixture. Defaults to the host architecture; on Apple Silicon
# the image runs under Docker Desktop's Rosetta translation when targeted
# as linux/amd64. The guest VM inside the image is always x86_64 (qemu
# CXL Type 3 device only exists in qemu-system-x86_64).
set -euo pipefail
cd "$(dirname "$0")"

TAG="${ACT_CXL_QEMU_IMAGE:-act-cxl:qemu}"
# QEMU's CXL Type 3 device is x86_64-only (qemu-system-x86_64). The wrapper
# image must therefore be linux/amd64 too so we get x86_64 busybox, kernel
# modules, and cxl-cli inside the initramfs. On Apple Silicon hosts the
# image runs under Docker Desktop's Rosetta translation.
PLATFORM="${ACT_CXL_PLATFORM:-linux/amd64}"

docker buildx build \
    --platform "${PLATFORM}" \
    --tag "${TAG}" \
    --load \
    .

echo
echo "Built ${TAG} for ${PLATFORM}. To use:"
echo "  export ACT_CXL_QEMU_IMAGE=${TAG}"
echo "  pytest tests/test_reproducibility_runtime_check_e2e.py::test_runtime_check_twice_and_hash_against_real_cxl_cluster"

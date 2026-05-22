#!/usr/bin/env bash
# Build the iverilog workload image used by FpgaSubstrate fixtures.
# Defaults to the host architecture so the resulting image runs natively
# inside k3s (no QEMU emulation — important because k3s under emulated
# amd64 on Apple Silicon hits a "seccomp is not supported" sandbox error
# that blocks workload Pods from starting).
set -euo pipefail
cd "$(dirname "$0")"

TAG="${ACT_FPGA_IVERILOG_IMAGE:-act-fpga:iverilog}"
case "$(uname -m)" in
    arm64|aarch64) PLATFORM="${ACT_FPGA_PLATFORM:-linux/arm64}" ;;
    *)             PLATFORM="${ACT_FPGA_PLATFORM:-linux/amd64}" ;;
esac

docker buildx build \
    --platform "${PLATFORM}" \
    --tag "${TAG}" \
    --load \
    .

echo
echo "Built ${TAG} for ${PLATFORM}. To use:"
echo "  export ACT_FPGA_IVERILOG_IMAGE=${TAG}"
echo "  pytest tests/test_reproducibility_runtime_check_e2e.py::test_runtime_check_twice_and_hash_against_real_fpga_cluster"

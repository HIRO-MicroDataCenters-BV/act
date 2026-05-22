#!/usr/bin/env bash
# Build the riscv64 k3s substrate image. Runs under QEMU binfmt on non-riscv64
# hosts (Docker Desktop on Apple Silicon already has binfmt registered).
set -euo pipefail
cd "$(dirname "$0")"

TAG="${ACT_K3S_RISCV64_IMAGE:-act-k3s:riscv64}"

docker buildx build \
    --platform linux/riscv64 \
    --tag "${TAG}" \
    --load \
    .

echo
echo "Built ${TAG}. To use:"
echo "  export ACT_K3S_RISCV64_IMAGE=${TAG}"
echo "  pytest tests/test_reproducibility_substrates_docker_e2e.py::test_e2e_riscv64_k3s_cluster_provisions_and_serves_kubeconfig"

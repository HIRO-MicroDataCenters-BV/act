# Workload image for CxlSubstrate's boot-flow fixture.
# Boots a real Linux 6.8 guest under qemu-system-x86_64 with a CXL Type 3
# memory device, runs `cxl list -v` inside the guest, prints the topology
# to the serial console, and halts. ACT captures the printed topology via
# probe_k8s_with_workload_logs and hashes it for twice-and-hash.
#
# Multi-stage build:
#   stage 1 (builder): install Ubuntu kernel + assemble a minimal initramfs
#                      (busybox + cxl-cli + CXL kernel modules) via
#                      build-initramfs.sh
#   stage 2 (runtime): ship qemu-system-x86 + the kernel + initramfs

FROM ubuntu:24.04 AS builder
SHELL ["/bin/bash", "-c"]

# `cxl` (universe) provides /usr/bin/cxl + libcxl1; linux-image-generic
# provides the bzImage and CXL kernel modules.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        cxl linux-image-generic busybox-static cpio gzip zstd && \
    rm -rf /var/lib/apt/lists/*

# Pin the installed kernel version.
RUN KVER=$(ls /lib/modules) && echo "$KVER" > /tmp/kver && \
    cp "/boot/vmlinuz-$KVER" /opt/vmlinuz

COPY build-initramfs.sh /usr/local/bin/build-initramfs.sh
RUN chmod +x /usr/local/bin/build-initramfs.sh && /usr/local/bin/build-initramfs.sh


# -- runtime stage --
FROM ubuntu:24.04

RUN apt-get update && \
    apt-get install -y --no-install-recommends qemu-system-x86 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/vmlinuz /opt/cxl-guest/vmlinuz
COPY --from=builder /opt/initrd.cpio.gz /opt/cxl-guest/initrd.cpio.gz

COPY entrypoint.sh /usr/local/bin/cxl-boot
RUN chmod +x /usr/local/bin/cxl-boot

ENTRYPOINT ["/usr/local/bin/cxl-boot"]

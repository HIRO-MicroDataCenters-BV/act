#!/bin/sh
# Boot a Linux 6.5+ guest under qemu-system-x86_64 with a CXL Type 3 memory
# device on a pxb-cxl root port. The init script inside the initramfs loads
# the CXL kernel modules, runs `cxl list -v`, prints the topology, and halts.
#
# qemu-system-x86_64 runs under software emulation by default (no -enable-kvm
# / no -accel flag) — works on any Docker host including macOS without
# requiring /dev/kvm passthrough. Slower than KVM, but deterministic.
set -eu

exec qemu-system-x86_64 \
    -machine q35,cxl=on \
    -m 1G \
    -cpu max \
    -smp 1 \
    -object memory-backend-ram,id=cxl-mem0,size=256M,share=on \
    -device pxb-cxl,id=cxl.0,bus=pcie.0 \
    -device cxl-rp,id=rp0,bus=cxl.0,chassis=0,slot=0,port=0 \
    -device cxl-type3,id=cxl-mem0d,volatile-memdev=cxl-mem0,bus=rp0 \
    -M cxl-fmw.0.targets.0=cxl.0,cxl-fmw.0.size=256M \
    -kernel /opt/cxl-guest/vmlinuz \
    -initrd /opt/cxl-guest/initrd.cpio.gz \
    -nographic \
    -no-reboot \
    -serial mon:stdio \
    -append "console=ttyS0 nokaslr panic=1 loglevel=4"

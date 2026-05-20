#!/bin/bash
# Assemble the initramfs that boots inside qemu-system-x86_64 and runs
# `cxl list -v` against the cxl-type3 device.
#
# Runs at image-build time inside the `builder` stage. The output is
# /opt/initrd.cpio.gz.
set -u  # NB: no -e — ldd on static binaries returns non-zero and trips
        # errexit inside command substitution under bash 5+.

KVER=$(cat /tmp/kver)
INITRAMFS=/initramfs

mkdir -p "$INITRAMFS"/{bin,sbin,usr/bin,usr/sbin,etc,proc,sys,dev,tmp,run}
mkdir -p "$INITRAMFS"/{usr/lib,usr/lib64,lib,lib64}
mkdir -p "$INITRAMFS/lib/modules/$KVER/kernel/drivers/cxl"

# busybox + every coreutils symlink we need in the init script.
cp /bin/busybox "$INITRAMFS/bin/busybox"
for cmd in sh ls mount umount cat echo printf sleep ln mkdir cp mv rm \
           modprobe insmod lsmod poweroff halt reboot dmesg uname find sort head tail; do
    ln -s busybox "$INITRAMFS/bin/$cmd"
done

# cxl-cli binary + dynamic deps (ldd output is best-effort).
cp /usr/bin/cxl "$INITRAMFS/usr/bin/"
collect_libs() {
    ldd "$1" 2>/dev/null | awk '/=>/ {print $3}' | grep -v '^$' || true
}
LIBS=$(collect_libs /usr/bin/cxl)
LIBS="$LIBS $(collect_libs /bin/busybox)"
LIBS="$LIBS /lib64/ld-linux-x86-64.so.2 /lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"

for lib in $LIBS; do
    if [ -f "$lib" ]; then
        dest="$INITRAMFS$lib"
        mkdir -p "$(dirname "$dest")"
        cp -L "$lib" "$dest"
    fi
done

# CXL kernel modules + module index files. Ubuntu ships modules as .ko.zst
# (zstd-compressed) but busybox-static's modprobe can't decompress zstd.
# Decompress at build time and rewrite the module index so modprobe finds
# the resulting .ko files.
cp -r "/lib/modules/$KVER/kernel/drivers/cxl"/* \
      "$INITRAMFS/lib/modules/$KVER/kernel/drivers/cxl/"

find "$INITRAMFS/lib/modules/$KVER" -name "*.ko.zst" -exec zstd -d --rm {} \;

for f in modules.dep modules.alias modules.symbols modules.builtin modules.order; do
    if [ -f "/lib/modules/$KVER/$f" ]; then
        sed 's|\.ko\.zst\b|.ko|g' "/lib/modules/$KVER/$f" \
            > "$INITRAMFS/lib/modules/$KVER/$f"
    fi
done

# /init: load CXL modules, dump topology, halt.
cat > "$INITRAMFS/init" <<'EOF'
#!/bin/sh
mount -t proc  proc  /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true

echo "=== loading CXL modules ==="
modprobe cxl_acpi 2>&1 || echo "cxl_acpi: load failed"
modprobe cxl_pci  2>&1 || echo "cxl_pci: load failed"
modprobe cxl_core 2>&1 || true
modprobe cxl_mem  2>&1 || echo "cxl_mem: load failed"
modprobe cxl_port 2>&1 || true
sleep 2

echo ""
echo "=== /sys/bus/cxl/devices ==="
ls /sys/bus/cxl/devices/ 2>&1 || echo "no /sys/bus/cxl"

echo ""
echo "=== cxl list -v ==="
cxl list -v 2>&1 || echo "cxl list failed"

echo ""
echo "=== DONE ==="
sleep 1
poweroff -f
EOF
chmod +x "$INITRAMFS/init"

# Pack.
cd "$INITRAMFS"
find . | cpio -o -H newc 2>/dev/null | gzip -9 > /opt/initrd.cpio.gz
ls -la /opt/initrd.cpio.gz /opt/vmlinuz

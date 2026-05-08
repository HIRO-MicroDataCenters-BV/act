from enum import Enum


class ArchTarget(Enum):
    X86 = "x86"
    ARM64 = "arm64"
    RISCV = "riscv64"
    CXL = "cxl"
    FPGA = "fpga"

    @property
    def platform(self) -> str:
        if self is ArchTarget.X86:
            return "linux/amd64"
        if self is ArchTarget.ARM64:
            return "linux/arm64"
        if self is ArchTarget.RISCV:
            return "linux/riscv64"
        raise NotImplementedError(
            f"{self.name} has no Docker --platform mapping; "
            f"emulation requires a full QEMU VM with a virtual {self.name} device"
        )

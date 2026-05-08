import pytest

from act.reproducibility.targets import ArchTarget


def test_all_members_present():
    names = {m.name for m in ArchTarget}
    assert names == {"X86", "ARM64", "RISCV", "CXL", "FPGA"}


def test_riscv_platform():
    assert ArchTarget.RISCV.platform == "linux/riscv64"


def test_x86_platform():
    assert ArchTarget.X86.platform == "linux/amd64"


def test_arm64_platform():
    assert ArchTarget.ARM64.platform == "linux/arm64"


def test_cxl_platform_raises():
    with pytest.raises(NotImplementedError, match="QEMU VM"):
        _ = ArchTarget.CXL.platform


def test_fpga_platform_raises():
    with pytest.raises(NotImplementedError, match="QEMU VM"):
        _ = ArchTarget.FPGA.platform

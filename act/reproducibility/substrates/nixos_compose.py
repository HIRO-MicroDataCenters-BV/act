from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)

SUPPORTED_ARCHES: frozenset[str] = frozenset({"x86_64-linux", "aarch64-linux"})
SUPPORTED_FLAVOURS: frozenset[str] = frozenset({"docker", "vm-ramdisk"})

_K8S_COMPOSITION_TEMPLATE = """\
{{
  description = "ACT runtime check composition";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  inputs.nixos-compose.url = "git+https://gitlab.inria.fr/nixos-compose/nixos-compose.git";

  outputs = {{ self, nixpkgs, nixos-compose }}:
    let
      system = "{arch}";
    in
    {{
      packages.${{system}}.default = nixos-compose.lib.compose {{
        inherit system;
        flavour = "{flavour}";
        modules = [
          ({{ pkgs, ... }}: {{
            services.k3s = {{
              enable = true;
              role = "server";
              extraFlags = "--disable=traefik --write-kubeconfig-mode=644";
            }};
            networking.firewall.allowedTCPPorts = [ 6443 ];
            system.stateVersion = "25.05";
          }})
        ];
      }};
    }};
}}
"""


class NixOSComposeSubstrate(Substrate):
    name: ClassVar[str] = "nixos-compose"

    def is_available(self) -> bool:
        return shutil.which("nxc") is not None and shutil.which("nix") is not None

    def matches(self, spec: TargetSpec) -> bool:
        if spec.arch not in SUPPORTED_ARCHES:
            return False
        if spec.orchestrator != "k8s":
            return False
        if "cxl" in spec.features:
            return False
        return True

    def _render_composition(self, spec: TargetSpec, flavour: str) -> str:
        if spec.arch not in SUPPORTED_ARCHES:
            raise NotImplementedError(
                f"NixOSComposeSubstrate does not render compositions for arch {spec.arch!r}; "
                "only x86_64-linux is supported today."
            )
        if flavour not in SUPPORTED_FLAVOURS:
            raise ValueError(
                f"unsupported flavour {flavour!r}; "
                f"expected one of {sorted(SUPPORTED_FLAVOURS)}"
            )
        return _K8S_COMPOSITION_TEMPLATE.format(arch=spec.arch, flavour=flavour)

    def provision(self, spec: TargetSpec, flavour: str = "docker", timeout: int = 600) -> ProvisionedTarget:
        work_dir = Path(tempfile.mkdtemp(prefix="act-nxc-"))
        # nxc looks for `flake.nix` or `composition.nix` in cwd after `nxc init`.
        composition_path = work_dir / "flake.nix"
        composition_path.write_text(self._render_composition(spec, flavour))

        # nxc init creates the `nxc/` composition environment directory; required
        # before `nxc build` will work in the directory.
        subprocess.run(
            ["nxc", "init", "-f", flavour],
            cwd=work_dir,
            capture_output=True,
            check=True,
            timeout=60,
        )

        # nxc build takes the composition file as a positional argument.
        subprocess.run(
            ["nxc", "build", "-f", flavour, str(composition_path)],
            cwd=work_dir,
            capture_output=True,
            check=True,
            timeout=timeout,
        )

        # nxc start picks up the previous build automatically; no -f or -c.
        process = subprocess.Popen(
            ["nxc", "start"],
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        kubeconfig = work_dir / "kubeconfig.yaml"

        def teardown() -> None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            # nxc stop takes -f flavour only.
            subprocess.run(
                ["nxc", "stop", "-f", flavour],
                cwd=work_dir,
                capture_output=True,
                check=False,
                timeout=60,
            )

        return ProvisionedTarget(
            endpoint=str(kubeconfig),
            kind="kubeconfig",
            teardown=teardown,
        )

from __future__ import annotations

import shutil
from typing import ClassVar

from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)

SUPPORTED_ARCHES: frozenset[str] = frozenset({"x86_64-linux"})
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

    def provision(self, spec: TargetSpec) -> ProvisionedTarget:
        raise NotImplementedError("provision is wired in a later cycle")

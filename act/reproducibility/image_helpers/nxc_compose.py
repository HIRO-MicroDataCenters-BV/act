"""Renders NixOS-Compose flakes for image-build pipelines.

A NixOS-Compose flake passed to `nxc init` + `nxc build` produces a docker-
compose YAML (the `docker` flavour) or a QEMU VM image (the `vm-ramdisk`
flavour). Those build artefacts are then packaged into the substrate images
the DockerSubstrate pulls at runtime.
"""

from __future__ import annotations

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


def render_k8s_composition(arch: str, flavour: str = "docker") -> str:
    if arch not in SUPPORTED_ARCHES:
        raise NotImplementedError(
            f"no k8s composition template for arch {arch!r}; "
            f"expected one of {sorted(SUPPORTED_ARCHES)}"
        )
    if flavour not in SUPPORTED_FLAVOURS:
        raise ValueError(
            f"unsupported flavour {flavour!r}; expected one of {sorted(SUPPORTED_FLAVOURS)}"
        )
    return _K8S_COMPOSITION_TEMPLATE.format(arch=arch, flavour=flavour)

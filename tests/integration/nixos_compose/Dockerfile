FROM nixos/nix:latest

RUN mkdir -p /etc/nix && \
    printf 'experimental-features = nix-command flakes\nfilter-syscalls = false\n' >> /etc/nix/nix.conf

RUN nix profile install nixpkgs#python311 nixpkgs#uv nixpkgs#docker-client

RUN nix profile install github:oar-team/nixos-compose && \
    nxc --help > /dev/null

# Pre-warm the flake-input cache so every e2e doesn't re-fetch nixos-compose +
# nixpkgs from scratch. Saves ~30s per test invocation. The dummy flake here
# pulls the same inputs the substrate's rendered composition uses.
RUN mkdir -p /opt/prewarm && cd /opt/prewarm && cat > flake.nix <<'NIX' && nix flake metadata path:/opt/prewarm --no-write-lock-file > /dev/null
{
  description = "prewarm";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  inputs.nixos-compose.url = "git+https://gitlab.inria.fr/nixos-compose/nixos-compose.git";
  outputs = { self, nixpkgs, nixos-compose }: { };
}
NIX

WORKDIR /workspace
CMD ["bash"]

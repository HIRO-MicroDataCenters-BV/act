FROM nixos/nix:latest

RUN mkdir -p /etc/nix && \
    printf 'experimental-features = nix-command flakes\nfilter-syscalls = false\n' >> /etc/nix/nix.conf

RUN nix profile install nixpkgs#python311 nixpkgs#uv

RUN nix profile install github:oar-team/nixos-compose && \
    nxc --help > /dev/null

WORKDIR /workspace
CMD ["bash"]

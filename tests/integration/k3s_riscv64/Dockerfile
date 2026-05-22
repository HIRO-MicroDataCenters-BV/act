FROM ubuntu:24.04

# k3s userspace deps (cgroup tools, iptables, conntrack). Privileged container
# at runtime provides cgroup v2; the container itself just needs the binaries.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl iproute2 iptables conntrack ebtables socat && \
    rm -rf /var/lib/apt/lists/* && \
    update-alternatives --set iptables /usr/sbin/iptables-legacy 2>/dev/null || true && \
    update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy 2>/dev/null || true

# CARV-ICS-FORTH publishes the riscv64 k3s binary split into 3 chunks because
# of GitHub's 25 MB asset upload limit. Fetch all three, concat, gunzip.
# Pin by release date + SHA256 of the assembled gzip for reproducibility.
ARG CARV_RELEASE=20241024
ARG K3S_GZ_SHA256=98cc699947f09e4faaca2ef91e71fbefda888bf054d2e90d111b77ef498975b6
ARG CARV_BASE_URL=https://github.com/CARV-ICS-FORTH/k3s/releases/download

RUN set -eux; \
    cd /tmp; \
    for part in aa ab ac; do \
        curl -fsSL -o "k3s.gz.$part" \
            "${CARV_BASE_URL}/${CARV_RELEASE}/k3s-riscv64.gz.$part"; \
    done; \
    cat k3s.gz.aa k3s.gz.ab k3s.gz.ac > k3s.gz; \
    echo "${K3S_GZ_SHA256}  k3s.gz" | sha256sum -c -; \
    gunzip k3s.gz; \
    install -m 0755 k3s /usr/local/bin/k3s; \
    rm -f /tmp/k3s.gz.* /tmp/k3s; \
    /usr/local/bin/k3s --version

# Install the reference CNI plugins so the kubelet can satisfy NetworkReady
# without flannel. Flannel itself can't run under user-mode QEMU emulation
# because its vxlan/host-gw backends need iptables, which is unreliable under
# emulation. The `bridge` + `host-local` + `loopback` plugins provide a flat
# L2 bridge network that doesn't touch iptables at all — enough for kubelet
# to report Ready.
ARG CNI_VERSION=v1.9.1
ARG CNI_TGZ_SHA256=8ae4f284805187106596678959807df8c61a2f7bd12d323b45c5f0c1f51d41cd
ARG CNI_BASE_URL=https://github.com/containernetworking/plugins/releases/download
RUN set -eux; \
    cd /tmp; \
    curl -fsSL -o cni.tgz \
        "${CNI_BASE_URL}/${CNI_VERSION}/cni-plugins-linux-riscv64-${CNI_VERSION}.tgz"; \
    echo "${CNI_TGZ_SHA256}  cni.tgz" | sha256sum -c -; \
    mkdir -p /opt/cni/bin; \
    tar -xzf cni.tgz -C /opt/cni/bin; \
    rm -f cni.tgz

RUN mkdir -p /etc/cni/net.d && \
    printf '%s\n' '{' \
      '  "cniVersion": "1.0.0",' \
      '  "name": "act-bridge",' \
      '  "plugins": [' \
      '    {' \
      '      "type": "bridge",' \
      '      "bridge": "cni0",' \
      '      "isDefaultGateway": true,' \
      '      "ipMasq": false,' \
      '      "hairpinMode": true,' \
      '      "ipam": {' \
      '        "type": "host-local",' \
      '        "ranges": [[{"subnet": "10.42.0.0/24"}]]' \
      '      }' \
      '    },' \
      '    { "type": "loopback" }' \
      '  ]' \
      '}' > /etc/cni/net.d/10-act-bridge.conflist

EXPOSE 6443
ENTRYPOINT ["/usr/local/bin/k3s"]
CMD ["server", "--disable=traefik", "--write-kubeconfig-mode=644"]

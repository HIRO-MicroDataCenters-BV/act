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

EXPOSE 6443
ENTRYPOINT ["/usr/local/bin/k3s"]
CMD ["server", "--disable=traefik", "--write-kubeconfig-mode=644"]

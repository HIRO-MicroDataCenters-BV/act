FROM ubuntu:24.04

# iverilog: open-source Verilog simulator. Used by the FPGA boot-flow
# fixture to compile + run HDL testbenches inside k3s Pods scheduled on
# the cape.eu/fpga extended resource declared by FpgaSubstrate.
RUN apt-get update && \
    apt-get install -y --no-install-recommends iverilog ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    iverilog -V 2>&1 | head -1

WORKDIR /work
ENTRYPOINT ["/bin/bash"]

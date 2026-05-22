"""FPGA boot-flow fixture: ConfigMap with HDL source + Job that runs iverilog.

Deploys against any cluster reporting cape.eu/fpga as a schedulable
resource (FpgaSubstrate sets this up via Extended Resource patch).
The Job mounts the HDL ConfigMap, compiles + runs the testbench with
iverilog, and prints deterministic $display output that ACT captures
via probe_k8s_with_workload_logs and includes in the hashed state.

HDL source is embedded inline (rather than read from neighbouring files)
so the fixture is self-contained when copied into Pulumi LocalWorkspace's
temp project directory.
"""

import os

import pulumi
from pulumi_kubernetes.batch.v1 import Job
from pulumi_kubernetes.core.v1 import ConfigMap

IMAGE = os.environ.get("ACT_FPGA_IVERILOG_IMAGE", "act-fpga:iverilog")

COUNTER_V = """\
module counter (
    input clk,
    input reset,
    output reg [3:0] count
);
    always @(posedge clk or posedge reset) begin
        if (reset)
            count <= 4'b0;
        else
            count <= count + 4'b1;
    end
endmodule
"""

TB_COUNTER_V = """\
module tb_counter;
    reg clk;
    reg reset;
    wire [3:0] count;

    counter dut (.clk(clk), .reset(reset), .count(count));

    always #5 clk = ~clk;

    initial begin
        clk = 0;
        reset = 1;
        #12 reset = 0;
        repeat (5) begin
            @(posedge clk);
            #1 $display("Test: counter=%0d", count);
        end
        $display("DONE");
        $finish;
    end
endmodule
"""

config_map = ConfigMap(
    "fpga-rtl",
    metadata={"name": "fpga-rtl", "namespace": "default"},
    data={"counter.v": COUNTER_V, "tb_counter.v": TB_COUNTER_V},
)

job = Job(
    "iverilog-boot-flow",
    metadata={"name": "iverilog-boot-flow", "namespace": "default"},
    spec={
        "backoffLimit": 0,
        "template": {
            "metadata": {"labels": {"app": "iverilog-boot-flow"}},
            "spec": {
                "restartPolicy": "Never",
                # Docker Desktop's k3s sandbox doesn't ship the host seccomp
                # profile (`seccomp is not supported`). Unconfined keeps the
                # boot-flow test portable across hosts; the workload is a
                # trivial iverilog simulation, no syscall surface to harden.
                "securityContext": {"seccompProfile": {"type": "Unconfined"}},
                "containers": [
                    {
                        "name": "iverilog",
                        "image": IMAGE,
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["/bin/bash", "-c"],
                        "args": ["iverilog -o /tmp/sim /work/counter.v /work/tb_counter.v " "&& /tmp/sim"],
                        "resources": {
                            "limits": {"cape.eu/fpga": "1"},
                            "requests": {"cape.eu/fpga": "1"},
                        },
                        "volumeMounts": [{"name": "rtl", "mountPath": "/work"}],
                    }
                ],
                "volumes": [
                    {
                        "name": "rtl",
                        "configMap": {"name": "fpga-rtl"},
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[config_map]),
)

pulumi.export("job_name", job.metadata["name"])
pulumi.export("configmap_name", config_map.metadata["name"])

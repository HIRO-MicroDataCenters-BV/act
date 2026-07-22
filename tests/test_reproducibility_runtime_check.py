import json
import os
from unittest.mock import MagicMock, patch

import pytest

from act.reproducibility.runtime_check import (
    PulumiUpOutcome,
    RuntimeCheck,
    RuntimeCheckFailure,
    RuntimeCheckResult,
    extract_target_spec,
    hash_output,
    normalise_output,
    probe_k8s,
    run_pulumi_against,
)
from act.reproducibility.substrates.base import ProvisionedTarget, Substrate, TargetSpec


def _mg_returning_types(types: dict[str, str]) -> MagicMock:
    mg = MagicMock()
    mg.get_resource_type.side_effect = lambda name: types.get(name)
    return mg


@pytest.mark.parametrize(
    "node_selector, expected_arch",
    [
        ({"kubernetes.io/arch": "amd64"}, "x86_64-linux"),
        ({"kubernetes.io/arch": "riscv64"}, "riscv64-linux"),
        (None, "x86_64-linux"),  # no selector -> default
    ],
)
def test_spec_arch_from_node_selector(node_selector, expected_arch):
    pod_spec: dict = {"containers": [{"name": "nginx", "image": "nginx:1.25"}]}
    if node_selector is not None:
        pod_spec["nodeSelector"] = node_selector
    plan = {"nginx": {"spec": {"template": {"spec": pod_spec}}}}
    mg = _mg_returning_types({"nginx": "kubernetes:apps/v1:Deployment"})

    spec = extract_target_spec(plan, mg)

    assert spec.arch == expected_arch
    assert spec.orchestrator == "k8s"


def test_spec_arch_from_bare_pod_node_selector():
    # A bare Pod carries the pod spec directly under spec (no template).
    plan = {"pod": {"spec": {"nodeSelector": {"kubernetes.io/arch": "riscv64"}}}}
    mg = _mg_returning_types({"pod": "kubernetes:core/v1:Pod"})

    assert extract_target_spec(plan, mg).arch == "riscv64-linux"


def test_spec_arch_from_node_affinity():
    # nodeAffinity (required) matching kubernetes.io/arch In [..] is honored like nodeSelector.
    pod_spec = {
        "affinity": {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {"matchExpressions": [{"key": "kubernetes.io/arch", "operator": "In", "values": ["riscv64"]}]}
                    ]
                }
            }
        }
    }
    plan = {"dep": {"spec": {"template": {"spec": pod_spec}}}}
    mg = _mg_returning_types({"dep": "kubernetes:apps/v1:Deployment"})

    assert extract_target_spec(plan, mg).arch == "riscv64-linux"


@pytest.mark.parametrize(
    "resource_type, expected_orchestrator",
    [
        ("kubernetes:core/v1:Pod", "k8s"),
        ("cape:compute:Instance", None),
    ],
)
def test_spec_orchestrator_from_resource_type(resource_type, expected_orchestrator):
    plan: dict = {"res": {}}
    mg = _mg_returning_types({"res": resource_type})

    spec = extract_target_spec(plan, mg)

    assert spec.orchestrator == expected_orchestrator


def test_runtime_check_result_default_fields():
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    result = RuntimeCheckResult(passed=True, substrate="docker:linux/amd64", spec=spec)
    assert result.passed is True
    assert result.substrate == "docker:linux/amd64"
    assert result.spec.arch == "x86_64-linux"
    assert result.hash_1 == ""
    assert result.hash_2 == ""
    assert result.diff == []
    assert result.failures == []
    assert result.capture_duration_ms == 0


def test_runtime_check_failure_classifies_stage():
    failure = RuntimeCheckFailure(stage="provision_failed", detail="k3s boot exit 1")
    assert failure.stage == "provision_failed"
    assert "k3s" in failure.detail


def test_runtime_check_result_holds_failures():
    spec = TargetSpec(arch="riscv64-linux", orchestrator="k8s")
    failures = [RuntimeCheckFailure(stage="substrate_unavailable", detail="docker not found")]
    result = RuntimeCheckResult(passed=False, substrate="docker:linux/amd64", spec=spec, failures=failures)
    assert len(result.failures) == 1
    assert result.failures[0].stage == "substrate_unavailable"


def _provisioned() -> ProvisionedTarget:
    return ProvisionedTarget(
        endpoint="/tmp/kube.config",
        kind="kubeconfig",
        teardown=lambda: None,
    )


def _stub_program(tmp_path) -> str:
    """A real on-disk file run_pulumi_against can copy into the temp project."""
    p = tmp_path / "prog.py"
    p.write_text("import pulumi\n")
    return str(p)


def test_run_pulumi_against_invokes_up_and_destroy(tmp_path):
    stack = MagicMock()
    stack.up.return_value = MagicMock(outputs={"endpoint": MagicMock(value="ok")})
    stack.destroy.return_value = MagicMock()

    with patch(
        "act.reproducibility.runtime_check.automation.create_or_select_stack",
        return_value=stack,
    ):
        outcome = run_pulumi_against(
            target=_provisioned(),
            program_path=_stub_program(tmp_path),
            backend_dir=str(tmp_path),
        )

    assert outcome.failure is None
    stack.up.assert_called_once()
    stack.destroy.assert_called_once()


def test_run_pulumi_against_destroys_on_up_failure(tmp_path):
    stack = MagicMock()
    stack.up.side_effect = RuntimeError("provider rejected manifest")
    stack.destroy.return_value = MagicMock()

    with patch(
        "act.reproducibility.runtime_check.automation.create_or_select_stack",
        return_value=stack,
    ):
        outcome = run_pulumi_against(
            target=_provisioned(),
            program_path=_stub_program(tmp_path),
            backend_dir=str(tmp_path),
        )

    assert outcome.failure is not None
    assert outcome.failure.stage == "pulumi_up_failed"
    assert "provider rejected manifest" in outcome.failure.detail
    stack.destroy.assert_called_once()


def test_skip_await_transformation_stamps_annotation():
    from types import SimpleNamespace
    from typing import cast

    from act.reproducibility._skip_await import skip_await_transformation

    args = SimpleNamespace(
        type_="kubernetes:apps/v1:Deployment",
        props={"metadata": {"name": "nginx"}, "spec": {"replicas": 1}},
        opts=None,
    )
    result = skip_await_transformation(args)  # type: ignore[arg-type]
    assert result is not None
    props = cast(dict, result.props)
    assert props["metadata"]["annotations"]["pulumi.com/skipAwait"] == "true"
    # Existing metadata and other props survive untouched.
    assert props["metadata"]["name"] == "nginx"
    assert props["spec"] == {"replicas": 1}


def test_skip_await_transformation_ignores_non_k8s():
    from types import SimpleNamespace

    from act.reproducibility._skip_await import skip_await_transformation

    args = SimpleNamespace(
        type_="random:index/randomPassword:RandomPassword",
        props={"length": 8},
        opts=None,
    )
    assert skip_await_transformation(args) is None  # type: ignore[arg-type]


def test_run_pulumi_against_wraps_program_for_skip_await(tmp_path, monkeypatch):
    # skip_await (default) runs the program behind the transform wrapper so `up` returns on
    # acceptance. Keep the work_dir around to inspect what was written.
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.setattr("act.reproducibility.runtime_check.tempfile.mkdtemp", lambda **k: str(work_dir))
    monkeypatch.setattr("act.reproducibility.runtime_check.shutil.rmtree", lambda *a, **k: None)
    stack = MagicMock()
    stack.up.return_value = MagicMock(outputs={})

    with patch(
        "act.reproducibility.runtime_check.automation.create_or_select_stack",
        return_value=stack,
    ):
        run_pulumi_against(
            target=_provisioned(),
            program_path=_stub_program(tmp_path),
            backend_dir=str(tmp_path),
        )

    main = (work_dir / "__main__.py").read_text()
    assert "register_stack_transformation" in main
    assert "skip_await_transformation" in main
    assert (work_dir / "_act_program.py").exists()


def test_probe_k8s_projects_resources():
    # Projects each object to (kind, namespace, accepted spec/data) — not the raw dump, and
    # not runtime status: under skipAwait we compare what the cluster accepted.
    sample = {
        "items": [
            {
                "kind": "Deployment",
                "metadata": {"name": "nginx", "namespace": "default"},
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1},
            }
        ]
    }
    with patch(
        "act.reproducibility.runtime_check.subprocess.run",
        return_value=MagicMock(stdout=json.dumps(sample).encode(), returncode=0),
    ):
        out = probe_k8s("/tmp/kube.config")

    assert out == {"resources": [{"kind": "Deployment", "namespace": "default", "spec": {"replicas": 1}}]}


def test_probe_k8s_excludes_derived_resources():
    # A Deployment's owned Pod carries injected non-determinism (generated name, per-pod SA
    # token volume); it must be excluded — the parent's accepted spec is what we compare.
    sample = {
        "items": [
            {
                "kind": "Deployment",
                "metadata": {"name": "pause"},
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1},
            },
            {
                "kind": "Pod",
                "metadata": {"name": "pause-abc-x1", "ownerReferences": [{"kind": "ReplicaSet"}]},
                "spec": {"volumes": [{"name": "kube-api-access-rnd1"}]},
            },
        ]
    }
    with patch(
        "act.reproducibility.runtime_check.subprocess.run",
        return_value=MagicMock(stdout=json.dumps(sample).encode(), returncode=0),
    ):
        out = probe_k8s("/tmp/kube.config")
    assert [r["kind"] for r in out["resources"]] == ["Deployment"]


def test_projection_ignores_pod_suffix_and_status():
    # Two deploys of the same workload differ only by generated pod name + assigned IP;
    # the projection must hash them equal.

    def probe_with(suffix):
        sample = {
            "items": [
                {
                    "kind": "Pod",
                    "metadata": {"name": f"pause-abc12-{suffix}", "namespace": "default", "uid": suffix},
                    "spec": {"containers": [{"name": "pause", "image": "pause:3.9"}]},
                    "status": {"podIP": f"10.0.0.{suffix}", "conditions": [{"type": "Ready", "status": "True"}]},
                }
            ]
        }
        with patch(
            "act.reproducibility.runtime_check.subprocess.run",
            return_value=MagicMock(stdout=json.dumps(sample).encode(), returncode=0),
        ):
            return probe_k8s("/tmp/kube.config")

    assert hash_output(probe_with("11111")) == hash_output(probe_with("22222"))


def test_probe_k8s_sort_order_stable_under_volatile_fields():
    # Two probes of the same two resources; a volatile field (bootID) is reassigned between
    # runs. Sorting must key on the normalised form so the reassignment can't flip item order
    # and cause a false mismatch.
    def items(boot1, boot2):
        return {
            "items": [
                {"kind": "Pod", "metadata": {"name": "x"}, "spec": {"bootID": boot1, "hostname": "h1"}},
                {"kind": "Pod", "metadata": {"name": "y"}, "spec": {"bootID": boot2, "hostname": "h2"}},
            ]
        }

    def probe(payload):
        with patch(
            "act.reproducibility.runtime_check.subprocess.run",
            return_value=MagicMock(stdout=json.dumps(payload).encode(), returncode=0),
        ):
            return probe_k8s("/tmp/kube.config")

    run_a = probe(items("AAA", "ZZZ"))
    run_b = probe(items("ZZZ", "AAA"))  # bootIDs swapped between runs
    assert hash_output(run_a) == hash_output(run_b)


def test_projection_distinguishes_binarydata_and_labels():
    # Two ConfigMaps differing only in binaryData or only in user labels are different
    # deployments and must not hash equal.
    def probe(cm):
        with patch(
            "act.reproducibility.runtime_check.subprocess.run",
            return_value=MagicMock(stdout=json.dumps({"items": [cm]}).encode(), returncode=0),
        ):
            return probe_k8s("/tmp/kube.config")

    base: dict = {
        "kind": "ConfigMap",
        "metadata": {"name": "c", "namespace": "default", "labels": {"app": "a"}},
        "data": {"k": "v"},
        "binaryData": {"b": "QUFB"},
    }
    diff_binary = {**base, "binaryData": {"b": "WlpaWg=="}}
    diff_label = {**base, "metadata": {**base["metadata"], "labels": {"app": "b"}}}

    assert hash_output(probe(base)) != hash_output(probe(diff_binary))
    assert hash_output(probe(base)) != hash_output(probe(diff_label))


def test_normalise_strips_volatile_keys():
    raw = {
        "items": [
            {
                "metadata": {
                    "name": "nginx-1",
                    "namespace": "default",
                    "creationTimestamp": "2026-05-19T10:00:00Z",
                    "resourceVersion": "12345",
                    "uid": "abc-123",
                }
            }
        ]
    }
    cleaned = normalise_output(raw)
    metadata = cleaned["items"][0]["metadata"]
    assert "creationTimestamp" not in metadata
    assert "resourceVersion" not in metadata
    assert "uid" not in metadata
    assert metadata["name"] == "nginx-1"


def test_normalise_strips_volatile_pid_in_logs():
    """The one remaining value scrub is the pid pattern; other digits survive."""
    raw = {"log": "started with pid: 4242 and ran at 1700000009"}
    cleaned = normalise_output(raw)
    assert "4242" not in cleaned["log"]  # pid scrubbed
    assert "1700000009" in cleaned["log"]  # non-pid digits preserved so real drift is caught


def test_normalise_strips_system_assigned_network_keys():
    """System-assigned network/identity fields are dropped by key (lossless)."""
    raw = {
        "status": {"podIP": "10.1.2.3", "hostIP": "192.168.0.5"},
        "spec": {"clusterIP": "10.96.0.1", "ports": [{"nodePort": 30001}]},
    }
    cleaned = normalise_output(raw)
    assert "podIP" not in cleaned["status"]
    assert "hostIP" not in cleaned["status"]
    assert "clusterIP" not in cleaned["spec"]
    # nodePort is left intact: an explicitly-set port is stable and meaningful.
    assert cleaned["spec"]["ports"][0]["nodePort"] == 30001


def test_normalise_keeps_numeric_ids_in_values():
    """Numeric identifiers in values survive; volatility is dropped by key, not digit shape."""
    raw = {"trace_id": "9876543210", "ts": "1716123456"}
    cleaned = normalise_output(raw)
    assert "9876543210" in cleaned["trace_id"]
    assert "1716123456" in cleaned["ts"]


def test_normalise_keeps_port_in_url_value():
    """host:port fragments are no longer value-scrubbed, so distinct endpoints stay distinct."""
    raw = {"endpoint": "https://svc.default:34567/api"}
    cleaned = normalise_output(raw)
    assert "34567" in cleaned["endpoint"]


@pytest.mark.parametrize(
    "plan, types, expect_cxl",
    [
        # explicit hardware.cape/cxl label -> cxl
        (
            {"node": {"metadata": {"labels": {"hardware.cape/cxl": "enabled"}}, "spec": {}}},
            {"node": "kubernetes:core/v1:Node"},
            True,
        ),
        # 'cxl' only in an image tag -> NOT a cxl spec
        (
            {
                "tool": {
                    "spec": {"template": {"spec": {"containers": [{"name": "tool", "image": "myorg/cxl-helpers:v1"}]}}}
                }
            },
            {"tool": "kubernetes:apps/v1:Deployment"},
            False,
        ),
        # canonical cape.eu/cxl resource request -> cxl
        (
            {
                "workload": {
                    "spec": {
                        "template": {
                            "spec": {"containers": [{"name": "x", "resources": {"requests": {"cape.eu/cxl": "1"}}}]}
                        }
                    }
                }
            },
            {"workload": "kubernetes:apps/v1:Deployment"},
            True,
        ),
    ],
)
def test_spec_features_cxl_detection(plan, types, expect_cxl):
    spec = extract_target_spec(plan, _mg_returning_types(types))
    assert ("cxl" in spec.features) is expect_cxl


@pytest.mark.parametrize(
    "resource_request, feature",
    [("nvidia.com/gpu", "gpu"), ("cape.eu/fpga", "fpga"), ("cape.eu/cxl", "cxl")],
)
def test_spec_features_accelerator_detection(resource_request, feature):
    # GPU/FPGA are now reachable (not dead registry entries), detected like CXL.
    plan = {
        "w": {"spec": {"template": {"spec": {"containers": [{"resources": {"requests": {resource_request: "1"}}}]}}}}
    }
    spec = extract_target_spec(plan, _mg_returning_types({"w": "kubernetes:apps/v1:Deployment"}))
    assert feature in spec.features


def test_spec_features_ignores_marker_as_value():
    # A marker appearing only as a value (e.g. inside an image name) is not a feature request.
    plan = {"w": {"spec": {"template": {"spec": {"containers": [{"image": "registry/nvidia.com/gpu-tools:1"}]}}}}}
    spec = extract_target_spec(plan, _mg_returning_types({"w": "kubernetes:apps/v1:Deployment"}))
    assert "gpu" not in spec.features


def test_spec_mode_classification():
    from act.reproducibility.runtime_check import _spec_mode

    assert _spec_mode(TargetSpec(arch="riscv64-linux", orchestrator="k8s")) == ("emulation", False)
    assert _spec_mode(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["cxl"])) == ("emulation", True)
    assert _spec_mode(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["gpu"])) == ("simulation", True)
    assert _spec_mode(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["fpga"])) == ("simulation", True)


def test_verified_label():
    from act.reproducibility.runtime_check import _verified_label

    skip = [RuntimeCheckFailure(stage="substrate_unavailable", detail="")]
    assert _verified_label(False, skip, False) == "skipped"
    assert _verified_label(False, [], False) == "failed"
    assert _verified_label(True, [], False) == "verified"
    assert _verified_label(True, [], True) == "experimental"


@pytest.mark.parametrize(
    "a, b, should_equal",
    [
        ({"items": [{"name": "x"}]}, {"items": [{"name": "x"}]}, True),  # stable for equal input
        ({"replicas": 2}, {"replicas": 3}, False),  # changes on a substantive diff
    ],
)
def test_hash_output(a, b, should_equal):
    assert (hash_output(a) == hash_output(b)) is should_equal


def test_hash_distinguishes_values_that_look_like_ports():
    """Two distinct image tags must not collapse to an equal hash and mask drift."""
    assert hash_output({"image": "app:12345"}) != hash_output({"image": "app:67890"})


def test_hash_distinguishes_distinct_epoch_shaped_ids():
    """Two distinct epoch-shaped identifiers must hash differently."""
    assert hash_output({"id": "1700000001"}) != hash_output({"id": "1700000002"})


def test_hash_ignores_system_assigned_network_fields():
    """Runs differing only in an assigned podIP/clusterIP hash equal (no false drift)."""
    a = {"status": {"podIP": "10.1.2.3", "phase": "Running"}}
    b = {"status": {"podIP": "10.4.5.6", "phase": "Running"}}
    assert hash_output(a) == hash_output(b)


def test_run_pulumi_against_sets_kubeconfig_config(tmp_path):
    stack = MagicMock()
    stack.up.return_value = MagicMock(outputs={})
    stack.destroy.return_value = MagicMock()

    with patch(
        "act.reproducibility.runtime_check.automation.create_or_select_stack",
        return_value=stack,
    ):
        run_pulumi_against(
            target=_provisioned(),
            program_path=_stub_program(tmp_path),
            backend_dir=str(tmp_path),
        )

    set_config_calls = stack.set_config.call_args_list
    keys = [call.args[0] for call in set_config_calls]
    assert "kubernetes:kubeconfig" in keys


# ----- Orchestrator (RuntimeCheck) ---------------------------------------------


class _FakeSubstrate(Substrate):
    name = "fake"

    def __init__(self, available=True, matches_fn=None):
        self._available = available
        self._matches_fn = matches_fn or (lambda spec: True)
        self.provision_calls = 0
        self.teardown_calls = 0

    def is_available(self):
        return self._available

    def matches(self, spec):
        return self._matches_fn(spec)

    def provision(self, spec):
        self.provision_calls += 1
        return ProvisionedTarget(
            endpoint="/tmp/kube.config",
            kind="kubeconfig",
            teardown=self._teardown,
        )

    def _teardown(self):
        self.teardown_calls += 1


def _patched_check_dependencies(probe_responses):
    """Patches MockGenerator and run_pulumi_against for orchestrator tests.

    The mocked `run_pulumi_against` returns a `PulumiUpOutcome` whose
    `.probed` carries the next probe response - mirroring the real flow
    where the probe runs between `up` and `destroy` and is attached to
    the outcome.
    """
    plan_responses = iter(probe_responses)

    def fake_run_pulumi_against(*args, **kwargs):
        return PulumiUpOutcome(outputs={}, failure=None, probed=next(plan_responses))

    return [
        patch("act.reproducibility.runtime_check.MockGenerator", autospec=True),
        patch(
            "act.reproducibility.runtime_check.run_pulumi_against",
            side_effect=fake_run_pulumi_against,
        ),
    ]


def test_runtime_check_passes_when_two_probes_match(tmp_path):
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    probes = [{"resources": [{"kind": "Pod", "name": "a"}]}, {"resources": [{"kind": "Pod", "name": "a"}]}]

    mg_patch, pulumi_patch = _patched_check_dependencies(probes)
    with mg_patch as mg_cls, pulumi_patch:
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        check = RuntimeCheck(substrates=[sub])
        result = check.run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is True
    assert result.substrate == "fake"
    assert result.hash_1 == result.hash_2
    assert result.diff == []
    assert result.capture_duration_ms >= 0
    # Each run gets a fresh target (independent twice-and-compare), so provision + teardown 2x.
    assert sub.provision_calls == 2
    assert sub.teardown_calls == 2
    # Honest labelling: a real CPU-arch green is "verified" via emulation at deployment-accepted depth.
    assert (result.mode, result.depth, result.verified) == ("emulation", "deployment-accepted", "verified")


def test_runtime_check_fails_when_probes_differ(tmp_path):
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    probes = [
        {"resources": [{"kind": "Pod", "name": "a"}]},
        {"resources": [{"kind": "Pod", "name": "b"}]},
    ]

    mg_patch, pulumi_patch = _patched_check_dependencies(probes)
    with mg_patch as mg_cls, pulumi_patch:
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        result = RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is False
    assert result.hash_1 != result.hash_2
    assert any(f.stage == "output_mismatch" for f in result.failures)


def test_runtime_check_flags_nothing_observed(tmp_path):
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    probes: list[dict] = [{"items": []}, {"items": []}]

    mg_patch, pulumi_patch = _patched_check_dependencies(probes)
    with mg_patch as mg_cls, pulumi_patch:
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        result = RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is False
    assert any(f.stage == "nothing_observed" for f in result.failures)


def test_runtime_check_records_substrate_unavailable(tmp_path):
    sub = _FakeSubstrate(available=False, matches_fn=lambda s: True)

    with patch("act.reproducibility.runtime_check.MockGenerator", autospec=True) as mg_cls:
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        result = RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is False
    assert any(f.stage == "substrate_unavailable" for f in result.failures)
    assert result.verified == "skipped"


def test_runtime_check_records_spec_unsupported(tmp_path):
    sub = _FakeSubstrate(matches_fn=lambda s: False)

    with patch("act.reproducibility.runtime_check.MockGenerator", autospec=True) as mg_cls:
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        result = RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is False
    assert any(f.stage == "spec_unsupported" for f in result.failures)


def test_runtime_check_teardown_runs_on_pulumi_failure(tmp_path):
    sub = _FakeSubstrate(matches_fn=lambda s: True)
    failing_outcome = MagicMock(outputs={}, failure=RuntimeCheckFailure(stage="pulumi_up_failed", detail="boom"))

    with (
        patch("act.reproducibility.runtime_check.MockGenerator", autospec=True) as mg_cls,
        patch("act.reproducibility.runtime_check.run_pulumi_against", return_value=failing_outcome),
    ):
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        result = RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is False
    assert any(f.stage == "pulumi_up_failed" for f in result.failures)
    # Run 1 failed, so run 2 is not attempted; the one provisioned target is still torn down.
    assert sub.provision_calls == 1
    assert sub.teardown_calls == 1


def test_runtime_check_uses_custom_probe_fn(tmp_path):
    """A caller-supplied probe_fn is passed through unchanged (not wrapped)."""
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    custom_probe = MagicMock(return_value={"_act_workload_logs": {"iverilog": "DONE\n"}})

    captured_probe_fns = []

    def fake_run_pulumi_against(*args, **kwargs):
        captured_probe_fns.append(kwargs.get("probe_fn"))
        # Mirror production: run_pulumi_against calls probe_fn(target) with the target.
        probed = kwargs["probe_fn"](kwargs["target"])
        return PulumiUpOutcome(outputs={}, failure=None, probed=probed)

    with (
        patch("act.reproducibility.runtime_check.MockGenerator", autospec=True) as mg_cls,
        patch("act.reproducibility.runtime_check.run_pulumi_against", side_effect=fake_run_pulumi_against),
    ):
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        check = RuntimeCheck(substrates=[sub], probe_fn=custom_probe)
        result = check.run("some.py", "schema.json", backend_dir=str(tmp_path))

    # A custom probe is passed through raw so a (target)-only probe keeps working.
    assert len(captured_probe_fns) == 2
    assert all(fn is custom_probe for fn in captured_probe_fns)
    assert result.passed is True
    assert result.hash_1 == result.hash_2


def test_runtime_check_binds_namespace_and_timeout_on_default_probe(tmp_path):
    """namespace/probe_timeout are bound onto the DEFAULT probe (probe_k8s) and it is
    callable with a single (target) arg, matching run_pulumi_against's probe_fn(target)."""
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    fake_probe = MagicMock(return_value={})
    captured_probe_fns = []

    def fake_run_pulumi_against(*args, **kwargs):
        captured_probe_fns.append(kwargs.get("probe_fn"))
        kwargs["probe_fn"](kwargs["target"])  # invoke exactly as production does
        return PulumiUpOutcome(outputs={}, failure=None, probed={})

    with (
        patch("act.reproducibility.runtime_check.probe_k8s", fake_probe),
        patch("act.reproducibility.runtime_check.MockGenerator", autospec=True) as mg_cls,
        patch("act.reproducibility.runtime_check.run_pulumi_against", side_effect=fake_run_pulumi_against),
    ):
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        check = RuntimeCheck(substrates=[sub], namespace="apps", probe_timeout=120)
        check.run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert len(captured_probe_fns) == 2
    # the bound partial invoked probe_k8s with the target plus the tunables
    for call in fake_probe.call_args_list:
        assert call.kwargs == {"namespace": "apps", "timeout": 120}
        assert len(call.args) == 1  # the target, positionally


def test_probe_k8s_with_workload_logs_waits_for_jobs_and_captures_logs():
    """probe_k8s_with_workload_logs combines existing probe + jobs wait + logs capture."""
    job_running = {"items": [{"status": {"succeeded": 0, "failed": 0}}]}
    job_done = {"items": [{"status": {"succeeded": 1}}]}
    base_state = {"items": [{"kind": "ConfigMap", "metadata": {"name": "fpga-rtl"}}]}
    pod_list = {
        "items": [
            {"metadata": {"name": "iverilog-boot-flow-7fkx2"}},
            {"metadata": {"name": "coredns-abc12"}},
        ]
    }

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "jobs" in cmd:
            payload = job_running if len([c for c in calls if "jobs" in c]) == 1 else job_done
            return MagicMock(stdout=json.dumps(payload).encode(), returncode=0)
        if "get" in cmd and any("pods,services" in c for c in cmd):
            return MagicMock(stdout=json.dumps(base_state).encode(), returncode=0)
        if "get" in cmd and "pods" in cmd:
            return MagicMock(stdout=json.dumps(pod_list).encode(), returncode=0)
        if "logs" in cmd:
            return MagicMock(stdout=b"Test: counter=1\nDONE\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    from act.reproducibility.runtime_check import probe_k8s_with_workload_logs

    with patch("act.reproducibility.runtime_check.subprocess.run", side_effect=fake_run):
        state = probe_k8s_with_workload_logs("/tmp/kube.config", namespace="default", timeout=5)

    assert "_act_workload_logs" in state
    # Random suffix stripped, system pod skipped.
    assert state["_act_workload_logs"] == {
        "iverilog-boot-flow": "Test: counter=1\nDONE\n",
    }


def test_runtime_check_removes_owned_backend_dir(tmp_path):
    """When no backend_dir is supplied, RuntimeCheck creates a tempdir and cleans it up."""
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    probes: list[dict] = [{"items": []}, {"items": []}]
    owned_dir = tmp_path / "owned-state"
    owned_dir.mkdir()

    mg_patch, pulumi_patch = _patched_check_dependencies(probes)
    with (
        mg_patch as mg_cls,
        pulumi_patch,
        patch("act.reproducibility.runtime_check.tempfile.mkdtemp", return_value=str(owned_dir)),
    ):
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        RuntimeCheck(substrates=[sub]).run("some.py", "schema.json")

    assert not os.path.exists(owned_dir)


def test_runtime_check_preserves_caller_backend_dir(tmp_path):
    """When the caller supplies backend_dir, RuntimeCheck must not remove it."""
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    probes: list[dict] = [{"items": []}, {"items": []}]

    mg_patch, pulumi_patch = _patched_check_dependencies(probes)
    with mg_patch as mg_cls, pulumi_patch:
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert os.path.exists(tmp_path)


def test_runtime_check_reports_internal_error_when_post_provision_raises(tmp_path):
    """Unexpected exceptions in the post-provision path classify as internal_error, not provision_failed."""
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    probes: list[dict] = [{"items": []}]

    mg_patch, pulumi_patch = _patched_check_dependencies(probes)
    with (
        mg_patch as mg_cls,
        pulumi_patch,
        patch(
            "act.reproducibility.runtime_check.normalise_output",
            side_effect=RuntimeError("normaliser blew up"),
        ),
    ):
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        result = RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is False
    assert any(f.stage == "internal_error" and "normaliser blew up" in f.detail for f in result.failures)
    assert not any(f.stage == "provision_failed" for f in result.failures)
    assert sub.teardown_calls == 1


def test_runtime_check_reports_provision_failed_when_substrate_returns_none(tmp_path):
    """A substrate that returns None from provision() must surface as a provision_failed, not silently pass."""
    sub = _FakeSubstrate(matches_fn=lambda s: True)
    sub.provision = lambda spec: None  # type: ignore[method-assign]

    with patch("act.reproducibility.runtime_check.MockGenerator", autospec=True) as mg_cls:
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        result = RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is False
    assert any(f.stage == "provision_failed" and "returned None" in f.detail for f in result.failures)


def test_runtime_check_classifies_provision_timeout_as_skip(tmp_path):
    """A provision TimeoutError (slow emulated arch) is a `timeout` skip, not a red provision_failed."""
    sub = _FakeSubstrate(matches_fn=lambda s: True)

    def _timeout(spec):
        raise TimeoutError("k3s did not produce kubeconfig within 180s")

    sub.provision = _timeout  # type: ignore[method-assign]

    with patch("act.reproducibility.runtime_check.MockGenerator", autospec=True) as mg_cls:
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        result = RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is False
    assert any(f.stage == "timeout" for f in result.failures)
    assert not any(f.stage == "provision_failed" for f in result.failures)

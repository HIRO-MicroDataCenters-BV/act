import json
import os
from unittest.mock import MagicMock, patch

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


def test_spec_arch_from_k8s_node_selector():
    plan = {
        "nginx": {
            "spec": {
                "template": {
                    "spec": {
                        "nodeSelector": {"kubernetes.io/arch": "amd64"},
                        "containers": [{"name": "nginx", "image": "nginx:1.25"}],
                    }
                }
            }
        }
    }
    mg = _mg_returning_types({"nginx": "kubernetes:apps/v1:Deployment"})

    spec = extract_target_spec(plan, mg)

    assert spec.arch == "x86_64-linux"
    assert spec.orchestrator == "k8s"


def test_spec_arch_riscv64_from_node_selector():
    plan = {
        "nginx": {
            "spec": {
                "template": {
                    "spec": {
                        "nodeSelector": {"kubernetes.io/arch": "riscv64"},
                        "containers": [{"name": "nginx", "image": "nginx:1.25"}],
                    }
                }
            }
        }
    }
    mg = _mg_returning_types({"nginx": "kubernetes:apps/v1:Deployment"})

    spec = extract_target_spec(plan, mg)

    assert spec.arch == "riscv64-linux"


def test_spec_default_arch_when_no_node_selector():
    plan = {"nginx": {"spec": {"template": {"spec": {"containers": [{"image": "nginx:1.25"}]}}}}}
    mg = _mg_returning_types({"nginx": "kubernetes:apps/v1:Deployment"})

    spec = extract_target_spec(plan, mg)

    assert spec.arch == "x86_64-linux"


def test_spec_orchestrator_k8s_when_k8s_token_present():
    plan: dict = {"nginx": {}}
    mg = _mg_returning_types({"nginx": "kubernetes:core/v1:Pod"})

    spec = extract_target_spec(plan, mg)

    assert spec.orchestrator == "k8s"


def test_spec_orchestrator_none_for_cape_only_program():
    plan: dict = {"my-instance": {}}
    mg = _mg_returning_types({"my-instance": "cape:compute:Instance"})

    spec = extract_target_spec(plan, mg)

    assert spec.orchestrator is None


def test_spec_features_include_cxl_when_program_mentions_it():
    plan = {
        "node": {
            "metadata": {"labels": {"hardware.cape/cxl": "enabled"}},
            "spec": {},
        }
    }
    mg = _mg_returning_types({"node": "kubernetes:core/v1:Node"})

    spec = extract_target_spec(plan, mg)

    assert "cxl" in spec.features


def test_runtime_check_result_default_fields():
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    result = RuntimeCheckResult(passed=True, substrate="nixos-compose", spec=spec)
    assert result.passed is True
    assert result.substrate == "nixos-compose"
    assert result.spec.arch == "x86_64-linux"
    assert result.hash_1 == ""
    assert result.hash_2 == ""
    assert result.diff == []
    assert result.failures == []
    assert result.capture_duration_ms == 0


def test_runtime_check_failure_classifies_stage():
    failure = RuntimeCheckFailure(stage="provision_failed", detail="nxc build exit 1")
    assert failure.stage == "provision_failed"
    assert "nxc" in failure.detail


def test_runtime_check_result_holds_failures():
    spec = TargetSpec(arch="riscv64-linux", orchestrator="k8s")
    failures = [RuntimeCheckFailure(stage="substrate_unavailable", detail="nxc not found")]
    result = RuntimeCheckResult(passed=False, substrate="nixos-compose", spec=spec, failures=failures)
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


def test_probe_k8s_returns_parsed_pod_list():
    sample = {
        "items": [
            {
                "metadata": {"name": "nginx-1", "namespace": "default"},
                "spec": {"containers": [{"image": "nginx:1.25"}]},
            }
        ]
    }
    with patch(
        "act.reproducibility.runtime_check.subprocess.run",
        return_value=MagicMock(stdout=json.dumps(sample).encode(), returncode=0),
    ):
        out = probe_k8s("/tmp/kube.config")

    assert out == sample


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


def test_normalise_strips_volatile_string_values():
    raw = {"log": "pid: 1234 started ok at 1716123456"}
    cleaned = normalise_output(raw)
    assert "1234" not in cleaned["log"]
    assert "1716123456" not in cleaned["log"]


def test_normalise_keeps_node_port_outside_url_context():
    """5-digit ports in JSON values (e.g. nodePort) must survive normalisation."""
    raw = {"spec": {"ports": [{"nodePort": 30001, "targetPort": 8080}]}}
    cleaned = normalise_output(raw)
    # Values are ints, not strings, so they're untouched anyway; but the
    # JSON-rendered hash must still see the same number.
    assert cleaned == raw

    # And the string-rendered form keeps the number too.
    raw_str = {"line": "nodePort=30001 targetPort=8080"}
    cleaned_str = normalise_output(raw_str)
    assert "30001" in cleaned_str["line"]
    assert "8080" in cleaned_str["line"]


def test_normalise_keeps_long_numeric_id_outside_epoch_range():
    """A 10-digit identifier that isn't an epoch timestamp must survive."""
    raw = {"trace_id": "9876543210", "epoch": "1716123456"}
    cleaned = normalise_output(raw)
    # Non-epoch-shaped ID kept; epoch-shaped stripped.
    assert "9876543210" in cleaned["trace_id"]
    assert "1716123456" not in cleaned["epoch"]


def test_normalise_strips_ephemeral_port_in_url_context():
    """Ports inside host:port URLs are scrubbed."""
    raw = {"endpoint": "https://127.0.0.1:34567/api"}
    cleaned = normalise_output(raw)
    assert "34567" not in cleaned["endpoint"]


def test_spec_features_skip_cxl_when_cxl_appears_only_in_image_name():
    """A resource whose only mention of 'cxl' is in an image tag is NOT a CXL spec."""
    plan = {
        "tool": {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "tool", "image": "myorg/cxl-helpers:v1"}],
                    }
                }
            }
        }
    }
    mg = _mg_returning_types({"tool": "kubernetes:apps/v1:Deployment"})

    spec = extract_target_spec(plan, mg)

    assert "cxl" not in spec.features


def test_spec_features_include_cxl_when_extended_resource_request_present():
    """The canonical cape.eu/cxl resource request flips the cxl feature on."""
    plan = {
        "workload": {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "x",
                                "resources": {"requests": {"cape.eu/cxl": "1"}},
                            }
                        ]
                    }
                }
            }
        }
    }
    mg = _mg_returning_types({"workload": "kubernetes:apps/v1:Deployment"})

    spec = extract_target_spec(plan, mg)

    assert "cxl" in spec.features


def test_hash_stable_for_normalised_output():
    a = {"items": [{"name": "x"}]}
    b = {"items": [{"name": "x"}]}
    assert hash_output(a) == hash_output(b)


def test_hash_changes_when_substantive_field_differs():
    a = {"replicas": 2}
    b = {"replicas": 3}
    assert hash_output(a) != hash_output(b)


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
        self.teardown_calls = 0

    def is_available(self):
        return self._available

    def matches(self, spec):
        return self._matches_fn(spec)

    def provision(self, spec):
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
    probes = [{"items": [{"metadata": {"name": "a"}}]}, {"items": [{"metadata": {"name": "a"}}]}]

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
    assert sub.teardown_calls == 1


def test_runtime_check_fails_when_probes_differ(tmp_path):
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    probes = [
        {"items": [{"metadata": {"name": "a"}}]},
        {"items": [{"metadata": {"name": "b"}}]},
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


def test_runtime_check_records_substrate_unavailable(tmp_path):
    sub = _FakeSubstrate(available=False, matches_fn=lambda s: True)

    with patch("act.reproducibility.runtime_check.MockGenerator", autospec=True) as mg_cls:
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        result = RuntimeCheck(substrates=[sub]).run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert result.passed is False
    assert any(f.stage == "substrate_unavailable" for f in result.failures)


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
    assert sub.teardown_calls == 1


def test_runtime_check_uses_custom_probe_fn(tmp_path):
    """RuntimeCheck honours an injected probe_fn - passed into run_pulumi_against."""
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    custom_probe = MagicMock(return_value={"_act_workload_logs": {"iverilog": "DONE\n"}})

    captured_probe_fns = []

    def fake_run_pulumi_against(*args, **kwargs):
        captured_probe_fns.append(kwargs.get("probe_fn"))
        # Mirror real behaviour: run the probe_fn ourselves so the test
        # observes the same data flow as production.
        target = kwargs["target"]
        probed = kwargs["probe_fn"](target.endpoint) if kwargs.get("probe_fn") else None
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

    # run_pulumi_against was called twice, each time with our custom probe_fn
    # wrapped in a partial that binds the namespace/timeout tunables.
    assert len(captured_probe_fns) == 2
    assert all(fn.func is custom_probe for fn in captured_probe_fns)
    assert all(fn.keywords == {"namespace": "default", "timeout": 60} for fn in captured_probe_fns)
    assert result.passed is True
    assert result.hash_1 == result.hash_2


def test_runtime_check_binds_custom_namespace_and_timeout(tmp_path):
    """RuntimeCheck binds a custom namespace + probe_timeout onto the probe_fn."""
    sub = _FakeSubstrate(matches_fn=lambda s: s.orchestrator == "k8s")
    custom_probe = MagicMock(return_value={})
    captured_probe_fns = []

    def fake_run_pulumi_against(*args, **kwargs):
        captured_probe_fns.append(kwargs.get("probe_fn"))
        kwargs["probe_fn"](kwargs["target"].endpoint)
        return PulumiUpOutcome(outputs={}, failure=None, probed={})

    with (
        patch("act.reproducibility.runtime_check.MockGenerator", autospec=True) as mg_cls,
        patch("act.reproducibility.runtime_check.run_pulumi_against", side_effect=fake_run_pulumi_against),
    ):
        mg = mg_cls.return_value
        mg.run_with_mocks.return_value = {"nginx": {}}
        mg.get_resource_type.return_value = "kubernetes:apps/v1:Deployment"

        check = RuntimeCheck(substrates=[sub], probe_fn=custom_probe, namespace="apps", probe_timeout=120)
        check.run("some.py", "schema.json", backend_dir=str(tmp_path))

    assert all(fn.keywords == {"namespace": "apps", "timeout": 120} for fn in captured_probe_fns)


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

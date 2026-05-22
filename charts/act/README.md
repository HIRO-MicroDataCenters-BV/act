# ACT Helm chart

A one-shot Kubernetes `Job` that runs ACT (Automated Configuration Testing) against a Pulumi program. The Job validates the program against security rules and exits with a status code; it does not stay running.

## Install

```bash
helm install act ./charts/act \
  --set program=/workspace/program.py \
  --set "schema[0]=/workspace/schema.json"
```

Read the result:

```bash
kubectl get job act
kubectl logs job/act
```

## Configuration

Common values (see `values.yaml` for the full list):

| Key | Default | Description |
|---|---|---|
| `image.repository` | `ghcr.io/hiro-microdatacenters-bv/act` | container image |
| `image.tag` | matches `appVersion` | image tag |
| `program` | `/workspace/program.py` | path to the Pulumi program inside the container |
| `schema` | `/workspace/schema.json` | provider schema path (list, repeat for multi-provider) |
| `rules` | `[]` | extra rule engines (e.g. `[checkov]`) |
| `volumes`, `volumeMounts` | `[]` | mount the program and schema into the container |
| `resources.requests.cpu` | `250m` | |
| `resources.limits.memory` | `2Gi` | |
| `job.ttlSecondsAfterFinished` | `300` | seconds before the completed Job is garbage-collected |
| `job.backoffLimit` | `0` | no retries: exit code is the result |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | all checks passed |
| 1 | one or more violations found |
| 2 | pipeline error (bad schema, program crash, missing tooling) |

## What the chart does NOT do

The chart runs the in-cluster checks only: mock generation, the correctness oracle, and (when reachable) the cognitive validator. The reproducibility flags (`--check-deployment-arch`, `--check-deployment-runtime`) are not exposed by the chart. They shell out to `docker`, `kubectl`, and the `pulumi` CLI on the host and require privileged access, which is appropriate for a developer workstation or a CI runner, not for a pod scheduled inside the cluster being validated.

Run those checks from CI or locally with `uv run python -m act.run` instead.

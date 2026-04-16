# ACT — Automated Configuration Testing

IaC validation framework for Pulumi programs. Catches security violations before deployment — no cloud credentials or real infrastructure needed.

Works with any Pulumi provider (Kubernetes, CAPE, AWS, ...).

## How it works

ACT runs your Pulumi program under mocks, intercepts resource registrations, and checks the captured outputs against security rules.

```
Pulumi program → MockGenerator → Correctness Oracle → CI/CD Gate
```

## Install

```bash
cd src
uv sync
```

## Run tests

```bash
uv run pytest act/tests/
```

## Example

Check a Kubernetes Deployment for security violations without a cluster:

```bash
uv run python act/examples/kubernetes/check_nginx.py
```

Output:

```
FAIL: [nginx] container 'nginx': runAsNonRoot not set
```

See `act/examples/` for the full example and more providers.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- Pulumi CLI (for downloading provider schemas)

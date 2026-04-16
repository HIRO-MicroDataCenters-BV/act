# Kubernetes Example

Validates a Kubernetes Pulumi program without a real cluster.

## Setup

```bash
# 1. Install the Kubernetes SDK
cd src
uv add pulumi-kubernetes

# 2. Download the provider schema
pulumi package get-schema kubernetes > act/examples/kubernetes/schema.json
```

## Run

```bash
# 3. Run the example — catches violation before any deployment
uv run python act/examples/kubernetes/check_nginx.py
```

## What it does

- Loads `nginx_deployment.py` — an nginx Deployment missing `securityContext`
- Runs it through MockGenerator (no cluster, no credentials)
- Checks if `runAsNonRoot` is set on each container
- Exits with code `1` and prints the violation if not

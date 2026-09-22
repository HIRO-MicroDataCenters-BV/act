# Workflow actions

Actions that let a workflow engine run the ACT gate as one step of a pipeline.
Each action is a self-contained directory: the engine copies only that directory
into its build, so the action installs ACT and the CAPE SDK from their public git
repositories rather than importing the package next to it.

| Action | What it runs |
|--------|--------------|
| `act-check/` | `act check` on an uploaded or inline Pulumi program: mock generation, structural oracle, optional Checkov rules, plan determinism, and the advisory cognitive validator. Outputs the verdict, exit code, report, and run artefact |

The action bundles the CAPE and Kubernetes provider schemas (the Kubernetes one
gzip-compressed; the handler unpacks it per run). A program for any other provider
needs its schema uploaded through the `schema` input, because the action container
has no `pulumi` CLI to fetch one, and its SDK added to `requirements.txt`.

The reproducibility checks that need Docker (`--check-deployment-arch`,
`--check-deployment-runtime`) are not exposed here: action containers have no
container runtime. Run those from a workstation or CI runner.

## Registering the action

1. In the engine's library, add this repository by its git URL and scan it on the
   `main` branch (a scan with no branch named falls back to `master`, which this
   repository does not have); the scanner finds every directory that carries an
   action metadata file.
2. Build `act-check`. The build installs `requirements.txt`, which pins ACT to a
   release tag. The action version in `ryax_metadata.yaml` mirrors that tag, so
   bump both together when a new ACT release should be picked up; the store shows
   the action version, not the package version.
3. In a workflow, add a trigger (a manual run with a file upload, or an HTTP
   endpoint that carries the program text in the request body) and connect it to
   `act-check`. The report and the run artefact appear as outputs of the run.

Give the action at least 1 CPU and 2 GB of memory; the Checkov engine is the
heaviest dependency.

## Seeing each validation layer

The `log_level` input controls how much of the run the engine's log panel shows for
that step. It defaults to `INFO`, which prints one line per layer with its timing:
mock generation, the oracle with the violation count split by the engine that raised
it, the verdict, and plan determinism with both hashes it compared. `DEBUG` adds the
detail inside each layer. `WARNING` returns to the quiet output.

# Contributing to ACT

Thanks for your interest in contributing.

## Development setup

ACT uses [`uv`](https://docs.astral.sh/uv/) for Python dependency management.

```bash
git clone --recurse-submodules https://github.com/HIRO-MicroDataCenters-BV/act.git
cd act
uv sync
```

The `cape-sdks` submodule is required for the CAPE provider tests.

## Branch workflow

- Branch off `main` for every change: `feature/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>`.
- Push the branch and open a pull request against `main`.
- Do not commit directly to `main`.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: add CXL substrate
fix: drain mock event loop before returning outputs
docs: clarify runtime check semantics
chore: bump pulumi to 3.230
ci: install pip in venv so Pulumi can discover Python deps
```

Keep the subject under 72 characters. Use the body for the "why" when it is not obvious from the diff.

## Code style

Formatting and linting are enforced by pre-commit hooks. Install them once:

```bash
uv run pre-commit install
```

The hooks run `isort`, `black`, `flake8`, `shellcheck`, plus generic file checks. To run them manually:

```bash
uv run pre-commit run --all-files
```

## Tests

```bash
uv run pytest                                 # full suite
uv run pytest tests/test_oracle.py            # one file
uv run pytest tests/test_oracle.py::test_x    # one test
```

Substrate end-to-end tests live under `tests/integration/<name>/` and run inside containers. They are gated on Docker availability and are skipped automatically when Docker is unavailable.

## Pull request checklist

- [ ] Branch is based on the latest `main`.
- [ ] `uv run pre-commit run --all-files` passes.
- [ ] `uv run pytest` passes.
- [ ] Commit messages follow Conventional Commits.
- [ ] PR title is short; PR body explains the "why" in one or two sentences.

## Reporting issues

Bug reports and feature requests are welcome via GitHub Issues.

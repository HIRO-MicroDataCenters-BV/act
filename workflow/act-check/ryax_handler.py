"""Workflow action wrapping the ACT command-line gate.

Inputs arrive as a dict keyed by the names declared in the metadata; file inputs are
paths. The report ACT prints is captured and returned as an output, alongside the exit
code and the JSON run artefact.
"""

from __future__ import annotations

import contextlib
import glob
import gzip
import io
import os
import shutil
import tempfile

from act.run import main as act_main

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_DIR = os.path.join(_HERE, "schemas")


def _bundled_schemas(workdir: str) -> list[str]:
    """Every schema shipped with the action; gzip-compressed ones are unpacked into workdir.

    ACT takes several --schema files and uses each for the provider it declares.
    """
    paths = []
    for name in sorted(os.listdir(_SCHEMA_DIR)):
        src = os.path.join(_SCHEMA_DIR, name)
        if name.endswith(".json.gz"):
            dst = os.path.join(workdir, name[: -len(".gz")])
            with gzip.open(src, "rb") as fin, open(dst, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            paths.append(dst)
        elif name.endswith(".json"):
            paths.append(src)
    return paths


def handle(inputs: dict) -> dict:
    workdir = tempfile.mkdtemp(prefix="act-run-")

    program = inputs.get("program")
    if not program:
        source = inputs.get("program_source") or ""
        if not source.strip():
            raise ValueError("provide either the program file or the program source")
        program = os.path.join(workdir, "program.py")
        with open(program, "w") as f:
            f.write(source)

    # An uploaded schema is used on its own; otherwise every bundled schema is offered.
    schemas = [inputs["schema"]] if inputs.get("schema") else _bundled_schemas(workdir)

    # The cognitive validator's settings travel as environment variables; acv_mode decides whether it runs.
    acv_env = (
        ("acv_model", "ACT_ACV_MODEL"),
        ("acv_base_url", "ACT_ACV_BASE_URL"),
        ("acv_api_key", "ACT_ACV_API_KEY"),
        ("acv_timeout", "ACT_ACV_TIMEOUT"),
        ("acv_max_iterations", "ACT_ACV_MAX_ITERATIONS"),
        ("acv_extra_body", "ACT_ACV_EXTRA_BODY"),
    )
    for key, env in acv_env:
        value = inputs.get(key)
        if value:
            os.environ[env] = str(value)
        else:
            os.environ.pop(env, None)

    argv = ["check", "--program", program, "--schema", *schemas, "--output", workdir]
    argv += ["--acv-mode", inputs.get("acv_mode") or "none"]
    # INFO and below put one line per validation layer in the engine's log for this step.
    argv += ["--log-level", inputs.get("log_level") or "INFO"]
    rules = inputs.get("rules") or "none"
    if rules != "none":
        argv += ["--rules", rules]

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exit_code = act_main(argv)
    report = captured.getvalue()
    print(report)

    artefacts = sorted(glob.glob(os.path.join(workdir, "act_run_*.json")))
    result = {"passed": exit_code == 0, "exit_code": exit_code, "report": report}
    if artefacts:
        result["artefact"] = artefacts[-1]
    return result

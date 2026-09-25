"""Shared helpers for FuzzRunner and PropertyRunner: find the inputs a parameterized program
reads (env vars, sys.argv), re-run it under one set of them, and record what triggered each finding."""

from typing import Optional

import ast
import itertools

from act.core.mock_generator import MockGenerator
from act.core.violations import Violation

# Per-variable boundary values: unset, empty, and a representative non-empty value.
_ENV_BOUNDARY_VALUES: tuple = (None, "", "act-fuzz")

# Key under which a finding's inputs record the program's arguments (not a valid env var name).
ARGV_KEY = "sys.argv"

# Values worth trying beyond unset/empty: wildcards, open CIDRs, flags, numeric edges, and a
# long string. Security misconfigurations tend to hide behind exactly these.
INTERESTING_VALUES: tuple = ("*", "0.0.0.0/0", "::/0", "true", "false", "0", "-1", "65536", " ", "null", "a" * 256)


def record_inputs(env: dict, argv: Optional[list]) -> dict:
    """The inputs a finding was triggered by, in the shape describe_inputs renders."""
    return {**env, ARGV_KEY: list(argv)} if argv is not None else dict(env)


def _is_name(node, ident: str) -> bool:
    return isinstance(node, ast.Name) and node.id == ident


def _is_os_environ(node) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "environ" and _is_name(node.value, "os")


def _const_str(node) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _env_var_from_node(node) -> Optional[str]:
    """Return the env var name a node reads (os.environ[...] / os.environ.get / os.getenv), else None."""
    if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
        return _const_str(node.slice)
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get" and _is_os_environ(func.value):
            return _const_str(node.args[0])
        if isinstance(func, ast.Attribute) and func.attr == "getenv" and _is_name(func.value, "os"):
            return _const_str(node.args[0])
    return None


def discover_env_vars(program_path: str) -> list[str]:
    """AST-scan the program for the environment variables it reads (unique, order-preserving)."""
    with open(MockGenerator._entry_point(program_path)) as f:
        tree = ast.parse(f.read())
    names: list[str] = []
    for node in ast.walk(tree):
        name = _env_var_from_node(node)
        if name and name not in names:
            names.append(name)
    return names


def reads_argv(program_path: str) -> bool:
    """Whether the program reads sys.argv (so its arguments are an input to vary)."""
    with open(MockGenerator._entry_point(program_path)) as f:
        tree = ast.parse(f.read())
    return any(
        isinstance(node, ast.Attribute) and node.attr == "argv" and _is_name(node.value, "sys")
        for node in ast.walk(tree)
    )


def describe_inputs(inputs: dict) -> str:
    """Render the inputs that triggered a finding: set values, then the unset names."""
    set_parts = [f'{name}="{value}"' for name, value in inputs.items() if name != ARGV_KEY and value is not None]
    unset = [name for name, value in inputs.items() if name != ARGV_KEY and value is None]
    text = " ".join(set_parts)
    if unset:
        text += f" ({', '.join(unset)} unset)" if text else f"{', '.join(unset)} unset"
    if ARGV_KEY in inputs:
        args = ", ".join(f'"{a}"' for a in inputs[ARGV_KEY])
        text += f"{' ' if text else ''}sys.argv[1:]=[{args}]"
    return text


def check_inputs(mock_generator, oracle, program_path: str, env: dict, argv: Optional[list] = None) -> list[Violation]:
    """Re-run the program under one set of inputs; return its violations, each naming its resource."""
    try:
        # A program that raises on some input is not itself a policy violation.
        outputs = mock_generator.run_with_mocks(program_path, env=env, argv=argv)
    except Exception:
        return []
    found: list[Violation] = []
    for name, resource_outputs in outputs.items():
        token = mock_generator.get_resource_type(name)
        if token:
            for v in oracle.check(token, resource_outputs):
                v.resource = name
                found.append(v)
    return found


def generate_env_combinations(var_names: list[str], cap: int = 64) -> list[dict]:
    """Boundary combos (unset / empty / non-empty): full cartesian under cap, else one-at-a-time."""
    if not var_names:
        return []
    if len(_ENV_BOUNDARY_VALUES) ** len(var_names) <= cap:
        return [dict(zip(var_names, vals)) for vals in itertools.product(_ENV_BOUNDARY_VALUES, repeat=len(var_names))]
    baseline = {name: None for name in var_names}
    combos = [dict(baseline)]
    for var in var_names:
        for value in _ENV_BOUNDARY_VALUES:
            if value is not None:
                combos.append({**baseline, var: value})
    return combos

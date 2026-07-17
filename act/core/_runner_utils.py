"""Shared helpers for FuzzRunner and PropertyRunner: re-run a program under boundary
combinations of the env vars it reads and check the oracle on each plan."""

from typing import Optional

import ast
import itertools

from act.core.mock_generator import MockGenerator
from act.core.violations import Violation

# Per-variable boundary values: unset, empty, and a representative non-empty value.
_ENV_BOUNDARY_VALUES: tuple = (None, "", "act-fuzz")


def deduplicate(violations: list[Violation], seen: set) -> list[Violation]:
    """Return violations whose (field, message) key is new; adds new keys to seen in-place."""
    new = []
    for v in violations:
        key = (v.field, v.message)
        if key not in seen:
            seen.add(key)
            new.append(v)
    return new


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


def build_env_strategy(var_names: list[str]):
    """Return a hypothesis strategy assigning each env var unset / empty / arbitrary text."""
    from hypothesis import strategies as st

    per_var = st.one_of(st.none(), st.just(""), st.text(max_size=32))
    return st.fixed_dictionaries({name: per_var for name in var_names})


def check_env(mock_generator, oracle, program_path: str, env: dict, seen: set) -> list[Violation]:
    """Re-run the program under one env assignment; return new oracle violations."""
    try:
        # A program that raises on some input is not itself a policy violation.
        outputs = mock_generator.run_with_mocks(program_path, env=env)
    except Exception:
        return []
    found: list[Violation] = []
    for name, resource_outputs in outputs.items():
        token = mock_generator.get_resource_type(name)
        if token:
            found.extend(deduplicate(oracle.check(token, resource_outputs), seen))
    return found


def explore_env_inputs(mock_generator, oracle, program_path: str, env_combos) -> list[Violation]:
    """Run the oracle across each env combination; return deduped violations."""
    seen: set = set()
    found: list[Violation] = []
    for env in env_combos:
        found.extend(check_env(mock_generator, oracle, program_path, env, seen))
    return found

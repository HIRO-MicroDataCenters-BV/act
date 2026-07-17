from typing import Any, Optional

import ast
import asyncio
import contextlib
import importlib.util
import io
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path

import pulumi
import pulumi.runtime

log = logging.getLogger(__name__)

DEFAULT_EXEC_TIMEOUT_S = 30


def _import_aliases(tree: ast.AST) -> dict:
    """Map a local alias to its original imported name (`from m import Orig as Alias`)."""
    aliases: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
    return aliases


def _set_or_unset_env(key: str, value: Optional[str]) -> None:
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


@contextlib.contextmanager
def _env_overrides(overrides: Optional[dict]):
    """Temporarily apply os.environ overrides (value None unsets the var); restore on exit."""
    if not overrides:
        yield
        return
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            _set_or_unset_env(k, v)
        yield
    finally:
        for k, v in saved.items():
            _set_or_unset_env(k, v)


@contextlib.contextmanager
def _exec_timeout(seconds: float):
    """Best-effort wall-clock cap on program execution (SIGALRM; Unix main thread only)."""
    usable = seconds > 0 and hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()
    if not usable:
        yield
        return

    def _raise(signum, frame):
        raise TimeoutError(f"program execution exceeded {seconds:g}s")

    prev = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prev)


class MockGenerator:
    """Generates pulumi.runtime.Mocks from any Pulumi provider schema.

    Reads the schema to discover resource types, then intercepts
    CustomResource registrations at the Pulumi SDK level.
    """

    def __init__(self, schema_path: str | list[str], exec_timeout_s: float = DEFAULT_EXEC_TIMEOUT_S):
        paths = [schema_path] if isinstance(schema_path, str) else schema_path
        merged_resources: dict = {}
        for p in paths:
            with open(p) as f:
                merged_resources.update(json.load(f).get("resources", {}))
        self._schema = {"resources": merged_resources}
        self._schema_path = paths
        self._exec_timeout_s = exec_timeout_s
        self._type_map = self._build_type_map()

    def _build_type_map(self) -> dict:
        """Map class name (last token segment) -> {token, inputs, outputs, required}."""
        result: dict = {}
        for token, resource in self._schema.get("resources", {}).items():
            class_name = token.split(":")[-1]
            existing = result.get(class_name)
            # Only a cross-provider clash is ambiguous; the same class across a provider's own
            # API versions (e.g. kubernetes apps/v1 vs apps/v1beta1) is expected, not a collision.
            if existing and existing["token"].split(":")[0] != token.split(":")[0]:
                log.warning(
                    "mock_generator.class_name_collision class=%s tokens=%s,%s",
                    class_name,
                    existing["token"],
                    token,
                )
            result[class_name] = {
                "token": token,
                "inputs": resource.get("inputProperties", {}),
                "outputs": resource.get("properties", {}),
                "required": resource.get("requiredInputs", []),
            }
        return result

    def _default_for_type(self, prop_schema: dict) -> Any:
        """Return a default value for a schema property type."""
        t = prop_schema.get("type", "string")
        if t == "string":
            return ""
        if t == "integer":
            return 0
        if t == "boolean":
            return False
        if t == "array":
            return []
        if t == "object":
            return {}
        return None

    def _detect_resource_types(self, program_path: str) -> set:
        """Return known resource class names used in a program (via AST, no execution)."""
        with open(self._entry_point(program_path)) as f:
            tree = ast.parse(f.read())

        aliases = _import_aliases(tree)
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name is None:
                continue
            resolved = aliases.get(name, name)  # `from m import Instance as VM` -> resolve VM to Instance
            if resolved in self._type_map:
                found.add(resolved)
        return found

    def generate(self, program_path: str) -> type:
        """Return a pulumi.runtime.Mocks subclass that merges schema-derived
        default outputs with actual input values on new_resource calls."""
        detected = self._detect_resource_types(program_path)
        type_map = self._type_map

        # Defaults only for computed outputs (not inputs) so missing fields stay missing.
        token_defaults: dict[str, dict] = {}
        for class_name in detected:
            info = type_map[class_name]
            input_names = set(info["inputs"].keys())
            defaults: dict[str, Any] = {}
            for prop_name, prop_schema in info["outputs"].items():
                if prop_name in input_names:
                    continue
                if prop_name == "status":
                    defaults["status"] = "active"
                elif prop_name == "metadata":
                    defaults["metadata"] = {"name": "mock-resource"}
                else:
                    defaults[prop_name] = self._default_for_type(prop_schema)
            token_defaults[info["token"]] = defaults

        class GeneratedMock(pulumi.runtime.Mocks):
            def new_resource(self, args: pulumi.runtime.MockResourceArgs):
                defaults = token_defaults.get(args.typ, {})
                outputs = {**defaults, **args.inputs}
                return args.name, outputs

            def call(self, args: pulumi.runtime.MockCallArgs):
                return {}

        return GeneratedMock

    def get_resource_type(self, resource_name: str) -> str | None:
        """Return the Pulumi token for a resource name captured by the last run_with_mocks call."""
        return self._recorded_types.get(resource_name)

    @staticmethod
    def _entry_point(program_path: str) -> str:
        """Return the .py entry point: directory -> __main__.py, file unchanged."""
        p = Path(program_path)
        return str(p / "__main__.py") if p.is_dir() else str(p)

    def run_with_mocks(self, program_path: str, env: Optional[dict[str, Optional[str]]] = None) -> dict:
        """Run a program (file or project dir) under mocks; return {resource name -> outputs}.

        env: os.environ overrides applied during execution (value None unsets the var),
        so a parameterised program can be re-run under varied inputs.
        """
        program_path = self._entry_point(program_path)
        MockClass = self.generate(program_path)
        recorded: dict[str, dict] = {}
        recorded_types: dict[str, str] = {}

        class RecordingMock(MockClass):  # type: ignore[valid-type,misc]
            def new_resource(self, args: pulumi.runtime.MockResourceArgs):
                name, outputs = super().new_resource(args)
                recorded[name] = outputs
                recorded_types[name] = args.typ
                return name, outputs

        program_dir = str(Path(program_path).parent)

        async def _execute():
            pulumi.runtime.set_mocks(RecordingMock(), preview=False)
            sys.path.insert(0, program_dir)
            try:
                spec = importlib.util.spec_from_file_location("_act_prog", program_path)
                if spec is None or spec.loader is None:
                    raise RuntimeError(f"Cannot load program: {program_path}")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                # Drain registration tasks scheduled by CustomResource.__init__
                pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            finally:
                if program_dir in sys.path:
                    sys.path.remove(program_dir)
                sys.modules.pop("_act_prog", None)

        log.debug("mock_generator.start", extra={"program": program_path})
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Divert the program's own stdout so its prints don't pollute ACT's report
            # or corrupt the canonical JSON the plan-determinism subprocess emits.
            with contextlib.redirect_stdout(io.StringIO()), _env_overrides(env), _exec_timeout(self._exec_timeout_s):
                loop.run_until_complete(_execute())
        finally:
            asyncio.set_event_loop(None)
            loop.close()

        self._recorded_types = recorded_types
        log.debug("mock_generator.done", extra={"resources": list(recorded)})
        return recorded

import ast
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pulumi
import pulumi.runtime


class MockGenerator:
    """Generates pulumi.runtime.Mocks from any Pulumi provider schema.

    Works with any provider — reads the schema file to discover resource types,
    then intercepts CustomResource registrations at the Pulumi SDK level.
    """

    def __init__(self, schema_path: str):
        self._schema_path = schema_path
        with open(schema_path) as f:
            self._schema = json.load(f)
        self._type_map = self._build_type_map()

    def _build_type_map(self) -> dict:
        """Build a map from class name to resource metadata.

        Key: last segment of the Pulumi token (e.g. "Instance", "Database").
        Value: {token, inputs, outputs, required}
        """
        result = {}
        for token, resource in self._schema.get("resources", {}).items():
            class_name = token.split(":")[-1]
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
        """Return the set of known resource class names used in a program.

        Uses AST analysis — no execution required.
        """
        with open(self._entry_point(program_path)) as f:
            tree = ast.parse(f.read())

        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name and name in self._type_map:
                found.add(name)
        return found

    def generate(self, program_path: str) -> type:
        """Return a pulumi.runtime.Mocks subclass for the given program.

        The generated class intercepts new_resource calls and returns
        schema-derived default outputs merged with actual input values.
        """
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
        """Return the .py entry point — directory → __main__.py, file → unchanged."""
        p = Path(program_path)
        return str(p / "__main__.py") if p.is_dir() else str(p)

    def run_with_mocks(self, program_path: str) -> dict:
        """Run a Pulumi program under mocks and return captured resource outputs.

        Accepts a single .py file or a project directory (uses __main__.py).
        Returns a dict mapping resource name to its output dict.
        """
        program_path = self._entry_point(program_path)
        MockClass = self.generate(program_path)
        recorded: dict[str, dict] = {}
        recorded_types: dict[str, str] = {}

        class RecordingMock(MockClass):
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
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                # Drain all resource registration tasks scheduled by CustomResource.__init__
                pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            finally:
                if program_dir in sys.path:
                    sys.path.remove(program_dir)
                sys.modules.pop("_act_prog", None)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_execute())
        finally:
            asyncio.set_event_loop(None)
            loop.close()

        self._recorded_types = recorded_types
        return recorded

"""Shared helpers for FuzzRunner and PropertyRunner."""

import copy

from act.core.violations import Violation

# Boundary values to try per schema field type
_BOUNDARY_VALUES: dict[str, list] = {
    "string": ["", None],
    "integer": [-1, 0, 99999, None],
    "boolean": [None],
    "array": [[], None],
    "object": [{}, None],
}


def collect_resource_info(mock_generator, program_path: str) -> list[tuple]:
    """Run the program once and return [(token, resource_name, base_outputs)].

    base_outputs is the outputs dict captured by run_with_mocks for that resource.
    token is the full Pulumi token (e.g. "cape:compute:Instance").
    """
    outputs = mock_generator.run_with_mocks(program_path)
    result = []
    for name, resource_outputs in outputs.items():
        token = mock_generator.get_resource_type(name)
        if token:
            result.append((token, name, resource_outputs))
    return result


def generate_mutations(base_outputs: dict, schema_inputs: dict) -> list[dict]:
    """Return a list of mutated copies of base_outputs.

    One mutation per (field, boundary_value) pair derived from schema_inputs.
    Only top-level fields declared in the schema are mutated.
    """
    mutations = []
    for field, prop_schema in schema_inputs.items():
        field_type = prop_schema.get("type", "string")
        for val in _BOUNDARY_VALUES.get(field_type, [None]):
            mutated = copy.deepcopy(base_outputs)
            mutated[field] = val
            mutations.append(mutated)
    return mutations


def deduplicate(violations: list[Violation], seen: set) -> list[Violation]:
    """Return violations whose (field, message) key is not already in seen.

    Adds new keys to seen in-place.
    """
    new = []
    for v in violations:
        key = (v.field, v.message)
        if key not in seen:
            seen.add(key)
            new.append(v)
    return new


def _atheris_mutate(base_outputs: dict, schema_inputs: dict, fdp) -> dict:
    """Apply a single schema-aware mutation chosen by an atheris FuzzedDataProvider.

    fdp: atheris.FuzzedDataProvider instance.
    Returns a mutated copy of base_outputs.
    """
    fields = list(schema_inputs.items())
    if not fields:
        return copy.deepcopy(base_outputs)

    field_idx = fdp.ConsumeIntInRange(0, len(fields) - 1)
    field, prop_schema = fields[field_idx]
    field_type = prop_schema.get("type", "string")
    boundary_values = _BOUNDARY_VALUES.get(field_type, [None])

    val_idx = fdp.ConsumeIntInRange(0, len(boundary_values) - 1)
    val = boundary_values[val_idx]

    mutated = copy.deepcopy(base_outputs)
    mutated[field] = val
    return mutated


def build_field_strategy(prop_schema: dict):
    """Return a hypothesis strategy for a single schema field."""
    from hypothesis import strategies as st

    field_type = prop_schema.get("type", "string")
    enum_values = prop_schema.get("enum")
    if enum_values:
        return st.sampled_from(enum_values + [None])

    minimum = prop_schema.get("minimum")
    maximum = prop_schema.get("maximum")

    if field_type == "string":
        return st.one_of(st.just(""), st.just(None), st.text(max_size=64))
    if field_type == "integer":
        min_val = minimum if minimum is not None else -999
        max_val = maximum if maximum is not None else 99999
        return st.one_of(
            st.just(None),
            st.just(-1),
            st.just(0),
            st.integers(min_value=min_val, max_value=max_val),
        )
    if field_type == "boolean":
        return st.one_of(st.booleans(), st.just(None))
    if field_type == "array":
        return st.one_of(st.just([]), st.just(None), st.lists(st.text(), max_size=5))
    if field_type == "object":
        return st.one_of(st.just({}), st.just(None))
    return st.just(None)


def build_strategy(base_outputs: dict, schema_inputs: dict):
    """Return a hypothesis strategy that generates mutations of base_outputs.

    Each field declared in schema_inputs is varied independently.
    Fields not in the schema stay at their base value.
    """
    from hypothesis import strategies as st

    field_strategies: dict = {}
    for field, prop_schema in schema_inputs.items():
        base_val = base_outputs.get(field)
        field_strategies[field] = st.one_of(
            st.just(base_val),
            build_field_strategy(prop_schema),
        )
    for field, val in base_outputs.items():
        if field not in field_strategies:
            field_strategies[field] = st.just(val)
    return st.fixed_dictionaries(field_strategies)

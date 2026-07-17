from act.core._runner_utils import discover_env_vars, generate_env_combinations


def test_discover_env_vars_finds_fixture_vars(path_b_fixture):
    assert set(discover_env_vars(str(path_b_fixture))) == {
        "CAPE_ZONE",
        "CAPE_SSH_KEYS",
        "CAPE_SECURITY_GROUP_REF",
    }


def test_discover_env_vars_recognizes_all_read_forms(tmp_path):
    prog = tmp_path / "prog.py"
    prog.write_text("import os\na = os.getenv('A')\nb = os.environ['B']\nc = os.environ.get('C')\n")
    assert set(discover_env_vars(str(prog))) == {"A", "B", "C"}


def test_generate_env_combinations_full_cartesian_when_small():
    combos = generate_env_combinations(["X", "Y", "Z"])
    assert len(combos) == 27  # 3 boundary values ** 3 vars
    assert {"X": None, "Y": "act-fuzz", "Z": None} in combos


def test_generate_env_combinations_one_at_a_time_when_large():
    combos = generate_env_combinations(["A", "B", "C", "D", "E"])  # 3**5 = 243 > cap
    assert len(combos) == 1 + 5 * 2  # baseline + two non-default values per var
    assert {k: None for k in "ABCDE"} in combos

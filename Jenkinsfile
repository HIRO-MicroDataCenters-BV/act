// Jenkins mirror of .github/workflows/test.yaml: lint, type-check, test, and run the ACT gate.
// One of the CI/CD platforms ACT integrates with (alongside GitHub Actions and GitLab CI).
// Docker/Pulumi-only checks (cross-arch boot, kubernetes schema) are omitted here; those tests
// skip gracefully without docker/pulumi, exactly as they do locally.
pipeline {
    agent {
        docker {
            image 'ghcr.io/astral-sh/uv:python3.11-bookworm'
            args '-u root'
        }
    }

    environment {
        UV_CACHE_DIR = "${WORKSPACE}/.uv-cache"
    }

    stages {
        stage('Setup') {
            steps {
                // git is needed for the cape-sdks submodule (provides pulumi_cape).
                sh 'command -v git >/dev/null || (apt-get update && apt-get install -y --no-install-recommends git)'
                sh 'git submodule update --init cape-sdks'
                sh 'uv sync --frozen --dev --extra fuzzing --extra acv'
                sh 'uv pip install pip'
            }
        }
        stage('Lint') {
            steps {
                sh 'uv run isort act/ tests/ --check --diff'
                sh 'uv run black act/ tests/ --check --diff'
                sh 'uv run flake8 --config ./.flake8 act/ tests/'
                sh 'uv run mypy act/ tests/ --ignore-missing-imports --no-error-summary'
            }
        }
        stage('Test') {
            steps {
                sh 'uv run pytest -v'
            }
        }
        stage('Gate') {
            steps {
                // ACT gate on a known-good program: exits 0 (green). ACV is unconfigured here, so it skips.
                sh 'uv run python -m act.run --program tests/fixtures/cape/path_a_valid.py --schema tests/fixtures/cape/schema.json'
            }
        }
    }
}

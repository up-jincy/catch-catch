import os
import shutil
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _normalized(path: Path) -> str:
    content = path.read_text(encoding="utf-8").replace("\\\n", " ")
    return " ".join(content.split())


def test_dev_launcher_loads_env_before_python_process_starts() -> None:
    launcher = _normalized(REPOSITORY_ROOT / "scripts" / "dev.sh")

    assert '"${env_isolation[@]}" uv run "${env_args[@]}" --project backend uvicorn' in launcher
    assert "env -u LANGSMITH_PROJECT" in launcher


def test_fixture_launcher_loads_env_before_python_process_starts() -> None:
    makefile = _normalized(REPOSITORY_ROOT / "Makefile")

    assert (
        '"$${env_isolation[@]}" uv run "$${env_args[@]}" --project backend uvicorn'
        in makefile
    )
    assert "env -u LANGSMITH_PROJECT" in makefile


def test_frontend_launchers_strip_provider_and_tracing_settings() -> None:
    launcher = _normalized(REPOSITORY_ROOT / "scripts" / "dev.sh")
    makefile = _normalized(REPOSITORY_ROOT / "Makefile")

    assert '"${frontend_env_isolation[@]}" npm --prefix frontend run dev' in launcher
    assert 'env -u GEMINI_API_KEY -u GOOGLE_API_KEY' in launcher
    assert 'env -u GEMINI_API_KEY -u GOOGLE_API_KEY' in makefile
    assert '-u LANGSMITH_API_KEY' in launcher
    assert '-u LANGCHAIN_API_KEY' in launcher
    assert '-u LANGSMITH_API_KEY' in makefile
    assert '-u LANGCHAIN_API_KEY' in makefile


def test_uv_env_file_enables_langsmith_in_a_clean_subprocess(tmp_path: Path) -> None:
    env_file = tmp_path / "launcher.env"
    env_file.write_text(
        "LANGSMITH_TRACING=true\n"
        "LANGSMITH_API_KEY=test-key\n"
        "LANGSMITH_PROJECT=launcher-test\n",
        encoding="utf-8",
    )
    clean_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LANGSMITH_", "LANGCHAIN_"))
    }
    uv = shutil.which("uv")
    assert uv is not None

    result = subprocess.run(
        [
            uv,
            "run",
            "--env-file",
            str(env_file),
            "--project",
            "backend",
            "python",
            "-c",
            (
                "from langsmith.utils import tracing_is_enabled; "
                "print(tracing_is_enabled())"
            ),
        ],
        cwd=REPOSITORY_ROOT,
        env=clean_environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "True"

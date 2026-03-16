from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_requirements_enable_httpx_socks_support():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx[http2,socks]==0.28.1" in requirements


def test_pyproject_enable_httpx_socks_support():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"httpx[http2,socks]==0.28.1"' in pyproject

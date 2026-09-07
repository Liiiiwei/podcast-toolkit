"""確認套件、App 與打包腳本使用同一個版本來源。"""

from pathlib import Path

from podcast_toolkit.version import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_package_version_is_the_single_declared_version():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    setup_app = (ROOT / "setup_app.py").read_text(encoding="utf-8")
    build_app = (ROOT / "build_app.sh").read_text(encoding="utf-8")

    assert f'__version__ = "{__version__}"' in (
        ROOT / "podcast_toolkit" / "version.py"
    ).read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = {attr = "podcast_toolkit.version.__version__"}' in pyproject
    assert "from podcast_toolkit.version import __version__" in setup_app
    assert 'CFBundleShortVersionString": __version__' in setup_app
    assert "from podcast_toolkit.version import __version__" in build_app

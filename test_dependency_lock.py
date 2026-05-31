from pathlib import Path


ROOT = Path(__file__).parent


def test_desktop_build_uses_locked_requirements():
    installer_dir = ROOT / "installer"
    lock_text = (installer_dir / "requirements-lock.txt").read_text(encoding="utf-8")
    build_text = (installer_dir / "build.bat").read_text(encoding="utf-8")
    workflow_text = (ROOT / ".github" / "workflows" / "build-release.yml").read_text(encoding="utf-8")

    assert (installer_dir / "requirements.in").exists()
    assert "-r requirements.in" in (installer_dir / "requirements.txt").read_text(encoding="utf-8")
    assert "playwright==1.58.0" in lock_text
    assert "cryptography==48.0.0" in lock_text
    assert "pyinstaller==6.20.0" in lock_text
    assert "pip install --no-deps -r \"%REQ_LOCK%\"" in build_text
    assert "REQ_FALLBACK" not in build_text
    assert "pip install -r \"%REQ_FALLBACK%\"" not in build_text
    assert "requirements-lock.txt" in workflow_text

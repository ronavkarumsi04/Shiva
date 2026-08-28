from pathlib import Path


def test_windows_native_install_path_docs_match_installer() -> None:
    doc = Path("website/docs/user-guide/windows-native.md").read_text()
    install = Path("scripts/install.ps1").read_text()

    # The launchers live in the managed binary dir OUTSIDE the git checkout
    # (SHIVA_HOME\bin, next to the managed uv) — NOT the whole venv\Scripts
    # (which would shadow the user's python, #83797) and NOT a dir inside
    # the checkout (which `shiva update`'s autostash swept off disk).
    assert "%LOCALAPPDATA%\\shiva\\bin" in doc
    assert (
        "Get-Command shiva        # should print "
        "C:\\Users\\<you>\\AppData\\Local\\shiva\\bin\\shiva.exe"
    ) in doc
    # Installer exposes $ShivaHome\bin, and must copy the launchers into it.
    assert '$shivaBin = "$ShivaHome\\bin"' in install
    assert "shiva.exe" in install and "shiva-acp.exe" in install
    # Guard against regressions to either legacy layout.
    assert '$shivaBin = "$InstallDir\\venv\\Scripts"' not in install
    assert '$shivaBin = "$InstallDir\\bin"' not in install

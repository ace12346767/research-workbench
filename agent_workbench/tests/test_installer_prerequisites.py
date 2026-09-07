"""Compile and execute the same Pascal policy used by the installer."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize('filename,executable,expected', [
    ('installer_prerequisites.iss', 'prerequisite-policy-test.exe', '14 policy checks passed'),
    ('installer_flow.iss', 'flow-test.exe', '16 flow checks passed'),
])
def test_native_installer_prerequisite_policy(tmp_path, filename, executable, expected):
    compiler = shutil.which('ISCC.exe') or 'D:/Tools/Inno Setup 6/ISCC.exe'
    if sys.platform != 'win32' or not Path(compiler).is_file():
        pytest.skip('Inno Setup compiler required for native policy checks')
    source = Path(__file__).with_name(filename)
    compiled = subprocess.run([compiler, '/Q', '/O' + str(tmp_path), str(source)],
                              capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    report = tmp_path / 'report.txt'
    subprocess.run([str(tmp_path / executable), '/VERYSILENT',
                    '/SUPPRESSMSGBOXES', '/REPORT=' + str(report)], timeout=30)
    assert report.read_text() == expected


def test_native_installer_existing_dependencies(tmp_path):
    compiler = shutil.which('ISCC.exe') or 'D:/Tools/Inno Setup 6/ISCC.exe'
    if sys.platform != 'win32' or not Path(compiler).is_file():
        pytest.skip('Inno Setup compiler required for native preflight check')
    source = Path(__file__).with_name('installer_preflight.iss')
    compiled = subprocess.run([compiler, '/Q', '/O' + str(tmp_path), str(source)],
                              capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    report = tmp_path / 'report.txt'
    log = tmp_path / 'setup.log'
    result = subprocess.run([str(tmp_path / 'preflight-test.exe'), '/VERYSILENT',
                             '/SUPPRESSMSGBOXES', '/NORESTART', '/REPORT=' + str(report),
                             '/LOG=' + str(log)], timeout=30)
    assert result.returncode == 0, log.read_text(encoding='utf-8-sig', errors='replace')
    assert report.read_text() == 'native preflight passed'


def test_standard_release_uses_bootstrapper_with_explicit_consent():
    project = Path(__file__).resolve().parents[1]
    setup = (project / 'installer.iss').read_text(encoding='utf-8')
    platform = (project / 'installer/prerequisite_platform.iss').read_text(encoding='utf-8')
    flow = (project / 'installer/prerequisites.iss').read_text(encoding='utf-8')
    assert 'MicrosoftEdgeWebview2Setup.exe' in setup
    assert 'MicrosoftEdgeWebview2Setup.exe' in platform
    assert 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe' not in setup + platform
    assert 'CreateInputOptionPage' in flow
    assert 'DependencyPage.Values[0] := False' in flow
    assert 'DownloadTemporaryFile' not in platform + flow

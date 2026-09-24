"""Exercise actual package assembly and PowerShell cleanup in disposable workspaces."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PWSH = os.environ.get('PWSH') or shutil.which('pwsh')
spec = importlib.util.spec_from_file_location('console_packaging', ROOT / 'scripts/package_deploy_console.py')
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


class PackageAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stage = self.root / '.local' / ('package-' + 'a' * 32)
        self.payload = self.stage / 'deploy-console-upload-test'
        self.published = self.payload / 'published'
        (self.published / 'wwwroot/assets').mkdir(parents=True)
        for name in ('DeployConsole.Api.dll', 'DeployConsole.Api.deps.json', 'DeployConsole.Api.runtimeconfig.json'):
            (self.published / name).write_text('isolated fixture')
        (self.published / 'wwwroot/index.html').write_text('<script src="/assets/index-test.js"></script>')
        (self.published / 'wwwroot/assets/index-test.js').write_text('fixture')
        shutil.copytree(ROOT / 'deploy/console', self.root / 'deploy/console', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / 'docs', self.root / 'docs')

    def test_archive_matches_payload_and_upgrade_targets_current_version(self):
        packaging.package(self.root, self.stage, 'test')
        delivery = self.stage / 'delivery'
        self.assertEqual({p.name for p in delivery.iterdir()}, {'deploy-console-server-test.tar.gz', 'upgrade-deploy-console.sh', 'DEPLOY.md'})
        with tarfile.open(delivery / 'deploy-console-server-test.tar.gz') as tar:
            for member in tar.getmembers():
                self.assertTrue(member.isfile()); self.assertEqual(member.uid, 0); self.assertEqual(member.mode, 0o644)
                self.assertEqual(tar.extractfile(member).read(), (self.stage / member.name).read_bytes())
        upgrade = (delivery / 'upgrade-deploy-console.sh').read_bytes()
        self.assertIn(b'archive=${1:-/root/deploy-console-server-test.tar.gz}', upgrade)
        self.assertNotIn(b'\r', upgrade); self.assertNotIn(b'sha256sum', upgrade)
        self.assertEqual(json.loads((self.payload / 'BUILD.json').read_text())['version'], 'test')

    def test_incomplete_or_sensitive_publish_never_produces_delivery(self):
        (self.published / 'wwwroot/assets/index-test.js').unlink()
        with self.assertRaisesRegex(ValueError, 'frontend asset'): packaging.package(self.root, self.stage, 'test')
        self.assertFalse((self.stage / 'delivery').exists())
        (self.published / 'wwwroot/assets/index-test.js').write_text('fixture')
        (self.published / 'password.txt').write_text('fixture only')
        with self.assertRaisesRegex(ValueError, 'sensitive'): packaging.package(self.root, self.stage, 'test')
        self.assertFalse((self.stage / 'delivery').exists())

    def test_invalid_stage_and_version_are_rejected(self):
        with self.assertRaises(ValueError): packaging.package(self.root, self.root, 'test')
        with self.assertRaises(ValueError): packaging.package(self.root, self.stage, '../outside')


@unittest.skipUnless(PWSH, 'PowerShell 7.4+ is required')
class PackageFlowTests(unittest.TestCase):
    def run_flow(self, mode='success', version='first'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        shutil.copytree(ROOT / 'scripts', root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / 'deploy/console', root / 'deploy/console', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / 'docs', root / 'docs')
        artifacts = root / 'deployment-artifacts'; artifacts.mkdir()
        (artifacts / 'deploy-console-server-old.tar.gz').write_bytes(b'KEEP UNTIL SUCCESS')
        (artifacts / 'deploy-console-server-old.tar.gz.sha256').write_text('old checksum')
        (artifacts / 'deploy-console-onboarding-old.zip').write_text('old delivery')
        (artifacts / 'deploy-console-upload-old').mkdir()
        (artifacts / 'deploy-console-upload-old/content.txt').write_text('old published copy')
        if mode == 'unknown': (artifacts / 'my-notes.txt').write_text('KEEP')
        outside = root / 'unrelated'; outside.mkdir(); (outside / 'keep.txt').write_text('KEEP')
        if mode == 'linked':
            (artifacts / 'deploy-console-upload-old/link').symlink_to(outside, target_is_directory=True)
        script = root / 'run.ps1'
        script.write_text(r'''
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/scripts/Package-DeployConsole.ps1"
function Invoke-PackageCommand {
    param([string]$Program, [string[]]$CommandArguments)
    if ($Program -eq 'python') {
        & $env:PYTHON_FOR_PACKAGE_TEST @CommandArguments
        if ($LASTEXITCODE -ne 0) { throw 'archive verification failed' }
    } elseif ($Program -eq 'dotnet' -and $CommandArguments[0] -eq 'publish') {
        if ($env:PACKAGE_TEST_MODE -eq 'build-failed') { throw 'mock build failed' }
        $index = [Array]::IndexOf($CommandArguments, '-o')
        $output = $CommandArguments[$index + 1]
        [void][IO.Directory]::CreateDirectory((Join-Path $output 'wwwroot/assets'))
        foreach ($name in @('DeployConsole.Api.dll','DeployConsole.Api.deps.json','DeployConsole.Api.runtimeconfig.json')) {
            [IO.File]::WriteAllText((Join-Path $output $name), 'fixture')
        }
        [IO.File]::WriteAllText((Join-Path $output 'wwwroot/index.html'), '<script src="/assets/index-test.js"></script>')
        if ($env:PACKAGE_TEST_MODE -ne 'verify-failed') { [IO.File]::WriteAllText((Join-Path $output 'wwwroot/assets/index-test.js'), 'fixture') }
    }
}
Invoke-DeployConsolePackage -ProjectRoot $PSScriptRoot -PackageVersion 'first'
if ($env:PACKAGE_TEST_MODE -eq 'twice') { Invoke-DeployConsolePackage -ProjectRoot $PSScriptRoot -PackageVersion 'second' }
if ($env:PACKAGE_TEST_MODE -eq 'duplicate') { Invoke-DeployConsolePackage -ProjectRoot $PSScriptRoot -PackageVersion 'first' }
''', encoding='utf-8')
        import sys
        result = subprocess.run([PWSH, '-NoProfile', '-File', str(script)], env={**os.environ, 'PACKAGE_TEST_MODE': mode, 'PYTHON_FOR_PACKAGE_TEST': sys.executable},
                                capture_output=True, text=True, encoding='utf-8', timeout=45)
        self.assertEqual((outside / 'keep.txt').read_text(), 'KEEP')
        self.assertFalse(list((root / '.local').glob('package-*')))
        return result, artifacts

    def test_success_retains_only_latest_three_files_and_replaces_on_next_run(self):
        for mode, version in [('success', 'first'), ('twice', 'second')]:
            with self.subTest(mode=mode):
                result, artifacts = self.run_flow(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual({p.name for p in artifacts.iterdir()}, {'deploy-console-server-' + version + '.tar.gz', 'upgrade-deploy-console.sh', 'DEPLOY.md'})

    def test_build_or_verify_failure_keeps_previous_outputs(self):
        for mode in ('build-failed', 'verify-failed'):
            with self.subTest(mode=mode):
                result, artifacts = self.run_flow(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((artifacts / 'deploy-console-server-old.tar.gz').read_bytes(), b'KEEP UNTIL SUCCESS')
                self.assertTrue((artifacts / 'deploy-console-upload-old/content.txt').exists())

    def test_unknown_file_prevents_all_cleanup(self):
        result, artifacts = self.run_flow('unknown')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((artifacts / 'my-notes.txt').read_text(), 'KEEP')
        self.assertTrue((artifacts / 'deploy-console-upload-old/content.txt').exists())

    def test_link_inside_old_directory_cannot_escape_cleanup(self):
        result, artifacts = self.run_flow('linked')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((artifacts / 'deploy-console-server-old.tar.gz').exists())

    def test_existing_version_is_not_overwritten(self):
        result, artifacts = self.run_flow('duplicate')
        self.assertNotEqual(result.returncode, 0)
        with tarfile.open(artifacts / 'deploy-console-server-first.tar.gz') as tar:
            self.assertTrue(tar.getmembers())

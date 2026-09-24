"""Run upgrade flows with isolated files and mocked installation/network/removal."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which('bash') if os.name != 'nt' else next((str(Path(base) / 'Git/bin/bash.exe') for base in
    ('D:/Program Files', 'C:/Program Files') if (Path(base) / 'Git/bin/bash.exe').is_file()), None)
VERSIONS = {'20260924-delete-options': 'upgrade', '20260924-delete-legacy': 'delete', '20260924-https-acme': 'https',
            '20260924-ports': 'ports', '20260924-start-all': 'start-all'}
MOCKS = r'''
sha256sum() { echo 'CHECKSUM MUST NOT BE READ' >&2; return 99; }
mktemp() { printf '%s\n' "$TEMP_RESULT"; }
tar() { printf '%s\n' extract >> "$TRACE"; [[ "$CASE" != extract-failed ]]; }
bash() { printf '%s\n' install >> "$TRACE"; [[ "$CASE" != install-failed ]]; }
curl() {
  printf '%s\n' health >> "$TRACE"
  case "$CASE" in
    http-failed) return 7;;
    unhealthy) printf '%s' '{"status":"unhealthy"}';;
    malformed) printf '%s' not-json;;
    *) printf '%s' '{"status":"healthy"}';;
  esac
}
python3() {
  if [[ "$*" == *'/proc/self/mountinfo'* ]]; then [[ "$CASE" != mounted-directory ]];
  else "$PYTHON_FOR_TEST" "$@"; fi
}
rm() {
  printf 'cleanup:%s\n' "$*" >> "$TRACE"
  [[ "$CASE" != cleanup-failed ]]
}
'''


@unittest.skipUnless(BASH, 'Bash is required')
class UpgradeInstructionsTests(unittest.TestCase):
    def run_flow(self, case, version='20260924-delete-legacy'):
        source = (ROOT / 'deploy/console/upgrade.sh').read_text(encoding='utf-8')
        self.assertNotIn('sha256sum', source)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            posix = subprocess.run([BASH, '-c', 'cd -- "$1" && pwd', 'test', str(base)],
                                   check=True, capture_output=True, text=True).stdout.strip()
            payload = 'deploy-console-upload-' + version
            archive = 'deploy-console-server-' + version + '.tar.gz'
            if not case.startswith('missing'):
                (base / archive).write_text('fixture package')
            if case != 'no-checksum':
                (base / (archive + '.sha256')).write_text('INVALID: must not be read')
            for name in ('installed/wwwroot', 'helpers'):
                (base / name).mkdir(parents=True)
            (base / 'installed/DeployConsole.Api.dll').write_text('DLL')
            (base / 'installed/wwwroot/index.html').write_text('HTML')
            (base / 'helpers/host.py').write_text('HELPER')

            def create_payload(path):
                (path / 'published/wwwroot').mkdir(parents=True)
                (path / 'console').mkdir()
                (path / 'published/DeployConsole.Api.dll').write_text('DLL')
                (path / 'published/wwwroot/index.html').write_text('HTML')
                (path / 'console/host.py').write_text('HELPER')
                (path / 'console/install.sh').write_text('INSTALL')

            fresh = base / 'deploy-console-upgrade.Ab1234'
            if not case.startswith('missing'):
                create_payload(fresh / payload)
            legacy = base / ('deploy-console-' + VERSIONS[version] + '.Cd5678')
            direct = base / payload
            if case in ('missing-with-extracted', 'legacy-extras', 'legacy-different', 'legacy-symlink'):
                create_payload(legacy / payload)
                create_payload(direct)
            if case == 'legacy-extras': (legacy / 'unrelated.txt').write_text('KEEP')
            if case == 'legacy-different': (legacy / payload / 'console/host.py').write_text('NOT INSTALLED')
            if case == 'legacy-symlink':
                try: (base / ('deploy-console-' + VERSIONS[version] + '.Ef9012')).symlink_to(legacy, target_is_directory=True)
                except OSError: self.skipTest('symlink creation unavailable')
            if case == 'fresh-different': (fresh / payload / 'published/DeployConsole.Api.dll').write_text('DIFFERENT')
            (base / 'unrelated.tar.gz').write_text('KEEP')
            # Omit only the CLI root guard in this harness; no production test flag exists.
            source = source.replace("[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }", '# isolated test')
            source = source.replace('/root', posix).replace('/opt/deploy-console/current', posix + '/installed').replace('/usr/local/lib/deploy-console', posix + '/helpers')
            script = base / 'run.sh'; script.write_text(MOCKS + '\n' + source, encoding='utf-8', newline='\n')
            env = {**os.environ, 'CASE': case, 'TRACE': posix + '/trace.txt',
                   'TEMP_RESULT': posix if case == 'wrong-directory' else posix + '/' + fresh.name,
                   'PYTHON_FOR_TEST': sys.executable.replace('\\', '/')}
            result = subprocess.run([BASH, str(script), posix + '/' + archive], env=env, capture_output=True,
                                    text=True, encoding='utf-8', timeout=15)
            trace = base / 'trace.txt'; log = trace.read_text().splitlines() if trace.exists() else []
            self.assertEqual((base / 'unrelated.tar.gz').read_text(), 'KEEP')
            # rm only records arguments; no recursive shell deletion occurs.
            return result, log, posix, archive

    def test_upload_installs_without_reading_invalid_or_missing_checksum(self):
        for version in VERSIONS:
            for case in ('success', 'no-checksum'):
                with self.subTest(version=version, case=case):
                    result, log, base, archive = self.run_flow(case, version)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(log, ['extract', 'install', 'health',
                        'cleanup:-rf --one-file-system -- ' + base + '/deploy-console-upgrade.Ab1234',
                        'cleanup:-f -- ' + base + '/' + archive + ' ' + base + '/' + archive + '.sha256'])

    def test_missing_archive_skips_install_and_cleans_verified_old_extraction(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                result, log, base, _ = self.run_flow('missing-with-extracted', version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('跳过安装', result.stdout)
                self.assertNotIn('install', log); self.assertNotIn('extract', log)
                self.assertIn('cleanup:-rf --one-file-system -- ' + base + '/deploy-console-' + VERSIONS[version] + '.Cd5678', log)
                self.assertIn('cleanup:-rf --one-file-system -- ' + base + '/deploy-console-upload-' + version, log)

    def test_missing_archive_and_extractions_are_a_successful_skip(self):
        result, log, *_ = self.run_flow('missing-everything')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('install', log)
        self.assertFalse(any('cleanup:-rf' in line for line in log))

    def test_extraction_install_or_health_failure_never_cleans(self):
        for case in ('extract-failed', 'install-failed', 'http-failed', 'unhealthy', 'malformed'):
            with self.subTest(case=case):
                result, log, *_ = self.run_flow(case)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(line.startswith('cleanup:') for line in log))

    def test_extra_files_different_content_or_symlink_are_not_removed(self):
        for case in ('legacy-extras', 'legacy-different', 'legacy-symlink'):
            with self.subTest(case=case):
                result, log, base, _ = self.run_flow(case)
                self.assertEqual(result.returncode, 0, result.stderr)
                protected = 'Ef9012' if case == 'legacy-symlink' else 'Cd5678'
                self.assertFalse(any('cleanup:-rf' in line and line.endswith(protected) for line in log))

    def test_wrong_new_directory_or_content_preserves_archive(self):
        for case in ('wrong-directory', 'fresh-different', 'cleanup-failed', 'mounted-directory'):
            with self.subTest(case=case):
                result, log, *_ = self.run_flow(case)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(line.startswith('cleanup:-f ') for line in log))


if __name__ == '__main__':
    unittest.main()

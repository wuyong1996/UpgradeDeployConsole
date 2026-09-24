"""Adapter contract tests with subprocesses mocked; never controls real services."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if os.name == 'nt':
    sys.modules['fcntl'] = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None)
    sys.modules['pwd'] = types.SimpleNamespace(getpwnam=lambda name: types.SimpleNamespace(pw_uid=1001, pw_gid=1001, pw_dir='/var/lib/' + name))
spec = importlib.util.spec_from_file_location('console_host', ROOT / 'deploy/console/host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class AdapterTests(unittest.TestCase):
    def test_git_credentials_are_scoped_to_repository_and_temporary_files_are_removed(self):
        control = host.Host(self.target)
        request = self.request('check')
        request['credential'] = {'repository': request['repository'], 'username': 'unit-user', 'secret': 'unit-secret'}
        credential_directory = Path(self.temp.name) / 'git-credentials'
        original_stat = Path.stat
        def permissions(path, *args, **kwargs):
            if path == credential_directory:
                return types.SimpleNamespace(st_mode=0o40700)
            return original_stat(path, *args, **kwargs)
        def execute(side, command, cwd, timeout, credential_file=None):
            self.assertNotIn('unit-secret', str(command))
            self.assertEqual(json.loads(credential_file.read_text()), request['credential'])
            raise host.Rejected('simulated Git failure')
        with patch.object(host, 'GIT_CREDENTIALS', credential_directory), patch.object(Path, 'stat', permissions), patch.object(control, 'sandbox', side_effect=execute):
            with self.assertRaises(host.Rejected):
                control.git(request, 'check', ['ls-remote', '--', request['repository']], self.target['repositoryPath'], 30)
        self.assertEqual(list(credential_directory.iterdir()), [])

    def test_git_rejects_credentials_for_another_repository_before_running(self):
        control = host.Host(self.target)
        request = self.request('check')
        request['credential'] = {'repository': 'https://github.com/other/repo.git', 'username': 'user', 'secret': 'secret'}
        with patch.object(control, 'sandbox') as execute, self.assertRaises(host.Rejected):
            control.git(request, 'check', ['ls-remote'], self.target['repositoryPath'], 30)
        execute.assert_not_called()

    def test_git_wrapper_injects_header_only_for_exact_repository_without_inherited_config(self):
        spec = importlib.util.spec_from_file_location('git_auth', ROOT / 'deploy/console/git-auth.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        env = module.environment({'repository': 'https://github.com/team/repo.git', 'username': 'user', 'secret': 'secret'}, {'PATH': '/usr/bin', 'GIT_CONFIG_COUNT': '5', 'GIT_CONFIG_VALUE_4': 'untrusted'})
        self.assertEqual(env['GIT_CONFIG_KEY_0'], 'http.https://github.com/team/repo.git.extraHeader')
        self.assertEqual(env['GIT_ALLOW_PROTOCOL'], 'https')
        self.assertNotIn('GIT_CONFIG_VALUE_4', env)
        self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')

    def setUp(self):
        self.target = json.loads((ROOT / 'deploy/console/project.example.json').read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = patch.object(host, 'STATE', Path(self.temp.name))
        self.trust = patch.object(host, 'trusted', side_effect=lambda path, directory=False: Path(path))
        self.state.start()
        self.trust.start()
        self.addCleanup(self.state.stop)
        self.addCleanup(self.trust.stop)

    def request(self, action, side=''):
        return {'slug': 'project', 'action': action, 'side': side, 'operationId': '11111111-1111-1111-1111-111111111111', 'repository': 'https://github.com/team/project.git', 'branch': 'main'}

    def test_manifest_validates_example_and_rejects_shared_front_back_unit(self):
        host.validate_target(self.target, 'project')
        self.target['front'].update(kind='systemd', unit='project-api.service', portEnvironment='PORT')
        with self.assertRaises(host.Rejected):
            host.validate_target(self.target, 'project')

    def test_source_rejects_credentials_unknown_host_and_branch_injection(self):
        for source, branch in [('https://secret@github.com/repo', 'main'), ('https://evil.test/repo', 'main'), ('file:///etc/passwd', 'main'), ('https://github.com/repo', '--upload-pack=evil'), ('https://github.com/repo', '../main')]:
            with self.subTest(source=source), self.assertRaises(host.Rejected):
                host.validate_source(self.target, source, branch)

    def test_paths_reject_root_traversal_panel_and_overlap(self):
        for path in ['/etc/passwd', '/opt/../etc', '/opt/deploy-console/current', '/opt/project/repository/child']:
            self.target['front']['current'] = path
            with self.subTest(path=path), self.assertRaises(host.Rejected):
                host.validate_target(self.target, 'project')

    def test_stop_all_attempts_each_service_even_when_one_fails(self):
        control = host.Host(self.target)
        calls = []
        def service(side, action):
            calls.append((side, action))
            if side == 'front':
                raise host.Rejected('test failure')
        with patch.object(control, 'service', side_effect=service), patch.object(control, 'database') as database, patch.object(host, 'run'):
            with self.assertRaisesRegex(host.Rejected, '部分关停失败'):
                control.handle(self.request('stop-all'))
            self.assertEqual(calls, [('front', 'stop'), ('back', 'stop')])
            database.assert_called_once_with('stop')
            self.assertTrue(control.stopped('front') and control.stopped('back'))

    def test_external_database_blocks_stop_before_mutation(self):
        self.target['database'].update(kind='external', managed=False)
        control = host.Host(self.target)
        with patch.object(control, 'stop_request') as stop, self.assertRaises(host.Rejected):
            control.handle(self.request('stop-all'))
        stop.assert_not_called()
        self.assertFalse(control.stopped('front'))

    def stopped_project(self, pending=False):
        for side in host.SIDES:
            current = Path(self.temp.name) / (side + '-current')
            current.mkdir()
            self.target[side]['current'] = str(current)
        if pending:
            self.target['database'].update(kind='mysql', autoProvisioned=True)
        control = host.Host(self.target)
        for side in host.SIDES:
            marker = control.directory / ('stop-' + side)
            marker.touch()
            os.utime(marker, ns=(1, 1))
        if pending:
            (control.directory / 'database.json').write_text(json.dumps({'phase': 'ready', 'fresh': True, 'accessPaused': True}))
        return control

    def test_start_all_restores_database_then_backend_then_frontend(self):
        control = self.stopped_project()
        calls = []
        with patch.object(control, 'database', side_effect=lambda action: calls.append(('database', action))), patch.object(control, 'service', side_effect=lambda side, action: calls.append((side, action))), patch.object(control, 'wait_health', side_effect=lambda side: calls.append((side, 'healthy'))), patch.object(control, 'status', return_value={'ok': True}), patch.object(control, 'deploy') as deploy:
            result = control.handle(self.request('start-all'))
        self.assertTrue(result['ok'])
        self.assertEqual(calls, [('database', 'start'), ('back', 'start'), ('back', 'healthy'), ('front', 'start'), ('front', 'healthy')])
        deploy.assert_not_called()
        self.assertFalse(control.stopped('front') or control.stopped('back'))

    def test_pending_database_initializes_through_backend_publish_before_frontend(self):
        control = self.stopped_project(pending=True)
        calls = []
        with patch.object(control, 'database', side_effect=lambda action: calls.append('database')), patch.object(control, 'deploy', side_effect=lambda request, force=False: calls.append((request['side'], force))), patch.object(control, 'service', side_effect=lambda side, action: calls.append(side)), patch.object(control, 'wait_health'), patch.object(control, 'status', return_value={'ok': True}):
            control.handle(self.request('start-all'))
        self.assertEqual(calls, ['database', ('back', True), 'front'])

    def test_missing_release_is_published_on_start_all(self):
        control = self.stopped_project()
        Path(self.target['front']['current']).rmdir()
        with patch.object(control, 'database'), patch.object(control, 'service'), patch.object(control, 'wait_health'), patch.object(control, 'status', return_value={'ok': True}), patch.object(control, 'deploy') as deploy:
            control.handle(self.request('start-all'))
        self.assertEqual(deploy.call_args.args[0]['side'], 'front')
        self.assertTrue(deploy.call_args.kwargs['force'])

    def test_database_restore_failure_keeps_services_stopped(self):
        control = self.stopped_project()
        with patch.object(control, 'database', side_effect=host.Rejected('DB unavailable')), patch.object(control, 'service') as service, self.assertRaisesRegex(host.Rejected, 'DB unavailable'):
            control.handle(self.request('start-all'))
        service.assert_not_called()
        self.assertTrue(control.stopped('front') and control.stopped('back'))

    def test_backend_health_failure_prevents_starting_frontend(self):
        control = self.stopped_project()
        with patch.object(control, 'database'), patch.object(control, 'service') as service, patch.object(control, 'wait_health', side_effect=host.Rejected('unhealthy')), self.assertRaises(host.Rejected):
            control.handle(self.request('start-all'))
        service.assert_called_once_with('back', 'start')
        self.assertTrue(control.stopped('front'))

    def test_new_stop_during_database_restore_is_not_cleared(self):
        control = self.stopped_project()
        def stop_again(action):
            marker = control.directory / 'stop-back'
            os.utime(marker, ns=(host.time.time_ns() + 1000000000,) * 2)
        with patch.object(control, 'database', side_effect=stop_again), patch.object(control, 'service') as service, self.assertRaisesRegex(host.Rejected, '总开始已取消'):
            control.handle(self.request('start-all'))
        service.assert_not_called()
        self.assertTrue(control.stopped('front') and control.stopped('back'))

    def test_uncertain_database_migration_is_not_automatically_replayed(self):
        control = self.stopped_project(pending=True)
        (control.directory / 'database.json').write_text(json.dumps({'phase': 'migrating', 'fresh': True}))
        with patch.object(control, 'database') as database, patch.object(control, 'deploy') as deploy, self.assertRaisesRegex(host.Rejected, '结果需核对'):
            control.handle(self.request('start-all'))
        database.assert_not_called()
        deploy.assert_not_called()

    def test_stop_after_start_queue_but_before_helper_start_still_wins(self):
        control = self.stopped_project()
        requested_ms = host.time.time_ns() // 1000000 - 1000
        os.utime(control.directory / 'stop-back', ns=((requested_ms + 100) * 1000000,) * 2)
        with patch.object(control, 'database') as database, self.assertRaisesRegex(host.Rejected, '总开始已取消'):
            control.handle({**self.request('start-all'), 'requestedAtUnixMs': requested_ms})
        database.assert_not_called()
        self.assertTrue(control.stopped('back'))

    def test_start_all_rejects_unmanaged_database_and_side(self):
        control = self.stopped_project()
        self.target['database'].update(kind='external', managed=False)
        with patch.object(control, 'database') as database:
            for side in ('', 'back'):
                with self.subTest(side=side), self.assertRaises(host.Rejected):
                    control.handle(self.request('start-all', side))
        database.assert_not_called()

    def test_stop_front_never_stops_shared_nginx_or_backend(self):
        control = host.Host(self.target)
        with patch.object(host, 'run') as command, patch.object(control, 'nginx') as nginx, patch.object(control, 'status', return_value={'ok': True}):
            control.handle(self.request('stop', 'front'))
            nginx.assert_called_once()
            self.assertTrue(control.stopped('front'))
            self.assertFalse(control.stopped('back'))
            self.assertEqual(command.call_args_list[0].args[0], ['/usr/bin/systemctl', 'stop', 'deploy-build-project-front.service'])
            self.assertEqual(command.call_count, 1)

    def test_failed_activation_restores_previous_release(self):
        control = host.Host(self.target)
        previous = {'release': '/opt/project/releases/old', 'commit': 'a' * 40}
        with patch.object(control, 'switch') as switch, patch.object(control, 'service') as service, patch.object(control, 'wait_health', side_effect=host.Rejected('health')):
            with self.assertRaises(host.Rejected):
                control.activate('back', Path('/opt/project/releases/new'), {}, previous)
            self.assertEqual(switch.call_args_list[-1].args, ('back', Path(previous['release'])))
            self.assertEqual(service.call_args_list[-1].args, ('back', 'restart'))
            self.assertEqual(control.state, {})

    def test_first_failure_allows_retry_but_explicit_stop_still_wins(self):
        control = host.Host(self.target)
        with patch.object(control, 'switch'), patch.object(control, 'service'), patch.object(control, 'wait_health', side_effect=host.Rejected('health')):
            with self.assertRaises(host.Rejected):
                control.activate('back', Path('/opt/project/releases/first'), {}, {})
        self.assertTrue((control.directory / 'failed-back').exists())
        self.assertFalse((control.directory / 'stop-back').exists())
        with patch.object(control, 'branches', side_effect=host.Rejected('reached Git')):
            with self.assertRaisesRegex(host.Rejected, 'reached Git'):
                control.deploy(self.request('deploy', 'back'))
        self.assertFalse(control.stopped('back'))
        (control.directory / 'stop-back').touch()
        with patch.object(control, 'branches') as branches, self.assertRaisesRegex(host.Rejected, '已停止'):
            control.deploy(self.request('deploy', 'back'))
        branches.assert_not_called()

    def test_port_failure_restores_old_port(self):
        control = host.Host(self.target)
        with patch.object(control, 'tcp', return_value=False), patch.object(control, 'nginx', side_effect=[host.Rejected('config failed'), None]), patch.object(control, 'service'):
            with self.assertRaises(host.Rejected):
                control.change_port({**self.request('port', 'front'), 'port': 8081})
        self.assertEqual(control.port('front'), 8080)

    def test_unchanged_commit_does_not_build_or_restart(self):
        control = host.Host(self.target)
        request = self.request('deploy', 'front')
        source = host.hashlib.sha256((request['repository'] + '\nmain').encode()).hexdigest()
        fingerprint = host.hashlib.sha256(json.dumps({'service': self.target['front'], 'database': None}, sort_keys=True).encode()).hexdigest()
        control.state['front'] = {'commit': 'a' * 40, 'source': source, 'deploymentFingerprint': fingerprint}
        with patch.object(control, 'branches', return_value=[{'name': 'main', 'commit': 'a' * 40}]), patch.object(control, 'sandbox') as build, patch.object(control, 'service') as service:
            result = control.deploy(request)
        self.assertTrue(result['ok'])
        build.assert_not_called()
        service.assert_not_called()

    def test_changed_binding_rebuilds_even_when_commit_is_unchanged(self):
        control = host.Host(self.target)
        request = self.request('deploy', 'back')
        source = host.hashlib.sha256((request['repository'] + '\nmain').encode()).hexdigest()
        control.state['back'] = {'commit': 'a' * 40, 'source': source, 'deploymentFingerprint': 'old-binding'}
        with patch.object(control, 'branches', return_value=[{'name': 'main', 'commit': 'a' * 40}]), patch.object(control, 'git', side_effect=host.Rejected('rebuild reached')) as clone:
            with self.assertRaisesRegex(host.Rejected, 'rebuild reached'):
                control.deploy(request)
        clone.assert_called_once()

    def test_forced_initialization_does_not_skip_unchanged_commit(self):
        control = host.Host(self.target)
        request = self.request('deploy', 'back')
        source = host.hashlib.sha256((request['repository'] + '\nmain').encode()).hexdigest()
        fingerprint = host.hashlib.sha256(json.dumps({'service': self.target['back'], 'database': self.target['database']}, sort_keys=True).encode()).hexdigest()
        control.state['back'] = {'commit': 'a' * 40, 'source': source, 'deploymentFingerprint': fingerprint}
        with patch.object(control, 'branches', return_value=[{'name': 'main', 'commit': 'a' * 40}]), patch.object(control, 'git', side_effect=host.Rejected('rebuild reached')) as clone:
            with self.assertRaisesRegex(host.Rejected, 'rebuild reached'):
                control.deploy(request, force=True)
        clone.assert_called_once()

    def test_rollback_uses_recorded_release_not_web_path(self):
        control = host.Host(self.target)
        with self.assertRaisesRegex(host.Rejected, '没有可回退'):
            control.rollback(self.request('rollback', 'back'))

    def test_subprocess_output_is_bounded(self):
        result = host.run([sys.executable, '-c', "import sys; sys.stdout.write('x' * 3000000)"])
        self.assertEqual(len(result.stdout), 2097152)

    def test_subprocess_timeout_is_sanitized(self):
        with self.assertRaisesRegex(host.Rejected, '命令超时'):
            host.run([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=.1)


if __name__ == '__main__':
    unittest.main()

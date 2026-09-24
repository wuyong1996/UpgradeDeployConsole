"""Discovery and first registration regression tests; no real system services."""
import importlib.util
import errno
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import types
import unittest
from unittest.mock import patch
from test_deploy_console import host, ROOT

spec = importlib.util.spec_from_file_location('console_provision', ROOT / 'deploy/console/provision.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, path, content):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding='utf-8')

    def fixture(self):
        self.write('src/frontend/package.json', '{"scripts":{"build":"vite build"}}')
        self.write('src/frontend/package-lock.json', '{}')
        self.write('src/backend/Sample.Api/Sample.Api.csproj', '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>')

    def test_discovers_front_and_backend_without_running_repository_commands(self):
        self.fixture()
        front, back = provision.discover(self.root, host.require)
        self.assertEqual(front['directory'], 'src/frontend')
        self.assertEqual(front['output'], 'dist')
        self.assertEqual(back['assembly'], 'Sample.Api')
        self.assertEqual(back['healthMode'], 'tcp')
        self.assertEqual(back['build']['commands'][0][0:2], ['/usr/bin/dotnet', 'publish'])

    def test_manifest_selects_exact_backend_and_http_health(self):
        self.fixture()
        self.write('src/backend/Other/Other.csproj', '<Project Sdk="Microsoft.NET.Sdk.Web"/>')
        with self.assertRaisesRegex(host.Rejected, '多个'):
            provision.discover(self.root, host.require)
        self.write('deploy-console.json', json.dumps({'front': None, 'back': {'project': 'src/backend/Other/Other.csproj', 'healthPath': '/health/ready'}}))
        front, back = provision.discover(self.root, host.require)
        self.assertIsNone(front)
        self.assertEqual(back['assembly'], 'Other')
        self.assertEqual(back['healthMode'], 'http')

    def test_repo_manifest_does_not_accept_privileged_commands_or_paths(self):
        for manifest in [{'commands': [['/usr/bin/sh', '-c', 'bad']]}, {'front': {'directory': '../outside'}}, {'front': None, 'back': {'project': '/etc/passwd'}}]:
            self.write('deploy-console.json', json.dumps(manifest))
            with self.subTest(manifest=manifest), self.assertRaises(host.Rejected):
                provision.discover(self.root, host.require)

    def test_missing_lockfile_and_unknown_projects_have_explicit_errors(self):
        with self.assertRaisesRegex(host.Rejected, '未识别'):
            provision.discover(self.root, host.require)
        self.write('package.json', '{"scripts":{"build":"vite build"}}')
        with self.assertRaisesRegex(host.Rejected, 'package-lock'):
            provision.discover(self.root, host.require)

    def test_nested_project_descriptor_resolves_front_backend_and_build_working_directory(self):
        clone = self.root
        self.root = clone / 'FDE-Project'
        self.fixture()
        self.write('deploy-console.json', json.dumps({'front': {'directory': 'src/frontend', 'output': 'dist'}, 'back': {'project': 'src/backend/Sample.Api/Sample.Api.csproj'}}))
        self.root = clone
        front, back = provision.discover(clone, host.require)
        self.assertEqual(front['directory'], 'FDE-Project/src/frontend')
        self.assertEqual(back['build']['directory'], 'FDE-Project')
        self.assertEqual(back['build']['commands'][0][2], 'src/backend/Sample.Api/Sample.Api.csproj')

    def test_nested_frontend_is_found_next_to_the_only_backend_without_descriptor(self):
        clone = self.root
        self.root = clone / 'FDE-Project'
        self.fixture()
        self.root = clone
        front, back = provision.discover(clone, host.require)
        self.assertEqual(front['directory'], 'FDE-Project/src/frontend')
        self.assertEqual(back['assembly'], 'Sample.Api')

    def test_multiple_nested_descriptors_require_explicit_root_selection(self):
        self.write('one/deploy-console.json', '{}')
        self.write('two/deploy-console.json', '{}')
        with self.assertRaisesRegex(host.Rejected, '多个deploy-console'):
            provision.discover(self.root, host.require)

    def test_repo_symlink_cannot_escape_checkout(self):
        self.write('target.json', '{}')
        try:
            (self.root / 'link.json').symlink_to(self.root / 'target.json')
        except OSError:
            self.skipTest('Host does not allow symlink creation')
        with self.assertRaises(host.Rejected):
            provision.read_repo_file(self.root, 'link.json', host.require)

    def test_onboarding_example_matches_standalone_sample_project(self):
        self.write('src/frontend/package.json', '{"scripts":{"build":"vite build"}}')
        self.write('src/frontend/package-lock.json', '{}')
        self.write('src/backend/Project.Api/Project.Api.csproj', '<Project Sdk="Microsoft.NET.Sdk.Web"/>')
        sample = (ROOT / 'docs/deploy-console-onboarding-examples/deploy-console.fullstack.example.json').read_text(encoding='utf-8')
        self.write('deploy-console.json', sample)
        front, back = provision.discover(self.root, host.require)
        self.assertEqual(front['directory'], 'src/frontend')
        self.assertEqual(back['assembly'], 'Project.Api')
        self.assertEqual(back['healthPath'], '/health/ready')

    def test_port_selection_skips_reserved_and_listening_ports(self):
        occupied = {15000}
        with patch.object(provision, 'port_available', side_effect=lambda port, adapter: port != 15001) as probe:
            self.assertEqual(provision.choose_port(occupied, host), 15002)
            self.assertEqual(provision.choose_port(occupied, host), 15003)
        self.assertIn(15002, occupied)
        self.assertNotIn(15000, [call.args[0] for call in probe.call_args_list])

    def test_port_range_includes_both_boundaries_and_fails_without_overflow(self):
        with patch.object(provision, 'port_available', return_value=True) as probe:
            self.assertEqual(provision.choose_port(set(), host), 15000)
            occupied = set(range(15000, 25000))
            self.assertEqual(provision.choose_port(occupied, host), 25000)
            with self.assertRaisesRegex(host.Rejected, '15000–25000已用完'):
                provision.choose_port(occupied, host)
        self.assertEqual([call.args[0] for call in probe.call_args_list], [15000, 25000])

    def test_bound_ipv4_socket_is_occupied_even_before_listen(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', 0))
            self.assertFalse(provision.port_available(listener.getsockname()[1], host))

    @unittest.skipUnless(socket.has_ipv6, 'IPv6 is unavailable')
    def test_ipv6_only_listener_is_not_allocated(self):
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            try:
                listener.bind(('::1', 0))
            except OSError as error:
                self.skipTest('IPv6 loopback is unavailable: ' + str(error.errno))
            listener.listen(1)
            self.assertFalse(provision.port_available(listener.getsockname()[1], host))

    def test_probe_releases_its_socket_when_port_is_available(self):
        with patch.object(provision.socket, 'has_ipv6', False):
            # Port zero asks the OS to choose a free port for this probe only.
            self.assertTrue(provision.port_available(0, host))
        with patch.object(provision.socket, 'socket') as factory:
            probe = factory.return_value.__enter__.return_value
            self.assertTrue(provision.port_available(15000, host))
            self.assertTrue(factory.return_value.__exit__.called)
            self.assertTrue(probe.bind.called)

    def test_probe_failure_is_not_mistaken_for_free_port(self):
        with patch.object(provision.socket, 'socket', side_effect=OSError(errno.EMFILE, 'test')):
            with self.assertRaisesRegex(host.Rejected, '无法检测'):
                provision.port_available(15000, host)

    def test_stopped_services_keep_configured_and_changed_ports_reserved(self):
        targets = self.root / 'targets'
        targets.mkdir()
        (targets / 'project.json').write_text('{}')
        state = self.root / 'state/project'
        state.mkdir(parents=True)
        (state / 'state.json').write_text(json.dumps({'front': {'port': 15004}, 'back': {'port': 15005}}))
        (state / 'stop-front').touch()
        target = {'front': {'port': 15000}, 'back': {'port': 15001}, 'database': {'port': 15002}}
        with patch.object(host, 'TARGETS', targets), patch.object(host, 'STATE', state.parent), patch.object(host, 'load_target', return_value=target), patch.object(host, 'trusted', side_effect=Path):
            occupied = provision.reserved_ports(host)
        self.assertEqual(occupied, {15000, 15001, 15002, 15004, 15005})
        with patch.object(provision, 'port_available', return_value=True):
            self.assertEqual(provision.choose_port(occupied, host), 15003)

    def test_baota_reload_uses_own_nginx_binary(self):
        with patch.object(host, 'run') as execute:
            host.nginx_reload({'binary': Path('/www/server/nginx/sbin/nginx'), 'baota': True})
        self.assertEqual(execute.call_args.args[0], [str(Path('/www/server/nginx/sbin/nginx')), '-s', 'reload'])

    def test_prepare_clones_and_reconciles_missing_front_without_replacing_backend(self):
        source = self.root / 'fixture'
        self.root = source
        self.fixture()
        self.root = source.parent
        dirs = {name: self.root / name for name in ['targets', 'state', 'units', 'runtime', 'data', 'nginx']}
        for path in dirs.values():
            path.mkdir()
        policy = self.root / 'policy.json'
        policy.write_text('{"enabled":true,"allowedGitHosts":["github.com"]}')
        request = {'slug': 'sample', 'operationId': '11111111-1111-1111-1111-111111111111', 'repository': 'https://github.com/team/sample.git', 'branch': 'main', 'action': 'prepare',
                   'setup': {'frontPath': str(self.root / 'front/current'), 'backPath': str(self.root / 'back/current'), 'repositoryPath': str(self.root / 'repo')}}
        user = types.SimpleNamespace(pw_uid=self.root.stat().st_uid, pw_gid=self.root.stat().st_gid)
        def clone(control, request, side, arguments, cwd, timeout):
            shutil.copytree(source, Path(arguments[-1]))
        real_is_file = Path.is_file
        def is_file(path):
            return True if path.as_posix() in ('/usr/bin/npm', '/usr/bin/dotnet') else real_is_file(path)
        def load(slug):
            return json.loads((dirs['targets'] / (slug + '.json')).read_text())
        with patch.object(host, 'TARGETS', dirs['targets']), patch.object(host, 'STATE', dirs['state']), patch.object(provision, 'UNITS', dirs['units']), patch.object(provision, 'RUNTIME', dirs['runtime']), patch.object(provision, 'APP_DATA', dirs['data']), patch.object(provision, 'POLICY', policy), patch.object(host, 'absolute', side_effect=Path), patch.object(host, 'trusted', side_effect=lambda p, *args: Path(p)), patch.object(provision, 'account', return_value=user), patch.object(provision.os, 'chown', create=True), patch.object(host, 'run') as execute, patch.object(host.Host, 'git', clone), patch.object(provision, 'port_available', return_value=True), patch.object(host, 'nginx_runtime', return_value={'binary': self.root / 'nginx-bin', 'directory': dirs['nginx'], 'baota': True}), patch.object(Path, 'is_file', is_file), patch.object(host, 'validate_target'), patch.object(host, 'load_target', side_effect=load):
            result = provision.prepare(request, host)
            self.assertTrue(result['ok'])
            target = load('sample')
            self.assertEqual(target['front']['port'], 15000)
            self.assertEqual(target['back']['port'], 15001)
            self.assertEqual(target['database']['kind'], 'none')
            unit = (dirs['units'] / target['back']['unit']).read_text()
            self.assertIn('User=deploy-app-', unit)
            self.assertNotIn('User=root', unit)
            self.assertNotIn('start', str(execute.call_args_list))
            saved_back = target['back'].copy()
            target.pop('front')
            (dirs['targets'] / 'sample.json').write_text(json.dumps(target))
            with patch.object(provision.pwd, 'getpwnam', return_value=user):
                retry = {**request, 'operationId': '22222222-2222-2222-2222-222222222222'}
                self.assertTrue(provision.prepare(retry, host)['ok'])
            self.assertEqual(load('sample')['back'], saved_back)
            self.assertEqual(load('sample')['front']['kind'], 'nginx')
            # A stopped backend must not prevent the front-only publish path or create a DB.
            (source / 'deploy-console.json').write_text(json.dumps({'database': {
                'kind': 'mysql', 'connectionStringName': 'Sample',
                'planArguments': ['--deployment-database-plan'], 'applyArguments': ['--deployment-database-apply']}}))
            (dirs['state'] / 'sample' / 'stop-back').touch()
            with patch.object(provision.pwd, 'getpwnam', return_value=user):
                self.assertTrue(provision.prepare({**request, 'operationId': '33333333-3333-3333-3333-333333333333'}, host)['ok'])
            self.assertEqual(load('sample')['database']['kind'], 'none')
            changed = {**request, 'setup': {**request['setup'], 'frontPath': '/opt/other/current'}}
            with self.assertRaises(host.Rejected):
                provision.prepare(changed, host)


if __name__ == '__main__':
    unittest.main()

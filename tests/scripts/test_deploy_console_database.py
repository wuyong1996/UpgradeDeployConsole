"""Database orchestration tests; all MySQL and service processes are replaced."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from test_deploy_console import host, ROOT

spec = importlib.util.spec_from_file_location('database_tests_module', ROOT / 'deploy/console/database.py')
database = importlib.util.module_from_spec(spec)
spec.loader.exec_module(database)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'project'
        self.directory.mkdir()
        self.runtime = self.root / 'runtime' / 'project'
        self.runtime.mkdir(parents=True)
        (self.runtime / 'runtime.env').write_text('KEEP=value\n')
        self.control = types.SimpleNamespace(slug='project', directory=self.directory, stopped=lambda side: False, target={})
        self.definition = {'kind': 'mysql', 'connectionStringName': 'AppDb', 'assembly': 'Project.Api',
                           'planArguments': ['--deployment-database-plan'], 'applyArguments': ['--deployment-database-apply']}
        self.config = {'username': 'root', 'password': 'PRIVATE_ROOT_PASSWORD', 'port': 3306}
        self.uuid = '12345678-1234-1234-1234-123456789012'
        for target, name, replacement in [(database, 'RUNTIME', self.root / 'runtime'), (host, 'trusted', lambda path, *args: Path(path)),
                                          (database, 'configuration', lambda h: self.config), (database, 'server', lambda c, h: self.uuid)]:
            item = patch.object(target, name, replacement)
            item.start()
            self.addCleanup(item.stop)
        self.receipt_path = self.directory / 'database.json'

    def create(self):
        with patch.object(database, 'mysql', side_effect=['0', '']) as query:
            target = database.provision(self.control, self.definition, host)
        self.control.target['database'] = target
        return json.loads(self.receipt_path.read_text()), query

    def plan(self, receipt, pending=True):
        return {'status': 'planned', 'applied': [], 'pending': ['001'] if pending else [],
                'fingerprints': {'001': 'a' * 64}, 'planToken': 'b' * 64, 'conflicts': []}

    def client_directories(self):
        directories = (self.root / 'system-bin', self.root / 'baota-bin')
        for directory in directories:
            directory.mkdir()
            for name in ('mysql', 'mysqldump'):
                (directory / name).write_text('test fixture; never execute')
        return directories

    def test_client_prefers_protected_system_binary_over_baota(self):
        directories = self.client_directories()
        def trust(path):
            if path.parent == directories[1]:
                raise host.Rejected('service-owned path')
            return path
        with patch.object(database, 'CLIENT_DIRECTORIES', directories), patch.object(host, 'trusted', side_effect=trust) as checked, patch.object(database.os, 'access', return_value=True):
            for name in ('mysql', 'mysqldump'):
                self.assertEqual(database.binary(name, host), str(directories[0] / name))
            self.assertEqual([call.args[0].parent for call in checked.call_args_list], [directories[0], directories[0]])

    def test_client_tries_protected_baota_after_rejected_system_entry(self):
        directories = self.client_directories()
        def trust(path):
            if path.parent == directories[0]:
                raise host.Rejected('symlink is not trusted')
            return path
        with patch.object(database, 'CLIENT_DIRECTORIES', directories), patch.object(host, 'trusted', side_effect=trust), patch.object(database.os, 'access', return_value=True):
            self.assertEqual(database.binary('mysql', host), str(directories[1] / 'mysql'))

    def test_client_rejects_all_unprotected_candidates_without_execution(self):
        directories = self.client_directories()
        with patch.object(database, 'CLIENT_DIRECTORIES', directories), patch.object(host, 'trusted', side_effect=host.Rejected('service-owned path')), patch.object(database.subprocess, 'run') as execute:
            with self.assertRaisesRegex(host.Rejected, '客户端或父目录'):
                database.binary('mysql', host)
            execute.assert_not_called()

    def test_client_requires_execute_access_and_tries_next_candidate(self):
        directories = self.client_directories()
        with patch.object(database, 'CLIENT_DIRECTORIES', directories), patch.object(database.os, 'access', side_effect=lambda path, mode: path.parent == directories[1]):
            self.assertEqual(database.binary('mysqldump', host), str(directories[1] / 'mysqldump'))
        with patch.object(database, 'CLIENT_DIRECTORIES', directories), patch.object(database.os, 'access', return_value=False), self.assertRaisesRegex(host.Rejected, '客户端或父目录'):
            database.binary('mysqldump', host)

    def test_client_missing_and_unrecognized_names_have_distinct_errors(self):
        with patch.object(database, 'CLIENT_DIRECTORIES', (self.root / 'missing',)):
            with self.assertRaisesRegex(host.Rejected, '未找到MySQL客户端'):
                database.binary('mysql', host)
            with self.assertRaisesRegex(host.Rejected, '客户端类型无效'):
                database.binary('../sh', host)

    def test_repository_profile_rejects_root_commands_and_requires_backend(self):
        with self.assertRaises(host.Rejected):
            database.profile({**self.definition, 'commands': [['/bin/sh', '-c', 'anything']]}, {'assembly': 'Project.Api'}, host)
        with self.assertRaises(host.Rejected):
            database.profile(self.definition, None, host)
        with self.assertRaises(host.Rejected):
            database.profile({**self.definition, 'connectionStringName': 'A\nOTHER=value'}, {'assembly': 'Project.Api'}, host)
        self.assertEqual(database.profile(self.definition, {'assembly': 'Project.Api'}, host), self.definition)

    def test_settings_save_only_after_verification_and_never_returns_password(self):
        admin_path = self.root / 'mysql.json'
        with patch.object(database, 'ADMIN', admin_path), patch.object(host, 'STATE', self.root / 'empty-state'):
            result = database.settings({'action': 'mysql-configure', 'mySql': self.config}, host)
            self.assertNotIn(self.config['password'], json.dumps(result))
            before = admin_path.read_text()
            with patch.object(database, 'server', side_effect=host.Rejected('connection failed')), self.assertRaises(host.Rejected):
                database.settings({'action': 'mysql-configure', 'mySql': {**self.config, 'password': 'NEW_SECRET'}}, host)
            self.assertEqual(admin_path.read_text(), before)

    def test_names_are_bounded_and_do_not_introduce_grant_wildcards(self):
        first = database.names('project-a')
        self.assertNotEqual(first, database.names('projecta'))
        for name in database.names('p' * 64):
            self.assertRegex(name, '^[a-z0-9]+$')
            self.assertLessEqual(len(name), 64)

    def test_creation_uses_separate_scoped_users_and_keeps_runtime_overrides(self):
        receipt, query = self.create()
        ddl = query.call_args_list[1].args[1]
        self.assertIn('CREATE DATABASE', ddl)
        self.assertIn('GRANT SELECT,INSERT,UPDATE,DELETE', ddl)
        self.assertNotIn('ON *.*', ddl)
        self.assertNotIn(self.config['password'], ddl)
        self.assertNotEqual(receipt['migrationPassword'], receipt['runtimePassword'])
        environment = (self.runtime / 'runtime.env').read_text()
        self.assertIn('KEEP=value', environment)
        self.assertIn(receipt['runtimePassword'], environment)
        self.assertNotIn(receipt['migrationPassword'], environment)
        self.assertEqual(self.control.target['database']['kind'], 'mysql')

    def test_existing_name_is_a_conflict_and_no_create_or_runtime_change_occurs(self):
        with patch.object(database, 'mysql', return_value='1') as query, self.assertRaisesRegex(host.Rejected, '归属冲突'):
            database.provision(self.control, self.definition, host)
        self.assertEqual(query.call_count, 1)
        self.assertFalse(self.receipt_path.exists())
        self.assertEqual((self.runtime / 'runtime.env').read_text(), 'KEEP=value\n')

    def test_existing_runtime_connection_is_not_silently_replaced(self):
        (self.runtime / 'runtime.env').write_text('ConnectionStrings__AppDb="existing"\n')
        with patch.object(database, 'mysql') as query, self.assertRaisesRegex(host.Rejected, '连接配置'):
            database.provision(self.control, self.definition, host)
        query.assert_not_called()

    def test_successful_retry_keeps_database_accounts_and_passwords(self):
        before, _ = self.create()
        with patch.object(database, 'mysql', return_value='1') as query:
            database.provision(self.control, self.definition, host)
        self.assertEqual(query.call_count, 1)
        self.assertNotIn('CREATE', query.call_args.args[1])
        self.assertEqual(before, json.loads(self.receipt_path.read_text()))

    def test_interrupted_creation_stays_explicit_and_is_not_retried(self):
        with patch.object(database, 'mysql', side_effect=['0', host.Rejected('uncertain')]), self.assertRaises(host.Rejected):
            database.provision(self.control, self.definition, host)
        self.assertEqual(json.loads(self.receipt_path.read_text())['phase'], 'creating')
        with patch.object(database, 'mysql') as query, self.assertRaisesRegex(host.Rejected, '待核对'):
            database.provision(self.control, self.definition, host)
        query.assert_not_called()

    def test_upgrade_backs_up_before_migration_and_restores_mysql_guard(self):
        receipt, _ = self.create()
        receipt['fresh'] = False
        self.receipt_path.write_text(json.dumps(receipt))
        plan = self.plan(receipt)
        after = {**plan, 'status': 'applied', 'pending': []}
        events = []
        def migrate(*args, **kwargs):
            events.append('plan' if len(events) == 0 else 'apply')
            return plan if len(events) == 1 else after
        with patch.object(database, 'migration_command', side_effect=migrate), patch.object(database, 'backup', side_effect=lambda *args: events.append('backup') or '/private/backup.sql.gz'), patch.object(database, 'mysql', side_effect=['1\t0', '', '']) as query:
            database.migrate(self.control, self.root / 'release', 'operation', host)
        self.assertEqual(events, ['plan', 'backup', 'apply'])
        self.assertEqual(query.call_args_list[-1].args[1], 'SET GLOBAL log_bin_trust_function_creators=0;')
        receipt = json.loads(self.receipt_path.read_text())
        self.assertEqual(receipt['phase'], 'ready')
        self.assertFalse(receipt['fresh'])
        self.assertNotIn('restoreTrust', receipt)

    def test_failed_migration_keeps_recovery_phase_and_restores_guard(self):
        receipt, _ = self.create()
        plan = self.plan(receipt)
        with patch.object(database, 'migration_command', side_effect=[plan, host.Rejected('failed')]), patch.object(database, 'mysql', side_effect=['1\t0', '', '']) as query, self.assertRaises(host.Rejected):
            database.migrate(self.control, self.root / 'release', 'operation', host)
        self.assertEqual(query.call_args_list[-1].args[1], 'SET GLOBAL log_bin_trust_function_creators=0;')
        self.assertEqual(json.loads(self.receipt_path.read_text())['phase'], 'migrating')

    def test_changed_migration_or_conflicting_plan_never_applies(self):
        receipt, _ = self.create()
        receipt['fingerprints'] = {'001': 'c' * 64}
        self.receipt_path.write_text(json.dumps(receipt))
        with patch.object(database, 'migration_command', return_value=self.plan(receipt)) as command, patch.object(database, 'backup') as backup, self.assertRaisesRegex(host.Rejected, '已应用迁移'):
            database.migrate(self.control, self.root / 'release', 'operation', host)
        self.assertEqual(command.call_count, 1)
        backup.assert_not_called()

    def test_stopped_backend_does_not_migrate(self):
        self.create()
        self.control.stopped = lambda side: True
        with patch.object(database, 'migration_command') as command, self.assertRaisesRegex(host.Rejected, '已停止'):
            database.migrate(self.control, self.root / 'release', 'operation', host)
        command.assert_not_called()

    def test_interrupted_guard_is_restored_before_provision_reports_recovery_conflict(self):
        receipt, _ = self.create()
        receipt.update(phase='migrating', restoreTrust=0)
        self.receipt_path.write_text(json.dumps(receipt))
        with patch.object(database, 'mysql', return_value='') as query, self.assertRaisesRegex(host.Rejected, '待核对'):
            database.provision(self.control, self.definition, host)
        self.assertEqual(query.call_args.args[1], 'SET GLOBAL log_bin_trust_function_creators=0;')
        self.assertNotIn('restoreTrust', json.loads(self.receipt_path.read_text()))

    def test_project_shutdown_only_locks_project_accounts(self):
        receipt, _ = self.create()
        with patch.object(database, 'mysql', return_value='') as query:
            database.access(self.control, 'stop', host)
        sql = query.call_args.args[1]
        self.assertIn(receipt['runtimeUser'], sql)
        self.assertIn(receipt['migrationUser'], sql)
        self.assertNotIn('SHUTDOWN', sql)
        self.assertNotIn('DROP', sql)
        self.assertTrue(json.loads(self.receipt_path.read_text())['accessPaused'])


if __name__ == '__main__':
    unittest.main()

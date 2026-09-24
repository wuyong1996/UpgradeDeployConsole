from contextlib import ExitStack
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from test_deploy_console import host, ROOT

spec = importlib.util.spec_from_file_location('console_deletion', ROOT / 'deploy/console/deletion.py')
deletion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deletion)
spec = importlib.util.spec_from_file_location('deletion_database', ROOT / 'deploy/console/database.py')
database = importlib.util.module_from_spec(spec)
spec.loader.exec_module(database)


class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module, name, path in [(host, 'STATE', self.root / 'state'), (host, 'TARGETS', self.root / 'targets'),
                                   (deletion, 'RECORDS', self.root / 'records'), (deletion, 'ARCHIVES', self.root / 'archives'),
                                   (deletion, 'UNITS', self.root / 'units'), (deletion, 'RUNTIME', self.root / 'runtime'), (deletion, 'APP_DATA', self.root / 'data')]:
            path.mkdir()
            self.stack.enter_context(patch.object(module, name, path))
        self.stack.enter_context(patch.object(host, 'trusted', side_effect=lambda path, *args: Path(path)))
        self.stack.enter_context(patch.object(host, 'absolute', side_effect=lambda path: Path(path)))
        self.stack.enter_context(patch.object(deletion.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=1001)))
        self.stack.enter_context(patch.object(deletion, 'private_directory', side_effect=lambda path, _: path.mkdir(mode=0o700, exist_ok=True)))
        original_validate = deletion.validate_path
        def validate(item, adapter):
            # Windows has no POSIX uid; only relax the fixture uid, never its boundary checks.
            adjusted = {**item, 'owner': 0} if os.name == 'nt' else item
            original_validate(adjusted, adapter)
        self.stack.enter_context(patch.object(deletion, 'validate_path', side_effect=validate))
        self.stack.enter_context(patch.object(deletion.shutil.rmtree, 'avoids_symlink_attacks', True))
        if os.name == 'nt':
            self.stack.enter_context(patch.object(deletion.tarfile, 'pwd', None))
        self.nginx = self.root / 'nginx'; self.nginx.mkdir()
        self.runtime = {'binary': self.root / 'nginx-bin', 'directory': self.nginx, 'baota': False}
        self.stack.enter_context(patch.object(host, 'nginx_runtime', return_value=self.runtime))
        self.reload = self.stack.enter_context(patch.object(host, 'nginx_reload'))
        self.run = self.stack.enter_context(patch.object(host, 'run', side_effect=self.command))
        self.target = json.loads((ROOT / 'deploy/console/project.example.json').read_text(encoding='utf-8'))
        self.target.update(slug='project', autoManaged=True, buildUser='deploy-build-p' + hashlib.sha256(b'project').hexdigest()[:16], repositoryPath=str(self.root / 'project/repository'), database={'kind': 'none'})
        for side in host.SIDES:
            self.target[side].update(autoManaged=True, current=str(self.root / side / 'project/current'))
            releases = Path(self.target[side]['current']).parent / 'releases'; releases.mkdir(parents=True)
            (releases / 'content.txt').write_text(side)
        self.unit = 'deploy-project-project.service'
        self.target['back']['unit'] = self.unit
        (deletion.UNITS / self.unit).write_text('# Managed by deploy-console: project\n')
        self.repo = Path(self.target['repositoryPath']); self.repo.mkdir(parents=True)
        (self.repo / 'source.txt').write_text('SOURCE')
        for path in (deletion.RUNTIME / 'project', deletion.APP_DATA / 'project'):
            path.mkdir(); (path / 'saved.txt').write_text('PROJECT DATA')
        self.manifest = host.TARGETS / 'project.json'
        self.save_target()
        self.control = host.Host(self.target)
        for side in host.SIDES:
            (self.control.directory / ('stop-' + side)).touch()
        self.control.state = {'back': {'port': 17001}}
        self.control.save()
        self.site = self.nginx / 'deploy-console-project.conf'
        self.site.write_text(self.control.nginx_content(None), encoding='utf-8', newline='\n')
        self.stack.enter_context(patch.object(host, 'extension', side_effect=lambda name: database if name == 'database' else deletion if name == 'deletion' else types.SimpleNamespace(configuration=lambda _: None)))
        self.sql = []
        self.deleted = False
        self.sql_mock = self.stack.enter_context(patch.object(database, 'mysql', side_effect=self.mysql))
        self.stack.enter_context(patch.object(database, 'configuration', return_value={'port': 3306}))
        self.stack.enter_context(patch.object(database, 'server', return_value='server-id'))
        self.backup = self.stack.enter_context(patch.object(database, 'backup', side_effect=self.dump))

    def command(self, argv, *args, **kwargs):
        if argv[1] == 'show':
            return types.SimpleNamespace(stdout='ActiveState=inactive\nMainPID=0\nFragmentPath=' + str(deletion.UNITS / argv[2]), returncode=0)
        return types.SimpleNamespace(stdout='', returncode=0)

    def mysql(self, config, sql, adapter):
        self.sql.append(sql)
        if sql.startswith('DROP DATABASE'):
            self.deleted = True; return ''
        if 'SHOW GRANTS' in sql:
            return 'GRANT USAGE ON *.* TO `project`@`localhost`\nGRANT SELECT ON `' + database.names('project')[0] + '`.* TO `project`@`localhost`'
        if 'account_locked' in sql:
            return '4'
        if sql.startswith('SELECT COUNT(*) FROM mysql.user'):
            return '4'
        if 'SELECT Process_priv' in sql:
            return 'Y'
        if 'PROCESSLIST' in sql:
            return '0'
        return '0' if self.deleted else '1'

    def dump(self, receipt, config, operation, control, adapter):
        path = control.directory / (operation + '.sql.gz')
        with gzip.open(path, 'wb') as output:
            output.write(b'-- isolated test backup\nCREATE TABLE test (id int);')
        return str(path)

    def save_target(self):
        self.manifest.write_text(json.dumps(self.target), encoding='utf-8')

    def with_database(self):
        schema, user, migration = database.names('project')
        self.target['database'] = {'kind': 'mysql', 'identity': schema, 'port': 3306, 'host': '127.0.0.1', 'managed': True, 'autoProvisioned': True, 'deployment': {}}
        self.save_target()
        self.receipt = {'slug': 'project', 'name': schema, 'runtimeUser': user, 'migrationUser': migration, 'runtimePassword': 'PRIVATE', 'migrationPassword': 'PRIVATE',
                        'serverUuid': 'server-id', 'port': 3306, 'phase': 'ready', 'accessPaused': True, 'fingerprints': {}}
        (self.control.directory / 'database.json').write_text(json.dumps(self.receipt))

    def preview(self):
        return deletion.handle({'slug': 'project', 'action': 'delete-preview'}, host)['deletion']

    def execute(self, fingerprint=None, **options):
        request = {'slug': 'project', 'action': 'delete', 'deletionFingerprint': fingerprint or self.preview()['fingerprint'], **options}
        return deletion.handle(request, host)

    def test_no_backup_deletes_and_replays_without_creating_archive(self):
        self.with_database(); plan = self.preview()
        self.assertTrue(plan['backupBeforeDelete']); self.assertFalse(plan['backupModeLocked'])
        old = deletion.ARCHIVES / 'historical.txt'; old.write_text('KEEP')
        with patch.object(deletion, 'backup_files') as files:
            result = self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)
            self.assertTrue(result['ok']); self.assertFalse(result['backupBeforeDelete'])
            self.assertIsNone(result['backupDirectory']); self.assertIn('未备份', result['message'])
            self.assertTrue(self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)['ok'])
            files.assert_not_called(); self.backup.assert_not_called()
        self.assertEqual(list(deletion.ARCHIVES.iterdir()), [old]); self.assertEqual(old.read_text(), 'KEEP')
        self.assertFalse(self.repo.exists()); self.assertFalse(self.manifest.exists()); self.assertTrue(self.deleted)
        self.assertEqual(sum(x.startswith('DROP DATABASE') for x in self.sql), 1)
        self.assertNotIn('PRIVATE', json.dumps(deletion.record_for('project', host)))
        with self.assertRaisesRegex(host.Rejected, '原备份选项'): self.execute(plan['fingerprint'])

    def test_no_backup_also_supports_projects_without_database(self):
        result = self.execute(backupBeforeDelete=False, confirmWithoutBackup=True)
        self.assertTrue(result['ok']); self.assertFalse(self.repo.exists())
        self.assertEqual(list(deletion.ARCHIVES.iterdir()), []); self.backup.assert_not_called()

    def test_no_backup_requires_explicit_boolean_consent_before_any_write(self):
        plan = self.preview()
        for options in ({'backupBeforeDelete': False}, {'backupBeforeDelete': 'false', 'confirmWithoutBackup': True},
                        {'backupBeforeDelete': False, 'confirmWithoutBackup': 'true'}, {'backupBeforeDelete': None}):
            with self.subTest(options=options):
                self.assertFalse(self.execute(plan['fingerprint'], **options)['ok'])
                self.assertIsNone(deletion.record_for('project', host)); self.assertTrue(self.repo.exists())
        self.backup.assert_not_called(); self.assertEqual(list(deletion.ARCHIVES.iterdir()), [])

    def test_failed_backup_can_switch_to_no_backup_before_deletion(self):
        self.with_database(); self.backup.side_effect = host.Rejected('backup failed')
        self.assertFalse(self.execute()['ok'])
        plan = self.preview(); self.assertFalse(plan['backupModeLocked'])
        self.assertTrue(self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)['ok'])
        self.backup.assert_called_once(); self.assertTrue(self.deleted)
        record = deletion.record_for('project', host)
        self.assertFalse(record['backupBeforeDelete']); self.assertIsNone(record['plan']['backupDirectory'])
        self.assertNotIn('filesBackup', record); self.assertNotIn('databaseBackup', record)

    def test_no_backup_cleanup_retry_keeps_choice_and_ownership_metadata_without_secrets(self):
        self.with_database(); plan = self.preview(); self.reload.side_effect = host.Rejected('reload failed')
        self.assertFalse(self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)['ok'])
        record = deletion.record_for('project', host)
        self.assertEqual(record['phase'], 'clearing'); self.assertNotIn('PRIVATE', json.dumps(record))
        self.assertNotIn('runtimePassword', record['database'])
        retry = self.preview(); self.assertFalse(retry['backupBeforeDelete']); self.assertTrue(retry['backupModeLocked'])
        self.assertFalse(self.execute(plan['fingerprint'])['ok']); self.assertTrue(self.repo.exists())
        self.reload.side_effect = None
        self.assertTrue(self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)['ok'])
        self.assertEqual(sum(x.startswith('DROP DATABASE') for x in self.sql), 1); self.backup.assert_not_called()
        self.assertEqual(list(deletion.ARCHIVES.iterdir()), [])

    def test_backup_cleanup_retry_cannot_discard_backup_choice(self):
        self.with_database(); plan = self.preview(); self.reload.side_effect = host.Rejected('reload failed')
        self.assertFalse(self.execute(plan['fingerprint'])['ok'])
        self.assertTrue(self.preview()['backupModeLocked'])
        result = self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)
        self.assertFalse(result['ok']); self.assertIn('原备份选项', result['message']); self.assertTrue(self.repo.exists())

    def test_no_backup_does_not_bypass_running_or_shared_resource_guards(self):
        plan = self.preview()
        (self.control.directory / 'stop-back').unlink()
        self.assertFalse(self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)['ok'])
        (self.control.directory / 'stop-back').touch()
        self.target['autoManaged'] = False; self.save_target()
        self.assertFalse(self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)['ok'])
        self.assertTrue(self.repo.exists()); self.assertIsNone(deletion.record_for('project', host))

    def test_uncertain_no_backup_database_drop_cannot_replay_or_claim_recovery_backup(self):
        self.with_database(); plan = self.preview()
        def fail(config, sql, adapter):
            if sql.startswith('DROP DATABASE'):
                self.sql.append(sql); raise host.Rejected('connection interrupted')
            return self.mysql(config, sql, adapter)
        self.sql_mock.side_effect = fail
        self.assertFalse(self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)['ok'])
        result = self.execute(plan['fingerprint'], backupBeforeDelete=False, confirmWithoutBackup=True)
        self.assertFalse(result['ok']); self.assertIn('需人工核对', result['message']); self.assertNotIn('已保留备份', result['message'])
        self.assertIsNone(result['backupDirectory']); self.backup.assert_not_called()
        self.assertEqual(sum(x.startswith('DROP DATABASE') for x in self.sql), 1); self.assertTrue(self.repo.exists())

    def legacy_target(self, missing_front=True):
        self.target.pop('autoManaged')
        if missing_front:
            self.target['front'].pop('autoManaged')
        user = 'deploy-app-' + hashlib.sha256(b'project').hexdigest()[:16]
        (deletion.UNITS / self.unit).write_text('\n'.join([
            '# Managed by deploy-console: project', '[Service]', 'User=' + user, 'Group=' + user,
            'WorkingDirectory=' + self.target['back']['current'],
            'EnvironmentFile=' + str(deletion.RUNTIME / 'project/runtime.env'),
            'ReadWritePaths=' + str(deletion.APP_DATA / 'project'), '']), encoding='utf-8', newline='\n')
        self.save_target()

    def test_legacy_target_without_project_or_front_flag_deletes_after_full_checks(self):
        self.with_database(); self.legacy_target()
        before = self.manifest.read_bytes()
        plan = self.preview()
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertTrue(self.execute(plan['fingerprint'])['ok'])
        self.assertFalse(self.repo.exists())
        self.assertTrue(self.deleted)

    def test_legacy_target_with_later_front_binding_previews_without_rewriting_flags(self):
        self.legacy_target(missing_front=False)
        before = self.manifest.read_bytes()
        self.assertTrue(self.preview()['resources'])
        self.assertEqual(before, self.manifest.read_bytes())

    def test_legacy_target_does_not_override_explicit_manual_flags(self):
        self.legacy_target()
        self.target['front']['autoManaged'] = False; self.save_target()
        with self.assertRaises(host.Rejected): self.preview()
        self.target['front']['autoManaged'] = True
        self.target['autoManaged'] = False; self.save_target()
        with self.assertRaises(host.Rejected): self.preview()
        self.backup.assert_not_called()

    def test_legacy_target_without_generated_service_evidence_is_refused(self):
        self.legacy_target()
        (deletion.UNITS / self.unit).write_text('# Managed by deploy-console: project\n[Service]\nUser=shared\n')
        with self.assertRaisesRegex(host.Rejected, '自动创建证据'): self.preview()
        (deletion.UNITS / self.unit).unlink()
        with self.assertRaisesRegex(host.Rejected, '服务文件缺失'): self.preview()
        self.assertTrue(self.repo.exists())

    def test_legacy_target_still_rejects_nginx_edits_and_database_connections(self):
        self.with_database(); self.legacy_target()
        original = self.site.read_text(encoding='utf-8')
        self.site.write_text(original + '\n# unrelated change\n', encoding='utf-8')
        with self.assertRaisesRegex(host.Rejected, 'Nginx'): self.preview()
        self.site.write_text(original, encoding='utf-8', newline='\n')
        self.sql_mock.side_effect = lambda config, sql, adapter: '1' if 'PROCESSLIST' in sql else self.mysql(config, sql, adapter)
        with self.assertRaisesRegex(host.Rejected, '活动连接'): self.preview()
        self.assertFalse(self.deleted)

    def test_legacy_cleanup_can_resume_after_generated_unit_removed(self):
        self.with_database(); self.legacy_target(); plan = self.preview()
        self.reload.side_effect = host.Rejected('reload failed')
        self.assertFalse(self.execute(plan['fingerprint'])['ok'])
        self.assertFalse((deletion.UNITS / self.unit).exists())
        self.reload.side_effect = None
        self.assertTrue(self.preview()['resume'])
        self.assertTrue(self.execute(plan['fingerprint'])['ok'])
        self.assertEqual(sum(x.startswith('DROP DATABASE') for x in self.sql), 1)

    def test_missing_all_auto_markers_is_not_assumed_legacy(self):
        self.legacy_target(); self.target['back'].pop('autoManaged'); self.save_target()
        with self.assertRaisesRegex(host.Rejected, '管理员核对'): self.preview()
        self.backup.assert_not_called()

    def test_delete_backups_and_removes_only_this_project_and_replays_safely(self):
        self.with_database()
        other = self.nginx / 'other.conf'; other.write_text('unrelated')
        plan = self.preview()
        self.assertNotIn('PRIVATE', json.dumps(plan))
        self.assertIn(self.target['database']['identity'], json.dumps(plan))
        result = self.execute(plan['fingerprint'])
        self.assertTrue(result['ok'])
        self.assertFalse(self.repo.exists()); self.assertFalse(self.manifest.exists()); self.assertFalse(self.control.directory.exists())
        self.assertEqual(other.read_text(), 'unrelated')
        self.assertTrue((Path(result['backupDirectory']) / 'manifest.json').exists())
        self.assertTrue(self.execute(plan['fingerprint'])['ok'])
        self.assertEqual(sum(x.startswith('DROP DATABASE') for x in self.sql), 1)
        self.assertFalse(any('stop' in call.args[0] and 'mysql' in ' '.join(call.args[0]) for call in self.run.call_args_list))
        self.assertNotIn('PRIVATE', json.dumps(deletion.record_for('project', host)))

    def test_wrong_fingerprint_has_no_backup_or_deletion(self):
        result = self.execute('0' * 64)
        self.assertFalse(result['ok']); self.assertTrue(self.repo.exists()); self.backup.assert_not_called()
        self.assertIsNone(deletion.record_for('project', host))

    def test_missing_stop_marker_refuses_preview(self):
        (self.control.directory / 'stop-back').unlink()
        with self.assertRaises(host.Rejected): self.preview()

    def test_running_process_refuses_preview(self):
        self.run.side_effect = lambda *args, **kwargs: types.SimpleNamespace(stdout='ActiveState=active\nMainPID=100')
        with self.assertRaises(host.Rejected): self.preview()

    def test_manual_target_and_extra_dropin_are_conflicts(self):
        self.target['autoManaged'] = False; self.save_target()
        with self.assertRaises(host.Rejected): self.preview()
        self.target['autoManaged'] = True; self.save_target()
        folder = deletion.UNITS / (self.unit + '.d'); folder.mkdir(); (folder / 'custom.conf').write_text('user owned')
        with self.assertRaises(host.Rejected): self.preview()

    def test_other_project_directory_overlap_is_refused(self):
        other = {**self.target, 'slug': 'other', 'buildUser': 'deploy-build-other'}
        (host.TARGETS / 'other.json').write_text(json.dumps(other))
        with self.assertRaises(host.Rejected): self.preview()

    def test_backup_failure_preserves_database_and_all_resources(self):
        self.with_database(); self.backup.side_effect = host.Rejected('backup failed')
        result = self.execute()
        self.assertFalse(result['ok']); self.assertTrue(self.repo.exists()); self.assertFalse(self.deleted)
        self.assertEqual(deletion.record_for('project', host)['phase'], 'preparing')

    def test_cleanup_failure_retries_without_dropping_database_twice(self):
        self.with_database(); plan = self.preview()
        self.reload.side_effect = host.Rejected('reload failed')
        self.assertFalse(self.execute(plan['fingerprint'])['ok'])
        self.assertTrue(self.repo.exists()); self.assertTrue(self.deleted)
        self.reload.side_effect = None
        self.assertTrue(self.preview()['resume'])
        self.assertTrue(self.execute(plan['fingerprint'])['ok'])
        self.assertEqual(sum(x.startswith('DROP DATABASE') for x in self.sql), 1)
        self.backup.assert_called_once()

    def test_uncertain_database_drop_is_never_replayed(self):
        self.with_database(); plan = self.preview()
        def fail(config, sql, adapter):
            if sql.startswith('DROP DATABASE'):
                self.sql.append(sql); raise host.Rejected('connection interrupted')
            return self.mysql(config, sql, adapter)
        self.sql_mock.side_effect = fail
        self.assertFalse(self.execute(plan['fingerprint'])['ok'])
        with self.assertRaisesRegex(host.Rejected, '需人工核对'): self.preview()
        self.assertEqual(sum(x.startswith('DROP DATABASE') for x in self.sql), 1)
        self.assertTrue(self.repo.exists())

    def test_database_connections_or_extra_grants_refuse_delete(self):
        self.with_database()
        for fragment, answer in [('PROCESSLIST', '1'), ('SHOW GRANTS', 'GRANT ALL ON *.* TO `root`@`localhost`')]:
            with self.subTest(fragment=fragment):
                self.sql_mock.side_effect = lambda config, sql, adapter: answer if fragment in sql else self.mysql(config, sql, adapter)
                with self.assertRaises(host.Rejected): self.preview()

    def test_database_admin_without_full_session_visibility_refuses_delete(self):
        self.with_database()
        self.sql_mock.side_effect = lambda config, sql, adapter: 'N' if 'SELECT Process_priv' in sql else self.mysql(config, sql, adapter)
        with self.assertRaisesRegex(host.Rejected, 'PROCESS权限'): self.preview()
        self.assertFalse(any('PROCESSLIST' in sql or sql.startswith('DROP DATABASE') for sql in self.sql))
        self.backup.assert_not_called()

    def test_modified_manifest_after_partial_delete_is_refused(self):
        self.with_database(); self.backup.side_effect = host.Rejected('backup failed')
        self.assertFalse(self.execute()['ok'])
        self.target['back']['port'] = 18002; self.save_target()
        with self.assertRaisesRegex(host.Rejected, '配置已变化'): self.preview()

    def test_partial_deletion_blocks_publish_and_start(self):
        deletion.store({'slug': 'project', 'phase': 'clearing'}, host)
        with self.assertRaises(host.Rejected): deletion.ensure_available('project', host)
        deletion.store({'slug': 'project', 'phase': 'complete'}, host)
        deletion.ensure_available('project', host)

    def test_corrupted_backup_refuses_partial_cleanup(self):
        self.with_database(); plan = self.preview()
        self.reload.side_effect = host.Rejected('reload failed')
        self.assertFalse(self.execute(plan['fingerprint'])['ok'])
        record = deletion.record_for('project', host)
        Path(record['filesBackup']['path']).write_bytes(b'damaged')
        self.reload.side_effect = None
        self.assertFalse(self.execute(plan['fingerprint'])['ok'])
        self.assertTrue(self.repo.exists())

    def test_recreated_database_after_partial_cleanup_is_not_dropped(self):
        self.with_database(); plan = self.preview()
        self.reload.side_effect = host.Rejected('reload failed')
        self.assertFalse(self.execute(plan['fingerprint'])['ok'])
        self.deleted = False
        with self.assertRaisesRegex(host.Rejected, '重新创建'): self.preview()
        self.assertEqual(sum(x.startswith('DROP DATABASE') for x in self.sql), 1)

    def test_status_exposes_database_name_without_passwords(self):
        self.with_database()
        with patch.object(self.control, 'tcp', return_value=True):
            status = self.control.status()
        self.assertEqual(status['database']['databaseName'], self.receipt['name'])
        self.assertEqual(status['database']['username'], self.receipt['runtimeUser'])
        self.assertNotIn('PRIVATE', json.dumps(status))

    def test_current_link_cannot_point_outside_release_directory(self):
        outside = self.root / 'unrelated-content'; outside.mkdir()
        (outside / 'keep.txt').write_text('KEEP')
        current = Path(self.target['front']['current'])
        try:
            current.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('OS account cannot create test symlinks')
        with self.assertRaisesRegex(host.Rejected, '项目范围之外'): self.preview()
        self.assertEqual((outside / 'keep.txt').read_text(), 'KEEP')


if __name__ == '__main__':
    unittest.main()

"""Remove only an explicitly confirmed, stopped, automatically provisioned project."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import tarfile
import types
import uuid

RECORDS = Path('/var/lib/deploy-console-deletions')
ARCHIVES = Path('/var/lib/deploy-console-archives')
UNITS = Path('/etc/systemd/system')
RUNTIME = Path('/etc/deploy-console/projects')
APP_DATA = Path('/var/lib/deploy-projects')


def record_for(slug, host):
    path = RECORDS / (slug + '.json')
    return json.loads(host.trusted(path).read_text(encoding='utf-8')) if path.exists() or path.is_symlink() else None


def ensure_available(slug, host):
    value = record_for(slug, host)
    host.require(value is None or value['phase'] == 'complete', '项目删除尚未完成，请查看删除记录并重试清理，不能启动或发布')


def private_directory(path, host):
    if not path.exists():
        host.trusted(path.parent, True)
        path.mkdir(mode=0o700)
    host.trusted(path, True)
    host.require(path.stat().st_mode & 0o077 == 0, '删除备份目录权限不安全')


def store(record, host):
    private_directory(RECORDS, host)
    host.atomic_json(RECORDS / (record['slug'] + '.json'), record)


def overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def signature(path, host):
    info = host.trusted(path).stat()
    return hashlib.sha256(path.read_bytes() + str(info.st_mtime_ns).encode()).hexdigest()


def backup_choice(request, record, host):
    enabled = request.get('backupBeforeDelete', True)
    confirmed = request.get('confirmWithoutBackup', False)
    host.require(type(enabled) is bool and type(confirmed) is bool, '删除备份选项无效')
    host.require(enabled or confirmed, '请明确确认不备份删除，本次不会生成恢复备份')
    if record and record['phase'] != 'preparing':
        host.require(enabled == record.get('backupBeforeDelete', True), '删除已进入清理阶段，重试必须保持原备份选项')
    return enabled


def record_preview(record):
    return {**record['plan'], 'resume': True, 'backupBeforeDelete': record.get('backupBeforeDelete', True),
            'backupModeLocked': record['phase'] != 'preparing'}


def resources(control, host, record=None):
    target, slug = control.target, control.slug
    # The 20260923 provisioner marked the backend but not the project or frontend.
    # Recognize that format using the generated backend identity, never by editing flags.
    legacy = 'autoManaged' not in target and (target.get('back') or {}).get('autoManaged') is True
    host.require(target.get('autoManaged') is True or legacy,
                 '无法确认该项目由面板自动创建。请管理员核对项目接入文件、服务及目录归属；人工接入项目需单独制定清理方案，不要直接修改autoManaged标记')
    expected_builder = 'deploy-build-p' + hashlib.sha256(slug.encode()).hexdigest()[:16]
    host.require(target['buildUser'] == expected_builder, '构建账号归属不符。请管理员核对接入文件的buildUser与源码目录所有者，未执行删除')
    if legacy:
        back = target['back']
        unit = 'deploy-project-' + slug + '.service'
        host.require(back.get('kind') == 'systemd' and back.get('unit') == unit,
                     '旧版项目后端身份不符。请管理员核对后端是否为本面板创建的独立服务，未执行删除')
        path = UNITS / unit
        if path.exists() or path.is_symlink():
            content = host.trusted(path).read_text(encoding='utf-8')
            lines = content.splitlines()
            user = 'deploy-app-' + hashlib.sha256(slug.encode()).hexdigest()[:16]
            expected = ['User=' + user, 'Group=' + user, 'WorkingDirectory=' + back['current'],
                        'EnvironmentFile=' + str(RUNTIME / slug / 'runtime.env'), 'ReadWritePaths=' + str(APP_DATA / slug)]
            host.require(content.startswith('# Managed by deploy-console: ' + slug + '\n')
                         and all(line in lines for line in expected),
                         '旧版项目缺少自动创建证据或服务配置已改变。请管理员核对后端服务文件、运行账号和运行目录，未执行删除')
        else:
            host.require(record is not None and record['phase'] == 'clearing',
                         '旧版项目后端服务文件缺失，无法核对归属。请管理员恢复并核对原接入配置后重试，未执行删除')
    items, units = [], []
    def add(kind, path, owner=0, link_root=None):
        items.append({'kind': kind, 'name': str(path), 'owner': owner, 'linkRoot': str(link_root) if link_root else None})
    builder = pwd.getpwnam(expected_builder).pw_uid
    host.require(builder > 0, '构建账号无效')
    for side in host.SIDES:
        service = target.get(side)
        if not service:
            continue
        old_front = legacy and side == 'front' and service.get('kind') == 'nginx' and 'autoManaged' not in service
        host.require(service.get('autoManaged') is True or old_front,
                     '前后端含未确认归属的服务。请管理员核对该端接入来源；人工服务不能仅通过修改autoManaged标记纳入删除')
        current = host.absolute(service['current'])
        host.require(len(current.parent.parts) >= 3 and not overlap(current.parent, Path('/www/server'))
                     and current.parent not in (Path('/www/wwwroot'), Path('/srv/www')), '部署目录范围过大或包含系统目录，未执行删除')
        if service['kind'] == 'nginx':
            add('Nginx项目站点', host.nginx_runtime()['directory'] / ('deploy-console-' + slug + '.conf'))
        else:
            unit = 'deploy-project-' + slug + '.service'
            host.require(side == 'back' and service['unit'] == unit, '服务名称不属于自动创建的项目，未执行删除')
            units.append(unit)
            add('后端服务文件', UNITS / unit)
            add('后端端口配置', UNITS / (unit + '.d'))
        add('前端部署链接' if side == 'front' else '后端部署链接', current, link_root=current.parent / 'releases')
        add('前端发布版本' if side == 'front' else '后端发布版本', current.parent / 'releases')
    repo = host.absolute(target['repositoryPath'])
    host.require(len(repo.parts) >= 4 and not overlap(repo, Path('/www/server')), '源码目录范围过大，未执行删除')
    add('Git源码与构建缓存', repo, builder)
    if target.get('back'):
        app_user = 'deploy-app-' + hashlib.sha256(slug.encode()).hexdigest()[:16]
        app_uid = pwd.getpwnam(app_user).pw_uid
        host.require(app_uid > 0, '运行账号无效')
        add('项目运行配置', RUNTIME / slug)
        add('项目运行数据', APP_DATA / slug, app_uid)
    add('项目主机状态', control.directory)
    add('项目接入配置', host.TARGETS / (slug + '.json'))
    for path in host.TARGETS.glob('*.json'):
        if path.stem == slug:
            continue
        other = host.load_target(path.stem)
        old_paths = [Path(other['repositoryPath']), *[Path(other[s]['current']).parent for s in host.SIDES if other.get(s)]]
        host.require(other['buildUser'] != expected_builder, '其他项目共用构建账号，未执行删除')
        host.require(not any(overlap(Path(item['name']), old) for item in items for old in old_paths), '目录与其他项目重叠，未执行删除')
        host.require(not any((other.get(s) or {}).get('unit') in units for s in host.SIDES), '其他项目共用服务，未执行删除')
        db = target.get('database', {})
        host.require(not db.get('identity') or (other.get('database') or {}).get('identity') != db['identity'], '其他项目共用数据库，未执行删除')
    return items, units


def validate_path(item, host):
    path = Path(item['name'])
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    host.trusted(parent, True)
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if item['linkRoot']:
        host.require(path.is_symlink() and info.st_uid == 0 and path.resolve().is_relative_to(Path(item['linkRoot']).resolve()), '部署链接指向项目范围之外，未执行删除')
    else:
        host.require(not path.is_symlink() and info.st_uid == item['owner'] and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)), '删除路径类型或归属冲突，未执行删除')
        if item['owner'] == 0:
            host.trusted(path, path.is_dir())
    # Refuse bind mounts too; os.path.ismount alone does not detect all of them.
    mounts = Path('/proc/self/mountinfo')
    if mounts.exists():
        for line in mounts.read_text(encoding='utf-8').splitlines():
            mount = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
            host.require(not (mount == path or path in mount.parents), '删除范围包含挂载点，请先核对挂载关系')


def stopped(control, record, host):
    clearing = record and record['phase'] == 'clearing'
    host.require(clearing or all((control.directory / ('stop-' + side)).exists() for side in host.SIDES), '服务器尚未完成总关停，未执行删除')
    for side in (*host.SIDES, 'check'):
        names = [control.unit(side)]
        if side in host.SIDES and (control.target.get(side) or {}).get('kind') == 'systemd':
            names.append(control.target[side]['unit'])
        for unit in names:
            result = host.run(['/usr/bin/systemctl', 'show', unit, '--property=ActiveState,MainPID,FragmentPath'], check=False)
            values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            host.require(values.get('ActiveState') in ('inactive', 'failed') and values.get('MainPID') == '0', '项目进程尚未停止或状态无法确认，未执行删除')
            if unit.startswith('deploy-project-') and (UNITS / unit).exists():
                host.require(values.get('FragmentPath') == str(UNITS / unit), '实际运行服务来源与项目配置不符，未执行删除')


def database_state(control, record, host):
    target = control.target.get('database', {'kind': 'none'})
    if target['kind'] == 'none':
        return None
    host.require(target['kind'] == 'mysql' and target.get('autoProvisioned'), '数据库不是本面板自动创建的独立项目库，未执行删除')
    database = host.extension('database')
    receipt = (record or {}).get('database')
    if receipt is None:
        receipt = json.loads(host.trusted(control.directory / 'database.json').read_text(encoding='utf-8'))
    schema, runtime_user, migration_user = database.names(control.slug)
    host.require(receipt['slug'] == control.slug and receipt['name'] == target['identity'] == schema
                 and receipt['runtimeUser'] == runtime_user and receipt['migrationUser'] == migration_user,
                 '数据库名称或账号归属冲突，未执行删除')
    config = database.configuration(host)
    host.require(receipt['serverUuid'] == database.server(config, host) and receipt['port'] == config['port'] == target['port'], 'MySQL实例或端口已变化，未执行删除')
    host.require(receipt['phase'] == 'ready' and receipt.get('restoreTrust') is None, '数据库创建或迁移结果尚未确认，未执行删除')
    account_filter = "User IN ('" + runtime_user + "','" + migration_user + "')"
    if record and record.get('databaseDeleted'):
        count = database.mysql(config, "SELECT (SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='" + schema + "') + (SELECT COUNT(*) FROM mysql.user WHERE " + account_filter + ');', host)
        host.require(count == '0', '已删除的数据库或账号被重新创建，未继续清理')
    else:
        host.require(not record or record['phase'] != 'dropping-database', '数据库删除结果需人工核对，请查看删除记录，未重复执行删除')
        host.require(receipt.get('accessPaused'), '项目数据库访问尚未暂停，未执行删除')
        count = database.mysql(config, 'SELECT COUNT(*) FROM mysql.user WHERE ' + account_filter + ';', host)
        host.require(count == '4', '项目数据库账号包含额外主机授权或缺失账号，未执行删除')
        count = database.mysql(config, 'SELECT COUNT(*) FROM mysql.user WHERE ' + account_filter + " AND Host IN ('localhost','127.0.0.1') AND account_locked='Y';", host)
        host.require(count == '4', '项目数据库账号未全部锁定或归属变化，未执行删除')
        visibility = database.mysql(config, "SELECT Process_priv FROM mysql.user WHERE CONCAT(User,'@',Host)=CURRENT_USER();", host)
        host.require(visibility == 'Y', 'MySQL管理账号需要直接授予PROCESS权限以核对全部活动连接，未执行删除')
        count = database.mysql(config, "SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE USER IN ('" + runtime_user + "','" + migration_user + "');", host)
        host.require(count == '0', '项目数据库仍有活动连接，请关闭连接后重试删除')
        for user in (runtime_user, migration_user):
            for address in ('localhost', '127.0.0.1'):
                grants = database.mysql(config, "SHOW GRANTS FOR '" + user + "'@'" + address + "';", host)
                host.require(grants and all(line.startswith('GRANT USAGE ON *.* TO ') or (' ON `' + schema + '`.* TO ') in line for line in grants.splitlines()), '项目账号含其他数据库或角色授权，未执行删除')
    return receipt, config, database


def preview(control, record, host):
    stopped(control, record, host)
    items, units = resources(control, host, record)
    for item in items:
        validate_path(item, host)
    for unit in units:
        path = UNITS / unit
        if path.exists():
            host.require(path.read_text(encoding='utf-8').startswith('# Managed by deploy-console: ' + control.slug + '\n'), '后端服务文件不是本项目生成的，未执行删除')
        dropins = UNITS / (unit + '.d')
        if dropins.exists():
            host.require(all(p.name == '90-deploy-console-port.conf' and not p.is_symlink() and p.is_file() for p in dropins.iterdir()), '后端含额外服务配置，未执行删除')
    if control.target.get('front'):
        path = host.nginx_runtime()['directory'] / ('deploy-console-' + control.slug + '.conf')
        if path.exists():
            host.require(path.read_text(encoding='utf-8') == control.nginx_content(host.extension('tls').configuration(host)), 'Nginx项目站点存在额外修改或未停止，未执行删除')
        host.run([str(host.trusted(host.nginx_runtime()['binary'])), '-t'])
    db = database_state(control, record, host)
    public = [{'kind': item['kind'], 'name': item['name']} for item in items]
    if db:
        public.extend([{'kind': '项目数据库', 'name': db[0]['name']}, {'kind': '数据库账号', 'name': db[0]['runtimeUser'] + ' / ' + db[0]['migrationUser']}])
    if record:
        plan = record['plan']
    else:
        digest = signature(host.TARGETS / (control.slug + '.json'), host)
        fingerprint = hashlib.sha256(json.dumps([digest, control.state, public], sort_keys=True).encode()).hexdigest()
        plan = {'fingerprint': fingerprint, 'resources': public, 'backupDirectory': str(ARCHIVES / control.slug / fingerprint[:20]), 'resume': False}
    return (record_preview(record) if record else {**plan, 'backupBeforeDelete': True, 'backupModeLocked': False}), items, units, db


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1048576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def backup_files(items, archive, host):
    path = archive / ('files-' + uuid.uuid4().hex + '.tar.gz')
    with path.open('xb') as output:
        os.chmod(path, 0o600)
        with tarfile.open(fileobj=output, mode='w:gz', dereference=False) as tar:
            def regular(info):
                host.require(info.isfile() or info.isdir() or info.issym() or info.islnk(), '项目目录包含特殊文件，备份未完成，未执行删除')
                return info
            for index, item in enumerate(items):
                source = Path(item['name'])
                if source.exists() or source.is_symlink():
                    tar.add(source, arcname=str(index), filter=regular)
        output.flush()
        os.fsync(output.fileno())
    with tarfile.open(path, 'r|gz') as tar:
        for member in tar:
            if member.isfile():
                with tar.extractfile(member) as content:
                    while content.read(1048576):
                        pass
    return {'path': str(path), 'sha256': file_digest(path)}


def remove_resource(item, host):
    validate_path(item, host)
    path = Path(item['name'])
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        host.require(shutil.rmtree.avoids_symlink_attacks, '服务器缺少安全目录清理能力，未执行删除')
        shutil.rmtree(path)


def execute(control, request, record, host):
    backup_enabled = backup_choice(request, record, host)
    plan, items, units, db = preview(control, record, host)
    host.require(request.get('deletionFingerprint') == plan['fingerprint'], '删除范围已变化，请重新打开删除预览并确认')
    archive = Path(plan.get('backupDirectory') or ARCHIVES / control.slug / plan['fingerprint'][:20]) if backup_enabled else None
    plan = {**plan, 'backupBeforeDelete': backup_enabled, 'backupDirectory': str(archive) if archive else None}
    if record is None:
        record = {'slug': control.slug, 'phase': 'preparing', 'plan': plan, 'target': control.target,
                  'targetSignature': signature(host.TARGETS / (control.slug + '.json'), host), 'removed': [], 'databaseDeleted': False}
    if record['phase'] == 'preparing':
        record.update(backupBeforeDelete=backup_enabled, plan=plan)
        store(record, host)
    if record['phase'] == 'preparing':
        if backup_enabled:
            for directory in (ARCHIVES, ARCHIVES / control.slug, archive):
                private_directory(directory, host)
            host.emit('备份项目文件与独立数据库，校验通过后开始删除')
            record['filesBackup'] = backup_files(items, archive, host)
            if db:
                receipt, config, database = db
                record['database'] = receipt
                backup = Path(database.backup(receipt, config, 'database-' + uuid.uuid4().hex, types.SimpleNamespace(directory=archive), host))
                record['databaseBackup'] = {'path': str(backup), 'sha256': file_digest(backup)}
            record['phase'] = 'backed-up'
            host.atomic_json(archive / 'manifest.json', {'resources': items, 'target': control.target, 'files': record['filesBackup'], 'database': record.get('databaseBackup')})
            host.emit('删除恢复备份已校验并保留：' + str(archive))
        else:
            host.emit('用户已确认不备份删除，本次不生成文件或数据库恢复备份')
            record.pop('filesBackup', None)
            record.pop('databaseBackup', None)
            if db:
                # Retain only ownership/phase metadata needed for retries, never credentials or data.
                record['database'] = {key: db[0][key] for key in ('slug', 'name', 'runtimeUser', 'migrationUser', 'serverUuid', 'port', 'phase', 'accessPaused')}
            record['phase'] = 'no-backup-confirmed'
        store(record, host)
    for key in ('filesBackup', 'databaseBackup'):
        if key in record:
            backup = record[key]
            host.require(file_digest(host.trusted(Path(backup['path']))) == backup['sha256'], '备份校验失败，未继续删除')
    if db and not record['databaseDeleted']:
        receipt, config, database = db
        record['phase'] = 'dropping-database'
        store(record, host)
        host.emit('删除本项目数据库与专用账号，共享MySQL保持运行')
        sql = 'DROP DATABASE `' + receipt['name'] + '`;\n'
        sql += '\n'.join("DROP USER '" + user + "'@'" + address + "';" for user in (receipt['runtimeUser'], receipt['migrationUser']) for address in ('localhost', '127.0.0.1'))
        database.mysql(config, sql, host)
        record['databaseDeleted'] = True
    record['phase'] = 'clearing'
    store(record, host)
    host.emit('清理项目前后端、Git源码和运行目录')
    # Remove configuration first and reload before clearing deployed content.
    configs = [item for item in items if item['kind'] in ('Nginx项目站点', '后端服务文件', '后端端口配置')]
    for unit in units:
        if (UNITS / unit).exists():
            host.run(['/usr/bin/systemctl', 'disable', unit])
    for item in configs:
        remove_resource(item, host)
    if control.target.get('front'):
        runtime = host.nginx_runtime()
        host.run([str(host.trusted(runtime['binary'])), '-t'])
        host.nginx_reload(runtime)
    host.run(['/usr/bin/systemctl', 'daemon-reload'])
    for item in items:
        if item not in configs:
            remove_resource(item, host)
    record['phase'] = 'complete'
    # Completed deletion metadata never retains database credentials.
    record.pop('database', None)
    store(record, host)
    return {'ok': True, 'message': '项目及关联资源已删除，备份和操作记录已保留' if backup_enabled else '项目及关联资源已删除，本次未备份，操作记录已保留',
            'backupDirectory': str(archive) if archive else None, 'backupBeforeDelete': backup_enabled}


def handle(request, host):
    with open(host.STATE / 'operation.lock', 'a') as lock:
        try:
            host.fcntl.flock(lock, host.fcntl.LOCK_EX | host.fcntl.LOCK_NB)
        except BlockingIOError:
            raise host.Rejected('服务器正在执行其他操作，请稍后重试删除') from None
        slug = request['slug']
        record = record_for(slug, host)
        target_path = host.TARGETS / (slug + '.json')
        if record and record['phase'] == 'complete':
            if request['action'] == 'delete' and request.get('deletionFingerprint') == record['plan']['fingerprint']:
                enabled = backup_choice(request, record, host)
                return {'ok': True, 'message': '该次项目删除已完成，未重复清理', 'backupDirectory': record['plan']['backupDirectory'], 'backupBeforeDelete': enabled}
            if not target_path.exists():
                return {'ok': True, 'message': '服务器资源已删除，可重试完成面板记录清理', 'deletion': record_preview(record)}
            record = None
        host.require(record is not None or target_path.exists(), '项目尚未完成自动接入，未发现可安全删除的资源')
        if record:
            host.require(not target_path.exists() or signature(target_path, host) == record['targetSignature'], '项目接入配置已变化，未继续删除')
            target = record['target']
        else:
            target = host.load_target(slug)
        # Retry after state removal must not recreate deleted directories.
        control = object.__new__(host.Host)
        control.target, control.slug = target, slug
        control.directory = host.STATE / slug
        control.state_file = control.directory / 'state.json'
        control.reload()
        if request['action'] == 'delete-preview':
            plan, _, _, _ = preview(control, record, host)
            return {'ok': True, 'message': '删除范围已核对', 'deletion': plan}
        try:
            return execute(control, request, record, host)
        except host.Rejected as error:
            saved = record_for(slug, host)
            return {'ok': False, 'message': str(error), 'backupDirectory': saved['plan']['backupDirectory'] if saved else None,
                    'backupBeforeDelete': saved.get('backupBeforeDelete', True) if saved else None}

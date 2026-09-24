"""Provision project-owned MySQL schemas on the existing local server.

Only fixed administration SQL runs with the server credential. Repository migrations
run as a non-root account and receive credentials restricted to their own schema.
"""
import configparser
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import uuid

ADMIN = Path('/etc/deploy-console/mysql.json')
LEGACY_ADMIN = Path('/etc/training-registration/mysql-root.cnf')
RUNTIME = Path('/etc/deploy-console/projects')
TRANSIENT = Path('/run/deploy-console-db')
CLIENT_DIRECTORIES = (Path('/usr/bin'), Path('/www/server/mysql/bin'))


def write_private(path, value, host):
    host.trusted(path.parent, True)
    if path.exists() or path.is_symlink():
        host.trusted(path)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def binary(name, host):
    host.require(name in ('mysql', 'mysqldump'), 'MySQL客户端类型无效')
    found = False
    for directory in CLIENT_DIRECTORIES:
        path = directory / name
        if not path.is_file():
            continue
        found = True
        try:
            trusted = host.trusted(path)
        except host.Rejected:
            # Baota may keep its client under service-owned paths. Never run such
            # a binary as root with the administrator credential; try the next one.
            continue
        if not os.access(trusted, os.X_OK):
            continue
        return str(trusted)
    host.require(not found, 'MySQL客户端或父目录不符合权限要求：请使用root所有、其他用户不可写的系统MySQL客户端；不要递归修改宝塔数据库目录权限')
    raise host.Rejected('未找到MySQL客户端，请安装系统MySQL客户端（mysql和mysqldump）后重试')


def configuration(host):
    if ADMIN.exists():
        value = json.loads(host.trusted(ADMIN).read_text())
    elif LEGACY_ADMIN.exists():
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(host.trusted(LEGACY_ADMIN).read_text())
        client = parser['client']
        password = client.get('password', '').strip()
        if password.startswith('"') and password.endswith('"'):
            password = password[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        value = {'username': client.get('user', 'root'), 'password': password, 'port': int(client.get('port', '3306'))}
    else:
        raise host.Rejected('尚未授权MySQL自动建库：请在“面板设置 → MySQL自动部署”填写一次宝塔MySQL管理账号')
    host.require(isinstance(value, dict) and re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value.get('username', '')), 'MySQL管理账号配置无效')
    host.require(type(value.get('port')) is int and 1 <= value['port'] <= 65535, 'MySQL管理端口无效')
    host.require(isinstance(value.get('password'), str) and len(value['password']) <= 4096 and not any(ord(c) < 32 for c in value['password']), 'MySQL管理凭据无效')
    return value


def option_file(config, host):
    TRANSIENT.mkdir(mode=0o700, exist_ok=True)
    host.trusted(TRANSIENT, True)
    host.require(TRANSIENT.stat().st_mode & 0o077 == 0, '数据库临时目录权限不安全')
    descriptor, name = tempfile.mkstemp(prefix='mysql-', dir=TRANSIENT)
    password = config['password'].replace('\\', '\\\\').replace('"', '\\"')
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        output.write('[client]\nuser=' + config['username'] + '\npassword="' + password + '"\n')
    return Path(name)


def mysql(config, sql, host, timeout=30):
    path = option_file(config, host)
    try:
        result = subprocess.run([binary('mysql', host), '--defaults-file=' + str(path), '--protocol=TCP', '--host=127.0.0.1',
                                 '--port=' + str(config['port']), '--default-character-set=utf8mb4', '--batch', '--raw', '--skip-column-names'],
                                input=sql, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=host.SAFE_ENV, timeout=timeout, check=False)
        host.require(result.returncode == 0, 'MySQL操作失败或权限不足，请核对面板中的管理凭据与发布记录；未输出密码或SQL')
        host.require(len(result.stdout) <= 1048576, 'MySQL校验结果超出限制')
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise host.Rejected('MySQL操作超时，结果需核对；不会自动覆盖现有数据库') from None
    finally:
        path.unlink(missing_ok=True)


def server(config, host):
    facts = mysql(config, 'SELECT @@server_uuid, VERSION();', host).split('\t')
    host.require(len(facts) == 2 and facts[1].startswith('8.'), '自动数据库需要MySQL 8实例，当前版本不匹配')
    result = facts[0]
    host.require(re.fullmatch(r'[0-9a-fA-F-]{36}', result) is not None, 'MySQL服务器标识无效，需要MySQL实例')
    uuid.UUID(result)
    host.require(not config.get('serverUuid') or config['serverUuid'].lower() == result.lower(), 'MySQL实例标识变化，停止自动建库；请核对是否连到另一台实例')
    return result.lower()


def settings(request, host):
    if request['action'] == 'mysql-status':
        configured = ADMIN.exists() or LEGACY_ADMIN.exists()
        config = configuration(host) if configured else {'username': 'root', 'port': 3306}
        return {'ok': True, 'message': 'MySQL配置状态已读取', 'mySql': {'configured': configured, 'username': config['username'], 'port': config['port']}}
    value = request.get('mySql')
    host.require(isinstance(value, dict), '缺少MySQL配置')
    host.require(re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value.get('username', '')), 'MySQL用户名格式无效')
    host.require(type(value.get('port')) is int and 1 <= value['port'] <= 65535, 'MySQL端口无效')
    host.require(isinstance(value.get('password'), str) and 0 < len(value['password']) <= 4096 and not any(ord(c) < 32 for c in value['password']), '请填写MySQL管理密码')
    config = {key: value[key] for key in ('username', 'password', 'port')}
    config['serverUuid'] = server(config, host)
    for receipt_path in host.STATE.glob('*/database.json'):
        receipt = json.loads(host.trusted(receipt_path).read_text())
        host.require(receipt.get('serverUuid') == config['serverUuid'] and receipt.get('port') == config['port'], '已有自动数据库绑定其他实例或端口，不能直接替换全局连接')
    write_private(ADMIN, config, host)
    return {'ok': True, 'message': 'MySQL连接已验证并保存，后续按项目自动建库和迁移', 'mySql': {'configured': True, 'username': config['username'], 'port': config['port']}}


def profile(value, back, host):
    if value is None:
        return None
    host.require(isinstance(value, dict) and set(value) <= {'kind', 'connectionStringName', 'assembly', 'planArguments', 'applyArguments', 'bootstrapArguments'}, 'database部署描述字段无效')
    host.require(value.get('kind') == 'mysql' and back is not None, '自动建库需要MySQL与.NET后端')
    host.require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', value.get('connectionStringName', '')), '数据库连接配置名称无效')
    host.require(value.get('assembly', back['assembly']) == back['assembly'], '数据库迁移必须使用该后端程序集')
    for key in ('planArguments', 'applyArguments', 'bootstrapArguments'):
        args = value.get(key, [])
        host.require(isinstance(args, list) and len(args) <= 8 and all(isinstance(a, str) and re.fullmatch(r'[A-Za-z0-9_.=-]{1,120}', a) for a in args), '数据库迁移参数格式无效')
        if key != 'bootstrapArguments':
            host.require(args, '请在database中提供planArguments和applyArguments迁移入口')
    return {**value, 'assembly': back['assembly']}


def names(slug):
    digest = hashlib.sha256(slug.encode()).hexdigest()[:16]
    # Alphanumeric schema names avoid wildcard semantics in MySQL GRANT statements.
    return 'dc' + re.sub('[^a-z0-9]', '', slug)[:36] + digest[:10], 'dc' + digest, 'dm' + digest


def runtime_path(slug):
    return RUNTIME / slug / 'runtime.env'


def check_runtime(slug, definition, expected, host):
    path = host.trusted(runtime_path(slug))
    key = 'ConnectionStrings__' + definition['connectionStringName']
    entries = [line.split('=', 1)[1].strip() for line in path.read_text().splitlines() if line.startswith(key + '=')]
    host.require(len(entries) <= 1 and (not entries or entries[0] in ('', '""', expected)), '后端已有不同的数据库连接配置，未覆盖；请确认现有数据库归属后处理冲突')
    return path, key


def connection(receipt, migration=False):
    username = receipt['migrationUser'] if migration else receipt['runtimeUser']
    password = receipt['migrationPassword'] if migration else receipt['runtimePassword']
    return 'Server=127.0.0.1;Port=' + str(receipt['port']) + ';Database=' + receipt['name'] + ';User=' + username + ';Password=' + password + ';CharSet=utf8mb4'


def bind_runtime(receipt, definition, host):
    quoted = '"' + connection(receipt) + '"'
    path, key = check_runtime(receipt['slug'], definition, quoted, host)
    lines = [line for line in path.read_text().splitlines() if not line.startswith(key + '=')]
    lines.append(key + '=' + quoted)
    descriptor, temporary = tempfile.mkstemp(prefix='runtime-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write('\n'.join(lines) + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def provision(control, definition, host):
    config = configuration(host)
    server_uuid = server(config, host)
    path = control.directory / 'database.json'
    schema, runtime_user, migration_user = names(control.slug)
    if path.exists():
        receipt = json.loads(host.trusted(path).read_text())
        host.require(receipt.get('slug') == control.slug and receipt.get('serverUuid') == server_uuid and receipt.get('name') == schema and receipt.get('port') == config['port'], '已有数据库绑定与当前项目或MySQL实例冲突')
        restore_guard(receipt, path, config, host)
        host.require(receipt.get('definition') == definition, '数据库迁移入口与已登记配置冲突，未改写；请核对工程描述')
        host.require(receipt.get('phase') in ('ready', 'planned'), '数据库创建或迁移结果待核对，已保留现场；不会自动重建或再次执行不确定操作')
        check_runtime(control.slug, definition, '"' + connection(receipt) + '"', host)
    else:
        check_runtime(control.slug, definition, None, host)
        occupied = mysql(config, "SELECT (SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='" + schema + "') + (SELECT COUNT(*) FROM mysql.user WHERE User IN ('" + runtime_user + "','" + migration_user + "'));", host)
        host.require(occupied == '0', '数据库或账号归属冲突，未覆盖：库 ' + schema + '，账号 ' + runtime_user + ' / ' + migration_user)
        receipt = {'slug': control.slug, 'name': schema, 'runtimeUser': runtime_user, 'migrationUser': migration_user,
                   'runtimePassword': secrets.token_hex(24), 'migrationPassword': secrets.token_hex(24),
                   'serverUuid': server_uuid, 'port': config['port'], 'definition': definition, 'phase': 'planned', 'fresh': True, 'fingerprints': {}}
        write_private(path, receipt, host)
    if receipt['phase'] == 'planned':
        # A durable pending phase makes an interrupted multi-DDL result explicit on retry.
        receipt['phase'] = 'creating'
        write_private(path, receipt, host)
        host.emit('自动创建项目MySQL数据库、运行账号和迁移账号')
        sql = "CREATE DATABASE `" + schema + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;\n"
        for user, password, grants in [(runtime_user, receipt['runtimePassword'], 'SELECT,INSERT,UPDATE,DELETE'),
                                        (migration_user, receipt['migrationPassword'], 'ALL PRIVILEGES')]:
            for account_host in ('localhost', '127.0.0.1'):
                sql += "CREATE USER '" + user + "'@'" + account_host + "' IDENTIFIED BY '" + password + "';\n"
                sql += 'GRANT ' + grants + ' ON `' + schema + "`.* TO '" + user + "'@'" + account_host + "';\n"
        mysql(config, sql, host)
        receipt['phase'] = 'ready'
        write_private(path, receipt, host)
    else:
        exists = mysql(config, "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='" + schema + "';", host)
        host.require(exists == '1', '已登记的数据库不存在，未自动重建以免掩盖数据丢失')
    bind_runtime(receipt, definition, host)
    host.require(not receipt.get('accessPaused'), '项目数据库访问已暂停，请先在页面恢复数据库访问')
    return {'kind': 'mysql', 'identity': schema, 'host': '127.0.0.1', 'port': config['port'], 'managed': True,
            'autoProvisioned': True, 'deployment': definition}


def access(control, action, host):
    host.require(action in ('start', 'stop'), '数据库访问操作无效')
    path = control.directory / 'database.json'
    receipt = json.loads(host.trusted(path).read_text())
    config = configuration(host)
    host.require(receipt['serverUuid'] == server(config, host) and receipt['port'] == config['port'], '数据库实例冲突，未改变访问权限')
    schema, runtime_user, migration_user = names(control.slug)
    host.require(receipt['name'] == schema and receipt['runtimeUser'] == runtime_user and receipt['migrationUser'] == migration_user, '数据库账号归属冲突')
    clause = 'LOCK' if action == 'stop' else 'UNLOCK'
    sql = '\n'.join("ALTER USER '" + user + "'@'" + account_host + "' ACCOUNT " + clause + ';'
                    for user in (runtime_user, migration_user) for account_host in ('localhost', '127.0.0.1'))
    mysql(config, sql, host)
    receipt['accessPaused'] = action == 'stop'
    write_private(path, receipt, host)
    host.emit('项目数据库访问已暂停，共享MySQL实例继续运行' if action == 'stop' else '项目数据库访问已恢复')


def restore_guard(receipt, path, config, host):
    if receipt.get('restoreTrust') is not None:
        host.require(receipt['restoreTrust'] in (0, 1), '数据库恢复标记无效')
        mysql(config, 'SET GLOBAL log_bin_trust_function_creators=' + str(receipt['restoreTrust']) + ';', host)
        receipt.pop('restoreTrust')
        write_private(path, receipt, host)


def backup(receipt, config, operation, control, host):
    directory = control.directory / 'database-backups'
    directory.mkdir(mode=0o700, exist_ok=True)
    host.trusted(directory, True)
    path = directory / (operation + '.sql.gz')
    host.require(not path.exists(), '本次迁移备份已存在，请核对后使用新的发布操作')
    options = option_file(config, host)
    descriptor, pending = tempfile.mkstemp(prefix='backup-', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            process = subprocess.Popen([binary('mysqldump', host), '--defaults-file=' + str(options), '--protocol=TCP', '--host=127.0.0.1',
                                        '--port=' + str(config['port']), '--single-transaction', '--quick', '--skip-lock-tables', '--no-tablespaces',
                                        '--routines', '--events', '--triggers', '--hex-blob', '--set-gtid-purged=OFF', '--default-character-set=utf8mb4', receipt['name']],
                                       stdout=output, stderr=subprocess.DEVNULL, env=host.SAFE_ENV)
            try:
                code = process.wait(timeout=300)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise host.Rejected('数据库备份超时，迁移未执行') from None
        host.require(code == 0 and Path(pending).stat().st_size > 0, '数据库备份失败，迁移未执行')
        with open(pending, 'rb') as source, path.open('xb') as destination:
            os.chmod(path, 0o600)
            with gzip.GzipFile(fileobj=destination, mode='wb') as compressed:
                shutil.copyfileobj(source, compressed)
        with gzip.open(path, 'rb') as verified:
            while verified.read(1048576):
                pass
        return str(path)
    finally:
        options.unlink(missing_ok=True)
        Path(pending).unlink(missing_ok=True)


def migration_command(control, release, definition, receipt, arguments, host, plan_token=None):
    environment = {'ConnectionStrings__' + definition['connectionStringName']: connection(receipt, True),
                   'DeploymentDatabase__ExpectedDatabase': receipt['name'], 'DeploymentDatabase__ExpectedServerUuid': receipt['serverUuid'],
                   'DeploymentBootstrap__Enabled': 'true', 'DeploymentBootstrap__ExpectedDatabase': receipt['name'],
                   'DeploymentBootstrap__ExpectedServerUuid': receipt['serverUuid']}
    if plan_token:
        environment['DeploymentDatabase__PlanToken'] = plan_token
    TRANSIENT.mkdir(mode=0o700, exist_ok=True)
    host.trusted(TRANSIENT, True)
    descriptor, name = tempfile.mkstemp(prefix='migration-', dir=TRANSIENT)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(environment, output)
        wrapper = host.trusted(Path('/usr/local/lib/deploy-console/database-run.py'))
        assembly = host.trusted(release / (definition['assembly'] + '.dll'))
        runtime_user = 'deploy-app-' + hashlib.sha256(control.slug.encode()).hexdigest()[:16]
        argv = ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect', '--unit=deploy-db-' + control.slug,
                '--uid=' + runtime_user, '--working-directory=' + str(release), '--property=Type=exec', '--property=UMask=0077',
                '--property=ProtectSystem=strict', '--property=ProtectHome=yes', '--property=PrivateTmp=yes',
                '--property=NoNewPrivileges=yes', '--property=CapabilityBoundingSet=', '--property=RuntimeMaxSec=900',
                '--property=LoadCredential=database:' + name, '--setenv=ASPNETCORE_ENVIRONMENT=Production',
                '--', '/usr/bin/python3', '-I', str(wrapper), '/usr/bin/dotnet', str(assembly), *arguments]
        try:
            output = host.run(argv, 920).stdout
        finally:
            host.run(['/usr/bin/systemctl', 'stop', 'deploy-db-' + control.slug], 25, check=False)
        lines = [line for line in output.splitlines() if line.startswith('{')]
        host.require(lines and len(lines[-1]) < 65536, '项目迁移入口未返回可识别结果，请实现数据库发布协议')
        return json.loads(lines[-1])
    finally:
        Path(name).unlink(missing_ok=True)


def migrate(control, release, operation, host):
    database = control.target.get('database', {})
    if not database.get('autoProvisioned'):
        return
    definition = database['deployment']
    receipt_path = control.directory / 'database.json'
    receipt = json.loads(host.trusted(receipt_path).read_text())
    config = configuration(host)
    host.require(receipt['serverUuid'] == server(config, host) and receipt['port'] == config['port'], '迁移目标MySQL实例或端口冲突')
    # A killed migration must not leave the shared server's binary-log guard changed.
    restore_guard(receipt, receipt_path, config, host)
    host.require(receipt['phase'] == 'ready', '上次数据库迁移未确认完成，请核对记录及备份；未再次执行或重建数据库')
    host.require(not control.stopped('back'), '后端已停止，数据库迁移未执行')
    host.emit('检查数据库版本和迁移冲突')
    plan = migration_command(control, release, definition, receipt, definition['planArguments'], host)
    host.require(plan.get('status') == 'planned' and isinstance(plan.get('pending'), list) and isinstance(plan.get('fingerprints'), dict)
                 and re.fullmatch(r'[A-Fa-f0-9]{64}', plan.get('planToken', '')), '数据库迁移计划格式无效')
    conflicts = plan.get('conflicts')
    host.require(isinstance(conflicts, list), '数据库迁移冲突信息缺失')
    host.require(not conflicts, '数据库迁移冲突：' + '; '.join(str(item)[:250] for item in conflicts[:3]))
    host.require(all(plan['fingerprints'].get(key) == value for key, value in receipt['fingerprints'].items()), '已应用迁移被修改或当前分支版本倒退，未自动覆盖数据库')
    if plan['pending']:
        if not receipt['fresh']:
            host.emit('自动备份数据库，验证通过后执行升级')
            receipt['backup'] = backup(receipt, config, operation, control, host)
        host.require(not control.stopped('back'), '后端已停止，数据库迁移未执行')
        receipt['phase'] = 'migrating'
        write_private(receipt_path, receipt, host)
        try:
            guard = mysql(config, 'SELECT @@log_bin, @@global.log_bin_trust_function_creators;', host).split('\t')
            host.require(len(guard) == 2 and all(x in ('0', '1') for x in guard), 'MySQL迁移环境检查失败')
            if guard == ['1', '0']:
                receipt['restoreTrust'] = 0
                write_private(receipt_path, receipt, host)
                mysql(config, 'SET GLOBAL log_bin_trust_function_creators=1;', host)
            host.emit('自动执行数据库初始化或版本升级')
            after = migration_command(control, release, definition, receipt, definition['applyArguments'], host, plan['planToken'])
            host.require(after.get('status') == 'applied' and after.get('pending') == [] and after.get('conflicts') == []
                         and after.get('fingerprints') == plan['fingerprints'], '数据库迁移结果未确认，请核对现场和备份')
            receipt['fingerprints'] = after['fingerprints']
            receipt['phase'] = 'ready'
        finally:
            if receipt.get('restoreTrust') is not None:
                mysql(config, 'SET GLOBAL log_bin_trust_function_creators=' + str(receipt['restoreTrust']) + ';', host)
                receipt.pop('restoreTrust')
            write_private(receipt_path, receipt, host)
    if receipt['fresh'] and definition.get('bootstrapArguments'):
        host.emit('初始化项目必要基础数据')
        bootstrap = migration_command(control, release, definition, receipt, definition['bootstrapArguments'], host)
        host.require(bootstrap.get('status') in ('initialized', 'alreadyInitialized'), '项目基础数据初始化未确认，请核对；未重置现有账号')
    receipt['fresh'] = False
    receipt['fingerprints'] = plan['fingerprints']
    write_private(receipt_path, receipt, host)
    host.emit('数据库版本已就绪')

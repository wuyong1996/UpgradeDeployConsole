#!/usr/bin/python3 -I
"""Root-owned Linux adapter. Root policy and target manifests grant capabilities.

stdin is one JSON request, stdout is sanitized NDJSON. No web-supplied shell,
units or build commands are executed. Auto setup validates empty paths and uses fixed templates. Builds run in a restricted systemd
unit under a dedicated unprivileged user. This module is importable for tests.
"""
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import tempfile
import time
import types
import urllib.request
from urllib.parse import urlsplit

TARGETS = Path('/etc/deploy-console/targets')
STATE = Path('/var/lib/deploy-console-host')
SIDES = ('front', 'back')
SAFE_ENV = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
GIT_CREDENTIALS = Path('/run/deploy-console-git')


def adapter():
    return types.SimpleNamespace(**globals())


def extension(name):
    import importlib.util
    require(name in ('database', 'tls', 'deletion'), '扩展类型无效')
    path = trusted(Path(__file__).with_name(name + '.py'))
    spec = importlib.util.spec_from_file_location('console_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def nginx_runtime():
    if Path('/www/server/nginx/sbin/nginx').is_file():
        return {'binary': Path('/www/server/nginx/sbin/nginx'), 'directory': Path('/www/server/panel/vhost/nginx'), 'baota': True}
    return {'binary': Path('/usr/sbin/nginx'), 'directory': Path('/etc/nginx/conf.d'), 'baota': False}


def nginx_reload(runtime):
    run([str(runtime['binary']), '-s', 'reload'] if runtime['baota'] else ['/usr/bin/systemctl', 'reload', 'nginx.service'])


class Rejected(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def emit(step):
    print(json.dumps({'step': step}, ensure_ascii=False), flush=True)


def run(argv, timeout=40, check=True):
    # Drain output continuously, retaining at most 2 MiB. Build scripts can be noisy.
    retained = bytearray()
    process = subprocess.Popen(argv, env=SAFE_ENV, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    def drain():
        with process.stdout as output:
            while True:
                chunk = output.read(8192)
                if not chunk:
                    break
                available = 2097152 - len(retained)
                if available > 0:
                    retained.extend(chunk[:available])
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise Rejected('服务器命令超时，请核对实际状态后重试') from None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=3)
    result = subprocess.CompletedProcess(argv, process.returncode, bytes(retained).decode('utf-8', errors='replace'), '')
    if check and result.returncode:
        raise Rejected('服务器命令失败，请管理员在服务器检查服务日志与接入配置')
    return result


def trusted(path, directory=False):
    """Do not follow symlinks; all ancestors must be root-owned and non-writable."""
    path = Path(path)
    require(path.is_absolute(), '配置路径必须为绝对路径')
    for part in [*reversed(path.parents), path]:
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, '接入文件或父目录权限不安全')
    require(path.is_dir() if directory else path.is_file(), '接入路径类型无效')
    return path


def absolute(value):
    require(isinstance(value, str) and len(value) <= 400 and re.fullmatch(r'/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)+', value), '目录格式无效')
    require(not any(p in ('.', '..') for p in value.split('/')), '目录不能包含跳转片段')
    require(value.startswith(('/opt/', '/srv/', '/www/')), '部署目录必须位于 /opt、/srv 或 /www')
    require(value not in ('/opt/deploy-console', '/srv/deploy-console') and not value.startswith(('/opt/deploy-console/', '/srv/deploy-console/')), '不能管理面板自身目录')
    return Path(value)


def validate_target(target, slug):
    require(target.get('slug') == slug and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', slug), '项目标识不匹配')
    require(target.get('allowedGitHosts') and all(re.fullmatch(r'[A-Za-z0-9.-]+', h) for h in target['allowedGitHosts']), '缺少 Git 主机白名单')
    require(re.fullmatch(r'deploy-build-[a-z][a-z0-9-]{0,18}', target.get('buildUser', '')), '构建账号必须为独立的 deploy-build-项目账号且不超过 32 字符')
    paths = [absolute(target.get('repositoryPath'))]
    units = []
    for side in SIDES:
        service = target.get(side)
        if not service:
            continue
        require(service.get('kind') in ('nginx', 'systemd'), '服务类型仅支持 nginx 或 systemd')
        require(side == 'front' or service['kind'] == 'systemd', '后端需要 systemd 服务')
        require(type(service.get('port')) is int and 1 <= service['port'] <= 65535, '接入端口无效')
        current = absolute(service.get('current'))
        require(current.name == 'current', '发布目录应以 current 结尾')
        paths.extend([current, current.parent / 'releases'])
        if service['kind'] == 'systemd':
            unit = service.get('unit', '')
            require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,100}\.service', unit), '服务名无效')
            require(not unit.startswith(('deploy-console', 'nginx', 'ssh', 'sudo', 'systemd', 'dbus')), '不能控制系统公共服务或面板自身')
            units.append(unit)
            require(service.get('portEnvironment') in ('ASPNETCORE_URLS', 'SERVER_PORT', 'PORT'), '服务端口环境变量未配置')
        require(re.fullmatch(r'/[A-Za-z0-9_./-]*', service.get('healthPath', '/')), '健康检查路径无效')
        recipe = service.get('build', {})
        require(isinstance(recipe.get('commands'), list) and 0 < len(recipe['commands']) <= 8, '缺少构建命令')
        for command in recipe['commands']:
            require(isinstance(command, list) and command and command[0].startswith('/') and all(isinstance(v, str) and '\x00' not in v for v in command), '构建命令必须为绝对程序路径及参数数组')
        for value in (recipe.get('directory', '.'), recipe.get('output', '')):
            require(isinstance(value, str) and value and not value.startswith('/') and '..' not in value.split('/') and re.fullmatch(r'[A-Za-z0-9_./-]+', value), '构建相对目录无效')
    require(target.get('front') or target.get('back'), '至少接入一个服务')
    require(len(units) == len(set(units)), '前后端不能共用服务身份')
    require(not target.get('front') or not target.get('back') or target['front']['port'] != target['back']['port'], '前后端不能共用端口')
    for index, path in enumerate(paths):
        require(all(path != other and path not in other.parents and other not in path.parents for other in paths[index + 1:]), '源码、发布目录不能重叠')
    db = target.get('database', {'kind': 'none'})
    require(db.get('kind') in ('none', 'systemd', 'docker', 'external', 'mysql'), '数据库类型无效')
    if db['kind'] != 'none':
        require(type(db.get('port')) is int and 1 <= db['port'] <= 65535, '数据库端口无效')
        require(re.fullmatch(r'[A-Za-z0-9.:-]+', db.get('host', '')), '数据库主机无效')
        if db.get('managed'):
            require(db['kind'] in ('systemd', 'docker', 'mysql'), '外部数据库不能管理启停')
            identity = db.get('identity', '')
            require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,100}', identity), '数据库身份无效')
            require(not identity.startswith(('deploy-console', 'nginx', 'ssh', 'sudo', 'systemd', 'dbus')) and identity not in units, '数据库身份与其他服务冲突')
            require(db['kind'] != 'systemd' or identity.endswith('.service'), '数据库 systemd 名称必须为 service')
        if db['kind'] == 'mysql':
            require(db.get('autoProvisioned') is True and db.get('managed') is True and re.fullmatch(r'dc[a-z0-9]{1,62}', db.get('identity', '')), '自动MySQL数据库绑定无效')
            require(isinstance(db.get('deployment'), dict), '自动数据库缺少迁移描述')
    return target


def load_target(slug):
    require(isinstance(slug, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', slug), '项目标识无效')
    return validate_target(json.loads(trusted(TARGETS / (slug + '.json')).read_text()), slug)


def validate_source(target, repository, branch):
    require(isinstance(repository, str) and len(repository) <= 2000, 'Git 地址无效')
    url = urlsplit(repository)
    require(url.scheme in ('https', 'ssh') and url.hostname in target['allowedGitHosts'] and url.path.startswith('/') and len(url.path) > 1, 'Git 地址不在允许的主机中')
    require(not url.password and not url.query and not url.fragment and (not url.username if url.scheme == 'https' else url.username == 'git'), 'Git 地址不能包含凭据')
    require(url.port is None or url.port == (443 if url.scheme == 'https' else 22), 'Git 仅支持标准 SSH 或 HTTPS 端口')
    require(isinstance(branch, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', branch) and '..' not in branch and '//' not in branch and not branch.endswith(('/', '.', '.lock')), '分支名称无效')


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp-' + str(os.getpid()))
    with open(temporary, 'x', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


class Host:
    def __init__(self, target):
        self.target = target
        self.slug = target['slug']
        self.directory = STATE / self.slug
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        trusted(self.directory, True)
        self.state_file = self.directory / 'state.json'
        self.default_branch = None
        self.reload()

    def reload(self):
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

    def save(self):
        atomic_json(self.state_file, self.state)

    def stopped(self, side):
        return (self.directory / ('stop-' + side)).exists() or (self.directory / ('failed-' + side)).exists()

    def port(self, side):
        return self.state.get(side, {}).get('port', self.target[side]['port'])

    def unit(self, side):
        return 'deploy-build-' + self.slug + '-' + side + '.service'

    def sandbox(self, side, command, cwd, timeout=600, credential_file=None):
        account = pwd.getpwnam(self.target['buildUser'])
        require(account.pw_uid != 0 and account.pw_uid != pwd.getpwnam('deploy-console').pw_uid, '构建账号不能是 root 或面板账号')
        repo = Path(self.target['repositoryPath'])
        require(repo.is_dir() and not repo.is_symlink() and repo.stat().st_uid == account.pw_uid, '源码目录需要由独立构建账号持有')
        trusted(repo.parent, True)
        require(Path(cwd).resolve().is_relative_to(repo.resolve()), '构建目录越界')
        require(not self.stopped(side) or side == 'check', '服务已暂停，发布已取消')
        argv = ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect', '--unit=' + self.unit(side), '--uid=' + self.target['buildUser'], '--working-directory=' + str(cwd)]
        for prop in ['Type=exec', 'KillMode=control-group', 'TimeoutStopSec=10', 'RuntimeMaxSec=' + str(timeout), 'ProtectSystem=strict', 'ProtectHome=read-only', 'PrivateTmp=yes', 'NoNewPrivileges=yes', 'RestrictSUIDSGID=yes', 'CapabilityBoundingSet=', 'ReadWritePaths=' + str(repo), 'InaccessiblePaths=/etc/deploy-console /var/lib/deploy-console /var/lib/deploy-console-host /opt/deploy-console', 'UMask=0022']:
            argv.append('--property=' + prop)
        for key, value in {**SAFE_ENV, 'HOME': account.pw_dir, 'DOTNET_CLI_HOME': str(repo / '.build-home'), 'NPM_CONFIG_CACHE': str(repo / '.npm-cache')}.items():
            argv.append('--setenv=' + key + '=' + value)
        if credential_file is not None:
            argv.append('--property=LoadCredential=git-auth:' + str(credential_file))
        try:
            return run([*argv, '--', *command], timeout + 20)
        finally:
            # Also clean up if the adapter deadline interrupts systemd-run itself.
            run(['/usr/bin/systemctl', 'stop', self.unit(side)], 25, check=False)

    def git(self, request, side, arguments, cwd, timeout):
        command = ['/usr/bin/git', '-c', 'credential.helper=', '-c', 'http.followRedirects=false', '-c', 'http.sslVerify=true', *arguments]
        credential = request.get('credential')
        if credential is None:
            return self.sandbox(side, command, cwd, timeout)
        require(isinstance(credential, dict) and credential.get('repository') == request['repository'] and request['repository'].startswith('https://'), 'Git凭据与仓库不匹配')
        username, secret = credential.get('username'), credential.get('secret')
        require(isinstance(username, str) and 0 < len(username) <= 200 and ':' not in username and all(ord(c) >= 32 and ord(c) != 127 for c in username), 'Git用户名无效')
        require(isinstance(secret, str) and 0 < len(secret) <= 4096 and all(ord(c) >= 32 and ord(c) != 127 for c in secret), 'Git凭据无效')
        GIT_CREDENTIALS.mkdir(mode=0o700, exist_ok=True)
        trusted(GIT_CREDENTIALS, True)
        require(GIT_CREDENTIALS.stat().st_mode & 0o077 == 0, 'Git凭据临时目录权限不安全')
        descriptor, name = tempfile.mkstemp(prefix='credential-', dir=GIT_CREDENTIALS)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump(credential, output)
            # Only the Git subprocess gets the systemd credential; build recipes do not.
            wrapper = '/usr/local/lib/deploy-console/git-auth.py'
            trusted(Path(wrapper))
            return self.sandbox(side, ['/usr/bin/python3', '-I', wrapper, *command[1:]], cwd, timeout, credential_file=Path(name))
        finally:
            Path(name).unlink(missing_ok=True)

    def branches(self, request, side='check'):
        validate_source(self.target, request['repository'], request['branch'])
        result = self.git(request, side, ['ls-remote', '--symref', '--', request['repository'], 'HEAD', 'refs/heads/*'], self.target['repositoryPath'], 30)
        branches = []
        for line in result.stdout.splitlines():
            parts = line.split('\t')
            if len(parts) == 2 and parts[1] == 'HEAD' and parts[0].startswith('ref: refs/heads/'):
                self.default_branch = parts[0][16:]
            if len(parts) == 2 and re.fullmatch('[0-9a-f]{40}(?:[0-9a-f]{24})?', parts[0]) and parts[1].startswith('refs/heads/'):
                branches.append({'name': parts[1][11:], 'commit': parts[0]})
        require(len(branches) <= 5000, '远端分支数量超过限制')
        return branches

    @staticmethod
    def tcp(host, port):
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            return False

    def healthy(self, side):
        port = self.port(side)
        if side == 'front' and self.target[side]['kind'] == 'nginx':
            tls = extension('tls')
            configuration = tls.configuration(adapter())
            if configuration:
                try:
                    return tls.healthy(self, configuration, adapter())
                except (OSError, ValueError, http.client.HTTPException):
                    return False
        if self.target[side].get('healthMode') == 'tcp':
            return self.tcp('127.0.0.1', port)
        try:
            request = urllib.request.Request('http://127.0.0.1:' + str(port) + self.target[side].get('healthPath', '/'))
            # Do not follow redirects to another host or trust proxy environment variables.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(request, timeout=3) as response:
                return 200 <= response.status < 300
        except (OSError, ValueError):
            return False

    def status(self):
        result = {'ok': True, 'message': '状态已刷新'}
        for side in SIDES:
            service = self.target.get(side)
            if not service:
                continue
            if service['kind'] == 'systemd':
                active = run(['/usr/bin/systemctl', 'is-active', service['unit']], check=False).stdout.strip() == 'active'
                enabled = run(['/usr/bin/systemctl', 'is-enabled', service['unit']], check=False).stdout.strip() == 'enabled'
            else:
                active = not self.stopped(side) and (self.tcp('127.0.0.1', self.port(side)) if nginx_runtime()['baota'] else run(['/usr/bin/systemctl', 'is-active', 'nginx.service'], check=False).stdout.strip() == 'active')
                enabled = True
            current = Path(service['current'])
            commit = self.state.get(side, {}).get('commit') if current.is_symlink() else None
            access_url = None
            if side == 'front' and service['kind'] == 'nginx':
                tls = extension('tls').configuration(adapter())
                if tls:
                    access_url = 'https://' + tls['domain'] + ':' + str(self.port(side)) + '/'
            result[side] = {'state': 'running' if active else 'stopped', 'health': 'healthy' if active and self.healthy(side) else 'unhealthy' if active else 'unknown', 'port': self.port(side), 'path': str(current), 'bootEnabled': enabled, 'commit': commit, 'accessUrl': access_url}
        db = self.target.get('database', {'kind': 'none'})
        if db['kind'] != 'none':
            health = self.tcp(db['host'], db['port'])
            running = None
            if db['kind'] == 'mysql':
                receipt = json.loads(trusted(self.directory / 'database.json').read_text())
                paused = receipt.get('accessPaused', False)
                unresolved = receipt.get('phase') != 'ready'
                pending = receipt.get('fresh', False)
                result['database'] = {'state': 'conflict' if unresolved else 'paused' if paused else 'pending' if pending else 'running' if health else 'stopped',
                                      'health': 'unhealthy' if unresolved else 'unknown' if paused or pending else 'healthy' if health else 'unhealthy', 'port': db['port'], 'path': None,
                                      'databaseName': receipt['name'], 'host': db['host'], 'username': receipt['runtimeUser'],
                                      'migrationState': 'pending' if pending and not unresolved else receipt['phase'], 'appliedMigrations': len(receipt.get('fingerprints', {}))}
                return result
            if db.get('managed'):
                if db['kind'] == 'systemd':
                    running = run(['/usr/bin/systemctl', 'is-active', db['identity']], check=False).stdout.strip() == 'active'
                else:
                    running = run(['/usr/bin/docker', 'inspect', '--format', '{{.State.Running}}', db['identity']], check=False).stdout.strip() == 'true'
            result['database'] = {'state': 'unknown' if running is None else 'running' if running else 'stopped', 'health': 'healthy' if health else 'unhealthy', 'port': db['port'], 'path': None, 'host': db['host']}
        return result

    def nginx_content(self, tls):
        service = self.target['front']
        port = str(self.port('front'))
        content = 'server {\n listen ' + port + (' ssl' if tls else '') + ';\n server_name ' + (tls['domain'] if tls else '_') + ';\n'
        if tls:
            content += ' ssl_certificate ' + tls['certificateFile'] + ';\n ssl_certificate_key ' + tls['privateKeyFile'] + ';\n'
            content += ' ssl_protocols TLSv1.2 TLSv1.3;\n error_page 497 =308 https://' + tls['domain'] + ':' + port + '$request_uri;\n'
        if self.stopped('front'):
            content += ' return 503;\n'
        else:
            content += ' root ' + service['current'] + ';\n index index.html;\n location / { try_files $uri $uri/ /index.html; }\n'
            if self.target.get('back'):
                forwarded = ' proxy_set_header X-Forwarded-For $remote_addr; proxy_set_header X-Forwarded-Proto $scheme;' if tls else ''
                content += ' location /api/ { proxy_pass http://127.0.0.1:' + str(self.port('back')) + '; proxy_set_header Host $http_host; proxy_set_header X-Real-IP $remote_addr;' + forwarded + ' }\n'
        return content + '}\n'

    def nginx(self):
        service = self.target.get('front')
        if not service or service['kind'] != 'nginx':
            return
        runtime = nginx_runtime()
        trusted(runtime['binary'])
        path = runtime['directory'] / ('deploy-console-' + self.slug + '.conf')
        trusted(path.parent, True)
        if path.exists():
            trusted(path)
        else:
            require(not self.tcp('127.0.0.1', self.port('front')), '前端端口已被其他站点或进程占用，未接管该端口')
        before = path.read_bytes() if path.exists() else None
        tls = extension('tls').configuration(adapter())
        path.write_text(self.nginx_content(tls))
        try:
            run([str(runtime['binary']), '-t'])
            nginx_reload(runtime)
        except Rejected:
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
            try:
                nginx_reload(runtime)
            except Rejected:
                pass
            raise

    def service(self, side, action):
        service = self.target.get(side)
        require(service is not None, '服务未接入')
        if service['kind'] == 'nginx':
            self.nginx()
        else:
            run(['/usr/bin/systemctl', action, service['unit']], 90)

    def stop_request(self, sides):
        # Persist intent before taking the deployment lock; kill only that side's build.
        with open(self.directory / 'intent.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for side in sides:
                (self.directory / ('stop-' + side)).touch(mode=0o600)
        for side in sides:
            run(['/usr/bin/systemctl', 'stop', self.unit(side)], 25, check=False)

    def database(self, action):
        db = self.target.get('database', {'kind': 'none'})
        if db['kind'] == 'none':
            return
        if db['kind'] == 'mysql':
            extension('database').access(self, action, adapter())
            return
        require(db.get('managed') and db['kind'] in ('systemd', 'docker'), '数据库未接管，总关停未执行')
        run(['/usr/bin/systemctl' if db['kind'] == 'systemd' else '/usr/bin/docker', action, db['identity']], 90)

    def switch(self, side, release):
        current = Path(self.target[side]['current'])
        trusted(current.parent, True)
        require(not current.exists() or current.is_symlink(), 'current 是现有实体目录，禁止覆盖；请先人工接入')
        trusted(release, True)
        require(release.parent == current.parent / 'releases', '版本目录越界')
        temporary = current.with_name('current.next-' + str(os.getpid()))
        temporary.symlink_to(release)
        os.replace(temporary, current)

    def wait_health(self, side):
        for _ in range(20):
            if self.stopped(side):
                raise Rejected('收到停止请求，已取消发布')
            if self.healthy(side):
                return
            time.sleep(2)
        raise Rejected('健康检查失败，已尝试恢复上一版本')

    def deploy(self, request, force=False):
        side = request['side']
        service = self.target[side]
        require(service.get('independentDeploy') is True, '接入文件尚未确认前后端可独立发布')
        require(not (self.directory / ('stop-' + side)).exists(), '服务已停止，请先启动')
        (self.directory / ('failed-' + side)).unlink(missing_ok=True)
        require(not self.stopped(side), '服务已停止，请先启动')
        emit('检查远端分支')
        branches = self.branches(request, side)
        remote = next((x['commit'] for x in branches if x['name'] == request['branch']), None)
        require(remote, '远端分支不存在')
        previous = self.state.get(side, {}).copy()
        source_hash = hashlib.sha256((request['repository'] + '\n' + request['branch']).encode()).hexdigest()
        deployment_fingerprint = hashlib.sha256(json.dumps({'service': service, 'database': self.target.get('database') if side == 'back' else None}, sort_keys=True).encode()).hexdigest()
        if not force and remote == previous.get('commit') and source_hash == previous.get('source') and deployment_fingerprint == previous.get('deploymentFingerprint'):
            return {'ok': True, 'message': '当前服务已是最新提交', 'commit': remote, 'noChanges': True}
        repo = Path(self.target['repositoryPath'])
        workspace = repo / (side + '-' + request['operationId'])
        require(not workspace.exists(), '操作工作目录已存在，请使用新的操作重试')
        emit('拉取已确认的提交')
        self.git(request, side, ['clone', '--no-checkout', '--single-branch', '--branch', request['branch'], '--', request['repository'], str(workspace)], repo, 180)
        self.sandbox(side, ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', remote], workspace, 60)
        recipe = service['build']
        working = workspace / recipe.get('directory', '.')
        require(working.resolve().is_relative_to(workspace.resolve()), '构建目录不能链接到项目外部')
        emit('构建' + ('前端' if side == 'front' else '后端'))
        for command in recipe['commands']:
            self.sandbox(side, command, working, 900)
        output = working / recipe['output']
        require(output.is_dir() and output.resolve().is_relative_to(workspace.resolve()), '构建产物目录无效')
        require(not output.is_symlink(), '构建产物不能为符号链接')
        for directory, dirs, files in os.walk(output, followlinks=False):
            for name in dirs + files:
                info = (Path(directory) / name).lstat()
                require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), '构建产物包含不允许的链接或特殊文件')
                require(not stat.S_ISREG(info.st_mode) or info.st_nlink == 1, '构建产物不能包含硬链接')
        releases = Path(service['current']).parent / 'releases'
        trusted(releases, True)
        release = releases / request['operationId']
        require(not release.exists(), '版本目录已存在')
        shutil.copytree(output, release, symlinks=False)
        # Repository output may carry setuid or writable permissions; normalize all.
        for directory, dirs, files in os.walk(release):
            os.chmod(directory, 0o755)
            for name in files:
                file = Path(directory) / name
                os.chmod(file, 0o755 if file.stat().st_mode & 0o111 else 0o644)
        (release / '.deploy-console.json').write_text(json.dumps({'commit': remote, 'source': source_hash}))
        if side == 'back' and self.target.get('database', {}).get('autoProvisioned'):
            extension('database').migrate(self, release, request['operationId'], adapter())
        emit('切换版本并检查服务健康')
        self.activate(side, release, {'commit': remote, 'source': source_hash, 'release': str(release), 'port': self.port(side), 'deploymentFingerprint': deployment_fingerprint}, previous)
        # Retain workspace and releases for diagnosis; never delete user's source files.
        return {'ok': True, 'message': '发布成功，健康检查通过', 'commit': remote}

    def activate(self, side, release, next_state, previous):
        require(not self.stopped(side), '已暂停发布，未切换版本')
        self.switch(side, release)
        try:
            self.service(side, 'restart')
            self.wait_health(side)
            if self.target[side].get('autoManaged') and self.target[side]['kind'] == 'systemd':
                run(['/usr/bin/systemctl', 'enable', self.target[side]['unit']])
        except Rejected:
            if previous.get('release'):
                self.switch(side, Path(previous['release']))
            if self.stopped(side):
                self.service(side, 'stop')
            elif previous.get('release'):
                self.service(side, 'restart')
            else:
                (self.directory / ('failed-' + side)).touch(mode=0o600)
                self.service(side, 'stop')
            raise
        next_state['previous'] = previous.get('release')
        self.state[side] = next_state
        self.save()

    def rollback(self, request):
        side = request['side']
        previous = self.state.get(side, {}).copy()
        require(previous.get('previous'), '没有可回退的上一版本')
        release = Path(previous['previous'])
        meta = json.loads(trusted(release / '.deploy-console.json').read_text())
        emit('恢复上一应用版本；数据库保持原状')
        self.activate(side, release, {**meta, 'release': str(release), 'port': self.port(side)}, previous)
        return {'ok': True, 'message': '应用已回退，数据库未回退', 'commit': meta['commit']}

    def change_port(self, request):
        side, port = request['side'], request.get('port')
        require(type(port) is int and 1 <= port <= 65535, '端口必须为 1–65535')
        old = self.port(side)
        if old == port:
            return
        require(not self.tcp('127.0.0.1', port), '目标端口已占用')
        service = self.target[side]
        before = None
        path = None
        if service['kind'] == 'systemd':
            directory = Path('/etc/systemd/system') / (service['unit'] + '.d')
            directory.mkdir(mode=0o755, exist_ok=True)
            trusted(directory, True)
            path = directory / '90-deploy-console-port.conf'
            if path.exists():
                trusted(path)
                before = path.read_bytes()
            env = service['portEnvironment']
            value = 'http://127.0.0.1:' + str(port) if env == 'ASPNETCORE_URLS' else str(port)
            path.write_text('[Service]\nEnvironment="' + env + '=' + value + '"\n')
            run(['/usr/bin/systemctl', 'daemon-reload'])
        self.state.setdefault(side, {})['port'] = port
        try:
            self.nginx()
            if not self.stopped(side):
                self.service(side, 'restart')
                self.wait_health(side)
        except Rejected:
            self.state[side]['port'] = old
            if path:
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(before)
                run(['/usr/bin/systemctl', 'daemon-reload'])
            self.nginx()
            self.service(side, 'stop' if self.stopped(side) else 'restart')
            raise
        self.save()

    def start_all(self, request, began):
        def check_stop():
            for side in SIDES:
                marker = self.directory / ('stop-' + side)
                require(not marker.exists() or marker.stat().st_mtime_ns < began, '收到更新的停止请求，总开始已取消')
        check_stop()
        database = self.target.get('database', {'kind': 'none'})
        pending_database = False
        if database.get('autoProvisioned'):
            receipt = json.loads(trusted(self.directory / 'database.json').read_text())
            require(receipt.get('phase') == 'ready', '上次数据库初始化结果需核对，请查看发布记录；总开始未重复执行不确定的迁移')
            pending_database = receipt.get('fresh', False)
        emit('恢复数据库访问')
        self.database('start')
        for side in ('back', 'front'):
            if not self.target.get(side):
                continue
            # Checking and removing intent must not race a new stop_request.
            with open(self.directory / 'intent.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                check_stop()
                (self.directory / ('stop-' + side)).unlink(missing_ok=True)
                (self.directory / ('failed-' + side)).unlink(missing_ok=True)
            needs_publish = not Path(self.target[side]['current']).exists() or side == 'back' and pending_database
            if needs_publish:
                emit('完成数据库初始化与后端发布' if side == 'back' and pending_database else '首次发布' + ('后端' if side == 'back' else '前端'))
                self.deploy({**request, 'action': 'deploy', 'side': side}, force=True)
            else:
                emit('启动' + ('后端' if side == 'back' else '前端') + '并检查健康')
                self.service(side, 'start')
                self.wait_health(side)
        check_stop()
        result = self.status()
        result['message'] = '总开始完成，数据库及前后端已恢复；自动发布计划保持暂停'
        return result

    def handle(self, request):
        began = time.time_ns()
        action = request.get('action')
        side = request.get('side', '')
        require(action in ('status', 'branches', 'check', 'deploy', 'rollback', 'start', 'start-all', 'stop', 'restart', 'port', 'stop-all'), '操作无效')
        require(action != 'start-all' or side == '', '总开始不能指定单个服务')
        if action == 'start-all' and request.get('requestedAtUnixMs') is not None:
            requested = request['requestedAtUnixMs']
            require(type(requested) is int and requested > 0, '操作请求时间无效')
            # A stop can finish before this helper starts. Compare with queue time,
            # not process startup time, so that later stop still wins.
            began = min(began, requested * 1000000)
        require(re.fullmatch(r'[a-f0-9-]{36}', request.get('operationId', '')), '操作标识无效')
        if action == 'status':
            return self.status()
        extension('deletion').ensure_available(self.slug, adapter())
        if action in ('branches', 'check'):
            branches = self.branches(request)
            if action == 'branches':
                return {'ok': True, 'message': '分支列表已获取', 'branches': branches, 'defaultBranch': self.default_branch}
            commit = next((x['commit'] for x in branches if x['name'] == request['branch']), None)
            require(commit, '远端分支不存在')
            return {'ok': True, 'message': '远端检查完成', 'branches': branches, 'commit': commit}
        require(action in ('stop-all', 'start-all') or side in SIDES and self.target.get(side) or action == 'start' and side == 'database', '服务未配置')
        if action in ('stop-all', 'start-all'):
            db = self.target.get('database', {'kind': 'none'})
            require(db['kind'] == 'none' or db.get('managed'), '外部数据库未接管，整项目启停未执行')
        if action in ('stop', 'stop-all'):
            self.stop_request(SIDES if action == 'stop-all' else [side])
        # Global lock also protects shared Nginx configuration and database identities.
        with open(STATE / 'operation.lock', 'a') as lock:
            deadline = time.monotonic() + 180
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    require(time.monotonic() < deadline, '服务器仍有操作进行中，请稍后重试')
                    time.sleep(0.25)
            self.reload()
            extension('deletion').ensure_available(self.slug, adapter())
            if action == 'start-all':
                return self.start_all(request, began)
            # A stop request can arrive while start is waiting for the global lock.
            # A newer stop always wins; an explicit later start may clear old intent.
            if action == 'start' and side in SIDES:
                marker = self.directory / ('stop-' + side)
                require(not marker.exists() or marker.stat().st_mtime_ns < began, '收到更新的停止请求，未启动服务')
            if action == 'deploy':
                return self.deploy(request)
            if action == 'rollback':
                return self.rollback(request)
            if action == 'stop-all':
                for item in SIDES:
                    (self.directory / ('stop-' + item)).touch(mode=0o600)
                failures = []
                for item in SIDES:
                    if self.target.get(item):
                        try:
                            self.service(item, 'stop')
                        except Rejected:
                            failures.append(item)
                try:
                    self.database('stop')
                except Rejected:
                    failures.append('database')
                require(not failures, '部分关停失败：' + ', '.join(failures) + '；请查看实际状态并重试')
            elif side == 'database':
                self.database('start')
            elif action == 'port':
                self.change_port(request)
            else:
                if action == 'stop':
                    (self.directory / ('stop-' + side)).touch(mode=0o600)
                if action == 'start':
                    (self.directory / ('stop-' + side)).unlink(missing_ok=True)
                    (self.directory / ('failed-' + side)).unlink(missing_ok=True)
                if action == 'restart':
                    require(not self.stopped(side), '服务已暂停，请使用启动')
                self.service(side, action)
                if action in ('start', 'restart'):
                    self.wait_health(side)
            result = self.status()
            result['message'] = '总关停完成，前后端自动发布已暂停' if action == 'stop-all' else '服务操作完成'
            return result


def main():
    try:
        require(os.geteuid() == 0, '服务器入口需要受限 sudo 权限')
        def deadline(signum, frame):
            raise Rejected('操作超过最长执行时间，请检查服务状态')
        signal.signal(signal.SIGALRM, deadline)
        signal.alarm(3000)
        trusted(Path(__file__))
        if sys.argv[1:] == ['--refresh-https']:
            request = {'slug': 'console', 'action': 'https-refresh'}
        else:
            require(len(sys.argv) == 1, '参数无效')
            raw = sys.stdin.readline(16385)
            require(0 < len(raw) <= 16384, '请求长度无效')
            request = json.loads(raw)
        slug = request.get('slug', '')
        require(isinstance(slug, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', slug), '项目标识无效')
        if request.get('action') in ('delete-preview', 'delete'):
            result = extension('deletion').handle(request, adapter())
        elif request.get('action') in ('https-status', 'https-configure', 'https-refresh'):
            with open(STATE / 'operation.lock', 'a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if request['action'] != 'https-refresh':
                        raise Rejected('服务器正在发布，请稍后读取或保存HTTPS配置') from None
                    result = {'ok': True, 'message': '正在发布，HTTPS证书将在下个周期检查'}
                else:
                    result = extension('tls').settings(request, adapter())
        elif request.get('action') in ('mysql-status', 'mysql-configure'):
            with open(STATE / 'operation.lock', 'a') as lock:
                # Settings should not wait behind a long build while the HTTP request times out.
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Rejected('服务器正在发布，请稍后保存MySQL配置') from None
                result = extension('database').settings(request, adapter())
        elif request.get('action') == 'prepare':
            import importlib.util
            module_path = trusted(Path(__file__).with_name('provision.py'))
            spec = importlib.util.spec_from_file_location('console_provision', module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = module.prepare(request, sys.modules[__name__])
        elif request.get('action') == 'stop-all' and not (TARGETS / (slug + '.json')).exists():
            control = Host({'slug': slug})
            control.stop_request(SIDES)
            run(['/usr/bin/systemctl', 'stop', control.unit('check')], 25, check=False)
            result = {'ok': True, 'message': '首次接入已取消，未开始的发布已停止'}
        else:
            target = load_target(slug)
            result = Host(target).handle(request)
    except Rejected as error:
        result = {'ok': False, 'message': str(error)}
    except Exception:
        result = {'ok': False, 'message': '服务器接入检查失败，请管理员检查配置、权限及运行环境'}
    print(json.dumps({'result': result}, ensure_ascii=False), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())

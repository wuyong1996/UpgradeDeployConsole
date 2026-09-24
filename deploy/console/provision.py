"""Create a first deployment from fixed templates; repository code never runs as root."""
import hashlib
import errno
from contextlib import ExitStack
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import time
import xml.etree.ElementTree as ET

POLICY = Path('/etc/deploy-console/auto-deploy.json')
UNITS = Path('/etc/systemd/system')
RUNTIME = Path('/etc/deploy-console/projects')
APP_DATA = Path('/var/lib/deploy-projects')
AUTO_PORT_MIN = 15000
AUTO_PORT_MAX = 25000
SCAN_SKIP = {'.git', 'node_modules', 'bin', 'obj', 'tests', 'test', 'operations', '.local', 'deployment-artifacts', '.vs', '.agents', '.codex'}


def read_repo_file(root, relative, require):
    path = root / relative
    require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()), '仓库配置文件不存在或链接越界')
    require(path.stat().st_size <= 1048576, '仓库配置文件过大')
    return path.read_text(encoding='utf-8-sig')


def relative_path(value, require):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_./-]+', value) and not value.startswith('/') and '..' not in value.split('/'), '仓库部署目录格式无效')
    return value


def descriptor_location(root, require):
    if (root / 'deploy-console.json').exists():
        return root
    locations = []
    count = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SCAN_SKIP and not (Path(current) / d).is_symlink()]
        count += len(files)
        require(count <= 20000, '仓库文件过多，请在Git根目录提供deploy-console.json明确选择工程')
        if 'deploy-console.json' in files:
            locations.append(Path(current))
    require(len(locations) <= 1, '仓库包含多个deploy-console.json，请在Git根目录提供唯一部署描述并填写具体工程路径')
    return locations[0] if locations else root


def read_descriptor(root, require):
    scope = descriptor_location(root, require)
    hint = json.loads(read_repo_file(scope, 'deploy-console.json', require)) if (scope / 'deploy-console.json').exists() else {}
    require(isinstance(hint, dict) and set(hint) <= {'front', 'back', 'database', 'runtime'}, 'deploy-console.json仅支持front、back、database和runtime部署描述')
    return scope, hint


def discover_front(root, scope, hint, require):
    front = None
    candidates = [hint['front']['directory']] if hint.get('front') else ['src/frontend', 'frontend', 'web', 'client', '.']
    if hint.get('front', True) is not None:
        for directory in candidates:
            directory = relative_path(directory, require)
            if not (scope / directory / 'package.json').is_file():
                continue
            package = json.loads(read_repo_file(scope, directory + '/package.json', require))
            if not isinstance(package.get('scripts', {}).get('build'), str):
                continue
            require((scope / directory / 'package-lock.json').is_file(), '前端自动部署需要package-lock.json，请提交锁文件后重试')
            output = (hint.get('front') or {}).get('output', 'build' if 'react-scripts' in package.get('dependencies', {}) else 'dist')
            relative = (scope / directory).relative_to(root).as_posix()
            front = {'directory': relative_path(relative, require), 'output': relative_path(output, require), 'commands': [['/usr/bin/npm', 'ci'], ['/usr/bin/npm', 'run', 'build']]}
            break
    return front


def discover(root, require):
    """Only interpret bounded data. Build scripts execute later as the build user."""
    scope, hint = read_descriptor(root, require)
    front = discover_front(root, scope, hint, require)
    back = None
    projects = []
    if hint.get('back'):
        projects = [scope / relative_path(hint['back']['project'], require)]
    elif hint.get('back', True) is not None:
        # Prefer business backend trees over tooling/examples in a monorepo.
        for directory in ['src/backend', 'backend', 'server', 'src', '.']:
            base = scope / directory
            if not base.is_dir():
                continue
            count = 0
            for current, dirs, files in os.walk(base, followlinks=False):
                dirs[:] = [d for d in dirs if d not in SCAN_SKIP and not (Path(current) / d).is_symlink()]
                count += len(files)
                require(count <= 20000, '项目文件过多，请用deploy-console.json指定后端项目')
                for name in files:
                    if name.endswith('.csproj'):
                        candidate = Path(current) / name
                        xml = ET.fromstring(read_repo_file(root, candidate.relative_to(root).as_posix(), require))
                        if xml.attrib.get('Sdk', '').startswith('Microsoft.NET.Sdk.Web'):
                            projects.append(candidate)
            if projects:
                break
    require(len(projects) <= 1, '检测到多个.NET Web项目，请在deploy-console.json中指定back.project')
    if projects:
        project = projects[0].relative_to(scope).as_posix()
        relative_path(project, require)
        xml = ET.fromstring(read_repo_file(scope, project, require))
        assembly = (hint.get('back') or {}).get('assembly') or next((node.text for node in xml.iter() if node.tag.split('}')[-1] == 'AssemblyName' and node.text), projects[0].stem)
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}', assembly), '后端程序集名称无法自动识别，请指定back.assembly')
        health = (hint.get('back') or {}).get('healthPath', '/')
        require(isinstance(health, str) and re.fullmatch(r'/[A-Za-z0-9_./-]*', health), '后端健康路径无效')
        back = {'project': project, 'assembly': assembly, 'healthPath': health, 'healthMode': 'http' if (hint.get('back') or {}).get('healthPath') else 'tcp',
                'build': {'directory': relative_path(scope.relative_to(root).as_posix(), require), 'output': '.deploy-output/back', 'commands': [['/usr/bin/dotnet', 'publish', project, '-c', 'Release', '--no-self-contained', '-p:UseAppHost=false', '-o', '.deploy-output/back']]}}
        # A monorepo may have no descriptor yet: look next to the identified backend.
        if front is None and 'front' not in hint:
            for parent in projects[0].parents:
                if not parent.is_relative_to(root):
                    break
                front = discover_front(root, parent, {}, require)
                if front:
                    break
    require(front is not None or back is not None, '代码已拉取，但未识别到npm静态前端或.NET Web后端；请配置deploy-console.json或使用服务器接入文件')
    return front, back


def root_directory(path, host):
    if not path.exists():
        root_directory(path.parent, host)
        path.mkdir(mode=0o755)
        # The console has umask 0077; Nginx and the runtime user must traverse releases.
        os.chmod(path, 0o755)
    host.trusted(path, True)


def account(name, host):
    try:
        user = pwd.getpwnam(name)
        host.require(user.pw_uid != 0 and user.pw_shell in ('/usr/sbin/nologin', '/sbin/nologin'), '自动部署账号已被占用')
        return user
    except KeyError:
        host.run(['/usr/sbin/useradd', '--system', '--user-group', '--create-home', '--home-dir', '/var/lib/' + name, '--shell', '/usr/sbin/nologin', name])
        return pwd.getpwnam(name)


def check_paths(request, host):
    setup = request.get('setup')
    host.require(isinstance(setup, dict), '缺少部署目录')
    paths = [host.absolute(setup.get(key)) for key in ('frontPath', 'backPath', 'repositoryPath')]
    host.require(all(path.name == 'current' for path in paths[:2]), '前后端部署路径必须以current结尾')
    for index, path in enumerate(paths):
        host.require(all(path != other and path not in other.parents and other not in path.parents for other in paths[index + 1:]), '源码与部署目录不能重叠')
    for path in paths[:2]:
        host.require(not path.exists() and not path.is_symlink(), '部署目录已有内容，未自动覆盖；请使用空的部署目录或绑定已有服务')
    for manifest in host.TARGETS.glob('*.json'):
        other = host.load_target(manifest.stem)
        for path in paths:
            for occupied in [Path(other['repositoryPath']), *[Path(other[side]['current']).parent for side in host.SIDES if other.get(side)]]:
                host.require(path != occupied and path not in occupied.parents and occupied not in path.parents, '目录与已接入项目重叠')
    return paths


def port_available(port, host):
    # Wildcard bind also detects listeners on specific NICs and sockets that
    # have been bound but are not listening yet. Do not enable address reuse.
    with ExitStack() as sockets:
        families = [(socket.AF_INET, '0.0.0.0')]
        if socket.has_ipv6:
            families.append((socket.AF_INET6, '::'))
        for family, address in families:
            try:
                probe = sockets.enter_context(socket.socket(family, socket.SOCK_STREAM))
                if os.name == 'nt':
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                if family == socket.AF_INET6:
                    probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                probe.bind((address, port))
            except OSError as error:
                if family == socket.AF_INET6 and error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL):
                    continue
                if error.errno in (errno.EADDRINUSE, errno.EACCES):
                    return False
                raise host.Rejected('无法检测服务器端口占用，未分配端口') from None
    return True


def reserved_ports(host):
    occupied = set()
    for path in host.TARGETS.glob('*.json'):
        target = host.load_target(path.stem)
        for side in host.SIDES:
            if target.get(side):
                occupied.add(target[side]['port'])
        if target.get('database', {}).get('port'):
            occupied.add(target['database']['port'])
        state_file = host.STATE / path.stem / 'state.json'
        if state_file.exists():
            state = json.loads(host.trusted(state_file).read_text())
            for side in host.SIDES:
                if state.get(side, {}).get('port'):
                    occupied.add(state[side]['port'])
    return occupied


def choose_port(occupied, host):
    for port in range(AUTO_PORT_MIN, AUTO_PORT_MAX + 1):
        if port not in occupied and port_available(port, host):
            occupied.add(port)
            return port
    raise host.Rejected('自动部署端口范围15000–25000已用完，请释放端口后重试')


def create_runtime(slug, profile, current, port, host):
    username = 'deploy-app-' + hashlib.sha256(slug.encode()).hexdigest()[:16]
    user = account(username, host)
    data = APP_DATA / slug
    root_directory(data.parent, host)
    if not data.exists():
        data.mkdir(mode=0o700)
        os.chown(data, user.pw_uid, user.pw_gid)
    host.require(not data.is_symlink() and data.stat().st_uid == user.pw_uid, '运行数据目录已被占用')
    config = RUNTIME / slug
    root_directory(config, host)
    env = config / 'runtime.env'
    if not env.exists():
        with env.open('x', encoding='utf-8') as output:
            output.write('# Add this project database and runtime settings here; never commit secrets.\n')
            output.write('DataProtection__KeysPath=' + str(data / 'keys') + '\n')
        os.chmod(env, 0o600)
    host.trusted(env)
    unit = 'deploy-project-' + slug + '.service'
    path = UNITS / unit
    marker = '# Managed by deploy-console: ' + slug + '\n'
    content = marker + '\n'.join([
        '[Unit]', 'Description=Deployment project ' + slug, 'After=network.target',
        'ConditionPathExists=' + str(current / (profile['assembly'] + '.dll')),
        'ConditionPathExists=!' + str(host.STATE / slug / 'stop-back'),
        'ConditionPathExists=!' + str(host.STATE / slug / 'failed-back'), '', '[Service]',
        'User=' + username, 'Group=' + username, 'WorkingDirectory=' + str(current),
        'ExecStart=/usr/bin/dotnet ' + str(current / (profile['assembly'] + '.dll')),
        'Environment=ASPNETCORE_ENVIRONMENT=Production', 'Environment=ASPNETCORE_URLS=http://127.0.0.1:' + str(port),
        'EnvironmentFile=' + str(env), 'Restart=on-failure', 'RestartSec=5', 'TimeoutStopSec=60',
        'NoNewPrivileges=true', 'PrivateTmp=true', 'ProtectSystem=strict', 'ProtectHome=true',
        'ReadWritePaths=' + str(data), 'UMask=0077', '', '[Install]', 'WantedBy=multi-user.target', ''])
    if path.exists():
        host.trusted(path)
        host.require(path.read_text().startswith(marker), '后端服务名称已被占用，未覆盖')
    temporary = path.with_suffix('.service.pending')
    host.require(not temporary.exists() and not temporary.is_symlink(), '后端服务临时文件已存在，请检查后重试')
    with temporary.open('x', encoding='utf-8') as output:
        output.write(content)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    host.run(['/usr/bin/systemctl', 'daemon-reload'])
    return unit


def runtime_defaults(slug, values, host):
    host.require(isinstance(values, dict) and len(values) <= 40, 'runtime必须是最多40项的非敏感配置对象')
    path = host.trusted(RUNTIME / slug / 'runtime.env')
    content = path.read_text()
    existing = {line.split('=', 1)[0] for line in content.splitlines() if '=' in line and not line.startswith('#')}
    additions = []
    for key, value in values.items():
        host.require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,119}', key) and key not in ('ASPNETCORE_URLS', 'DOTNET_URLS', 'URLS')
                     and not key.startswith(('ConnectionStrings__', 'DeploymentDatabase__', 'DeploymentBootstrap__')), 'runtime包含受保护的端口、连接或部署配置')
        host.require(isinstance(value, str) and len(value) <= 1000 and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'runtime配置值无效')
        value = value.replace('{data}', str(APP_DATA / slug))
        if key not in existing:
            additions.append(key + '="' + value.replace('\\', '\\\\').replace('"', '\\"') + '"')
    if additions:
        # Atomic merge preserves local runtime overrides and credentials.
        import tempfile
        descriptor, temporary = tempfile.mkstemp(prefix='runtime-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                output.write(content.rstrip('\n') + '\n' + '\n'.join(additions) + '\n')
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def prepare(request, host):
    began = time.time_ns()
    slug = request.get('slug', '')
    host.require(isinstance(slug, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', slug), '项目标识无效')
    host.require(re.fullmatch(r'[a-f0-9-]{36}', request.get('operationId', '')), '操作标识无效')
    existing = host.TARGETS / (slug + '.json')
    with open(host.STATE / 'operation.lock', 'a') as lock:
        host.fcntl.flock(lock, host.fcntl.LOCK_EX)
        host.extension('deletion').ensure_available(slug, host)
        was_registered = existing.exists()
        if was_registered:
            target = host.load_target(slug)
            setup = request.get('setup') or {}
            paths = [host.absolute(setup.get(key)) for key in ('frontPath', 'backPath', 'repositoryPath')]
            host.require(target['repositoryPath'] == str(paths[2]) and all(not target.get(side) or target[side]['current'] == str(paths[index]) for index, side in enumerate(host.SIDES)), '部署目录与已有接入配置冲突，未修改')
            user = pwd.getpwnam(target['buildUser'])
        else:
            policy = json.loads(host.trusted(POLICY).read_text())
            host.require(policy.get('enabled') is True, '服务器未启用自动接入')
            paths = check_paths(request, host)
            username = 'deploy-build-p' + hashlib.sha256(slug.encode()).hexdigest()[:16]
            target = {'slug': slug, 'buildUser': username, 'repositoryPath': str(paths[2]), 'allowedGitHosts': policy.get('allowedGitHosts', []), 'autoManaged': True}
            host.emit('准备独立构建账号与源码目录')
            user = account(username, host)
        host.validate_source(target, request['repository'], request['branch'])
        repo = paths[2]
        root_directory(repo.parent, host)
        if not repo.exists():
            repo.mkdir(mode=0o750)
            os.chown(repo, user.pw_uid, user.pw_gid)
        host.require(not repo.is_symlink() and repo.stat().st_uid == user.pw_uid, '源码目录已有内容或属于其他账号，未接管')
        control = host.Host(target)
        for side in host.SIDES:
            marker = control.directory / ('stop-' + side)
            if not was_registered and marker.exists() and marker.stat().st_mtime_ns < began:
                marker.unlink()
        # Existing stopped sides stay stopped; publishing the other side is still allowed.
        host.require(not all((control.directory / ('stop-' + side)).exists() for side in host.SIDES), '项目已停止，自动接入取消')
        workspace = repo / ('prepare-' + request['operationId'])
        host.require(not workspace.exists(), '拉取工作目录已存在，请重新提交发布')
        host.emit('自动拉取所选分支代码')
        control.git(request, 'check', ['clone', '--single-branch', '--branch', request['branch'], '--', request['repository'], str(workspace)], repo, 180)
        host.emit('识别前端构建与后端启动方式')
        scope, descriptor = read_descriptor(workspace, host.require)
        has_descriptor = (scope / 'deploy-console.json').is_file()
        auto_managed = target.get('autoManaged') or any((target.get(side) or {}).get('autoManaged') for side in host.SIDES)
        if was_registered and not auto_managed and not has_descriptor:
            return {'ok': True, 'message': '沿用人工接入配置，准备发布'}
        front, back = discover(workspace, host.require)
        database = host.extension('database')
        database_profile = database.profile(descriptor.get('database'), back, host)
        occupied = reserved_ports(host)
        target.setdefault('database', {'kind': 'none'})
        for index, side in enumerate(host.SIDES):
            if (front if side == 'front' else back) and not target.get(side):
                path = paths[index]
                host.require(path.name == 'current' and not path.exists() and not path.is_symlink(), '新增服务部署目录已有内容，未覆盖，请处理目录冲突')
                for manifest in host.TARGETS.glob('*.json'):
                    if manifest.stem == slug:
                        continue
                    other = host.load_target(manifest.stem)
                    for occupied_path in [Path(other['repositoryPath']), *[Path(other[s]['current']).parent for s in host.SIDES if other.get(s)]]:
                        host.require(path != occupied_path and path not in occupied_path.parents and occupied_path not in path.parents, '新增服务目录与其他项目冲突')
        if front and not target.get('front'):
            host.require(Path('/usr/bin/npm').is_file(), '服务器缺少/usr/bin/npm，请安装项目要求的Node/npm后重试')
            runtime = host.nginx_runtime()
            host.trusted(runtime['binary'])
            host.trusted(runtime['directory'], True)
            config = runtime['directory'] / ('deploy-console-' + slug + '.conf')
            host.require(not config.exists() and not config.is_symlink(), '同名Nginx配置已存在，未覆盖；请先核对已有站点')
            target['front'] = {'kind': 'nginx', 'port': choose_port(occupied, host), 'current': str(paths[0]), 'independentDeploy': True, 'autoManaged': True, 'healthPath': '/', 'build': front}
        if back and not target.get('back'):
            host.require(Path('/usr/bin/dotnet').is_file(), '服务器缺少.NET SDK，请安装项目要求的版本后重试')
            port = choose_port(occupied, host)
            unit = create_runtime(slug, back, paths[1], port, host)
            target['back'] = {'kind': 'systemd', 'unit': unit, 'port': port, 'portEnvironment': 'ASPNETCORE_URLS', 'current': str(paths[1]), 'independentDeploy': True, 'autoManaged': True, 'healthPath': back['healthPath'], 'healthMode': back['healthMode'], 'build': back['build']}
        if front and target.get('front', {}).get('autoManaged'):
            target['front']['build'] = front
        if back and target.get('back', {}).get('autoManaged'):
            unit_content = host.trusted(UNITS / target['back']['unit']).read_text()
            host.require('ExecStart=/usr/bin/dotnet ' + str(paths[1] / (back['assembly'] + '.dll')) + '\n' in unit_content, '后端程序集启动入口变化，未覆盖运行服务，请处理配置冲突')
            target['back'].update(build=back['build'], healthPath=back['healthPath'], healthMode=back['healthMode'])
            runtime_defaults(slug, descriptor.get('runtime', {}), host)
        if database_profile and (control.directory / 'stop-back').exists():
            host.emit('后端已手动停止，本次保留数据库配置，仅发布未停止的一端')
        elif database_profile:
            host.require(target.get('back', {}).get('autoManaged'), '自动建库需要面板生成的后端服务；已有人工运行配置需先处理接入冲突')
            old_db = target['database']
            host.require(old_db['kind'] == 'none' or old_db.get('autoProvisioned'), '项目已有数据库绑定，未覆盖；请先核对现有数据库归属')
            # Validate paths and service bindings before any database side effect.
            host.validate_target(target, slug)
            target['database'] = database.provision(control, database_profile, host)
        for side, path in zip(host.SIDES, paths[:2]):
            if target.get(side):
                root_directory(path.parent / 'releases', host)
        host.validate_target(target, slug)
        host.require(not all((control.directory / ('stop-' + side)).exists() for side in host.SIDES), '项目已停止，自动接入取消')
        temporary = existing.with_suffix('.auto-pending')
        host.require(not temporary.exists() and not temporary.is_symlink(), '接入配置临时文件已存在，请检查后重试')
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(target, output, ensure_ascii=False)
        os.chmod(temporary, 0o640)
        os.chown(temporary, 0, pwd.getpwnam('deploy-console').pw_gid)
        os.replace(temporary, existing)
        host.emit('服务配置已自动生成，开始构建发布')
        return {'ok': True, 'message': '代码已拉取，缺失服务已自动补接；准备数据库迁移及前后端发布'}

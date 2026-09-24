#!/usr/bin/python3 -I
"""Explicit root-only project registration; does not start or stop any service."""
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('Usage as root: register.py /root/project.json')
    spec = importlib.util.spec_from_file_location('adapter', '/usr/local/lib/deploy-console/host.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    source = adapter.trusted(Path(sys.argv[1]).absolute())
    target = json.loads(source.read_text())
    adapter.validate_target(target, target['slug'])
    destination = adapter.TARGETS / (target['slug'] + '.json')
    adapter.require(not destination.exists(), '项目已接入；修改现有配置请由管理员审阅后原子替换文件')
    new_paths = [Path(target['repositoryPath'])] + [Path(target[s]['current']).parent for s in adapter.SIDES if target.get(s)]
    new_units = {target[s].get('unit') for s in adapter.SIDES if target.get(s) and target[s].get('unit')}
    for path in adapter.TARGETS.glob('*.json'):
        other = adapter.load_target(path.stem)
        adapter.require(other['buildUser'] != target['buildUser'], '不同项目不能共用构建账号')
        old_paths = [Path(other['repositoryPath'])] + [Path(other[s]['current']).parent for s in adapter.SIDES if other.get(s)]
        adapter.require(not any(a == b or a in b.parents or b in a.parents for a in new_paths for b in old_paths), '与其他项目目录重叠')
        adapter.require(not new_units.intersection({other[s].get('unit') for s in adapter.SIDES if other.get(s)}), '与其他项目服务身份重叠')
    username = target['buildUser']
    try:
        account = pwd.getpwnam(username)
        adapter.require(account.pw_uid > 0 and account.pw_shell in ('/usr/sbin/nologin', '/sbin/nologin'), '已有构建账号不符合隔离要求')
    except KeyError:
        subprocess.run(['/usr/sbin/useradd', '--system', '--user-group', '--create-home', '--home-dir', '/var/lib/' + username, '--shell', '/usr/sbin/nologin', username], check=True)
        account = pwd.getpwnam(username)
    repo = Path(target['repositoryPath'])
    repo.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    adapter.trusted(repo.parent, True)
    if not repo.exists():
        repo.mkdir(mode=0o750)
        os.chown(repo, account.pw_uid, account.pw_gid)
    adapter.require(not repo.is_symlink() and repo.stat().st_uid == account.pw_uid, '已有源码目录不属于构建账号；不会修改其权限')
    for side in adapter.SIDES:
        service = target.get(side)
        if not service:
            continue
        current = Path(service['current'])
        current.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        adapter.trusted(current.parent, True)
        adapter.require(not current.exists() and not current.is_symlink(), '已有 current，请先按文档迁移为独立发布目录；不会覆盖现有站点')
        releases = current.parent / 'releases'
        releases.mkdir(mode=0o755, exist_ok=True)
        adapter.trusted(releases, True)
        if service['kind'] == 'systemd':
            properties = subprocess.run(['/usr/bin/systemctl', 'show', service['unit'], '--property=LoadState,User,FragmentPath'], capture_output=True, text=True, check=True).stdout
            adapter.require('LoadState=loaded' in properties, '请先安装业务服务 systemd 单元')
            user = next((line[5:] for line in properties.splitlines() if line.startswith('User=')), '')
            adapter.require(user and user not in ('root', 'deploy-console', username), '业务服务必须使用独立的非 root 运行账号')
    temporary = destination.with_suffix('.pending')
    shutil.copyfile(source, temporary)
    os.chown(temporary, 0, pwd.getpwnam('deploy-console').pw_gid)
    os.chmod(temporary, 0o640)
    os.replace(temporary, destination)
    print('Registered ' + target['slug'] + '. No services were started or stopped. Add the same slug in the console.')


if __name__ == '__main__':
    main()

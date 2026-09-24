"""Assemble and verify a Linux package. Cleanup is performed by the PowerShell entry point."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tarfile


def plain_tree(path):
    if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        raise ValueError('Package input cannot be a symbolic link: ' + str(path))
    if not path.is_file() and not path.is_dir():
        raise ValueError('Package input must be an ordinary file or directory: ' + str(path))
    if path.is_dir():
        for child in path.iterdir():
            plain_tree(child)


def package(project, stage, version):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,79}', version):
        raise ValueError('Invalid package version')
    project, stage = Path(project).resolve(), Path(stage).resolve()
    if stage.parent != project / '.local' or not re.fullmatch(r'package-[a-f0-9]{32}', stage.name):
        raise ValueError('Unexpected package staging location')
    payload = stage / ('deploy-console-upload-' + version)
    published = payload / 'published'
    plain_tree(published)
    for required in ('DeployConsole.Api.dll', 'DeployConsole.Api.deps.json', 'DeployConsole.Api.runtimeconfig.json', 'wwwroot/index.html'):
        if not (published / required).is_file():
            raise ValueError('Incomplete publish output: ' + required)
    html = (published / 'wwwroot/index.html').read_text(encoding='utf-8-sig')
    assets = re.findall(r'(?:src|href)="(/assets/[^"<>]+)"', html)
    if not assets or not any(asset.endswith('.js') for asset in assets):
        raise ValueError('Frontend assets missing from index.html')
    for asset in assets:
        path = published / 'wwwroot' / asset.lstrip('/')
        if not path.resolve().is_relative_to(published / 'wwwroot') or not path.is_file():
            raise ValueError('Missing or unsafe frontend asset')
    console = payload / 'console'
    console.mkdir()
    source = project / 'deploy/console'
    plain_tree(source)
    for path in source.iterdir():
        if path.name == '__pycache__':
            continue
        plain_tree(path)
        if not path.is_file():
            raise ValueError('Unexpected directory in deploy/console')
        data = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
        if path.suffix == '.py':
            ast.parse(data)
        if path.name == 'upgrade.sh':
            data, count = re.subn(r'archive=\$\{1:-/root/deploy-console-server-[A-Za-z0-9-]+\.tar\.gz\}',
                                  'archive=${1:-/root/deploy-console-server-' + version + '.tar.gz}', data)
            if count != 1:
                raise ValueError('Upgrade default archive template was not found exactly once')
        (console / path.name).write_text(data, encoding='utf-8', newline='\n')
    docs = payload / 'docs'
    docs.mkdir()
    for name in ('deploy-console.md', 'deploy-console-package.md', 'deploy-console-project-onboarding.md',
                 'deploy-console-delete-upgrade.md', 'deploy-console-https-upgrade.md',
                 'deploy-console-port-range-upgrade.md', 'deploy-console-start-all-upgrade.md'):
        plain_tree(project / 'docs' / name)
        shutil.copyfile(project / 'docs' / name, docs / name)
    examples = project / 'docs/deploy-console-onboarding-examples'
    plain_tree(examples)
    shutil.copytree(examples, docs / examples.name)
    metadata = {'version': version, 'builtAtUtc': datetime.now(timezone.utc).isoformat(), 'assets': assets,
                'apiSha256': hashlib.sha256((published / 'DeployConsole.Api.dll').read_bytes()).hexdigest()}
    (payload / 'BUILD.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    archive_name = 'deploy-console-server-' + version + '.tar.gz'
    guide = f'''# 服务器部署包 {version}

将本压缩包`{archive_name}`和`upgrade-deploy-console.sh`上传至服务器`/root/`。
在服务器root终端执行：

```bash
bash /root/upgrade-deploy-console.sh /root/{archive_name}
```

包内含最新Release API、前端页面、Linux适配器及接入说明。安装保留现有面板密码、项目和服务器配置。
不需要上传或读取.sha256。包不存在时跳过安装；面板健康后清理本次包与能确认匹配的解压目录。
完成后浏览器Ctrl+F5刷新。打包主机的旧包/旧解压目录在新包校验成功后自动删除，只保留最新交付。

服务端仍需已安装.NET 10 ASP.NET Core Runtime、Python 3.10+、Git、sudo和systemd，具体要求见包内docs/deploy-console.md。
该包已校验归档完整性及页面文件引用，打包成功不代表已在服务器安装或完成真实Linux/MySQL操作验收。
'''
    (payload / 'UPGRADE.md').write_text(guide, encoding='utf-8', newline='\n')
    paths = sorted(path for path in payload.rglob('*') if path.is_file())
    plain_tree(payload)
    for path in paths:
        if path.suffix in ('.key', '.pem', '.pfx', '.p12', '.pyc') or path.name in ('state.json', 'password.txt', '.env'):
            raise ValueError('Unexpected sensitive/generated file in package: ' + path.name)
    delivery = stage / 'delivery'
    delivery.mkdir()
    archive = delivery / archive_name
    with tarfile.open(archive, 'x:gz', format=tarfile.PAX_FORMAT) as tar:
        for path in paths:
            info = tar.gettarinfo(str(path), arcname=path.relative_to(stage).as_posix())
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            with path.open('rb') as stream:
                tar.addfile(info, stream)
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        if len(members) != len(paths):
            raise ValueError('Archive member count mismatch')
        for member in members:
            if not member.isfile() or member.name.startswith('/') or '..' in Path(member.name).parts:
                raise ValueError('Unsafe archive entry')
            if tar.extractfile(member).read() != (stage / member.name).read_bytes():
                raise ValueError('Archive content mismatch')
    shutil.copyfile(console / 'upgrade.sh', delivery / 'upgrade-deploy-console.sh')
    (delivery / 'DEPLOY.md').write_text(guide, encoding='utf-8', newline='\n')
    print(json.dumps({'version': version, 'files': len(paths), 'bytes': archive.stat().st_size,
                      'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--version', required=True)
    arguments = parser.parse_args()
    package(arguments.project, arguments.stage, arguments.version)

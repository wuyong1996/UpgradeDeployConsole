"""Read existing ACME PEM files and apply validated snapshots to managed sites."""
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile

SETTINGS = Path('/etc/deploy-console/https.json')
CERTIFICATES = Path('/etc/deploy-console/tls')
OPENSSL = Path('/usr/bin/openssl')


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_input(value, host):
    host.require(isinstance(value, dict), '请填写默认域名和证书文件路径')
    domain = value.get('domain', '')
    host.require(isinstance(domain, str) and len(domain) <= 253 and re.fullmatch(
        r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', domain), '默认域名格式无效，请只填写域名，不包含协议、端口或路径')
    for field in ('certificatePath', 'privateKeyPath'):
        path = value.get(field, '')
        # A wildcard certificate name contains a literal '*'; paths are never globbed or sent to a shell.
        host.require(isinstance(path, str) and len(path) <= 512 and re.fullmatch(r'/[A-Za-z0-9_.*-]+(?:/[A-Za-z0-9_.*-]+)+', path)
                     and not any(p in ('.', '..') for p in path.split('/')), '证书和私钥必须填写有效的服务器绝对文件路径')
    host.require(value['certificatePath'] != value['privateKeyPath'], '完整证书链和私钥必须使用不同文件')
    return {key: value[key] for key in ('domain', 'certificatePath', 'privateKeyPath')}


def configuration(host):
    if not SETTINGS.exists() and not SETTINGS.is_symlink():
        return None
    return json.loads(host.trusted(SETTINGS).read_text())


def public_status(value):
    return {'configured': value is not None, 'domain': value['domain'] if value else '',
            'certificatePath': value['certificatePath'] if value else '', 'privateKeyPath': value['privateKeyPath'] if value else '',
            'expiresAt': value.get('expiresAt') if value else None, 'appliedAt': value.get('appliedAt') if value else None,
            'lastCheckedAt': value.get('lastCheckedAt') if value else None, 'syncError': value.get('syncError') if value else None}


def atomic_write(path, content, mode, host):
    host.trusted(path.parent, True)
    if path.exists() or path.is_symlink():
        host.trusted(path)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save(value, host):
    atomic_write(SETTINGS, json.dumps(value, ensure_ascii=False).encode(), 0o600, host)


def read_sources(value, host):
    contents = []
    for field in ('certificatePath', 'privateKeyPath'):
        path = Path(value[field])
        host.require(path.is_file(), '证书或私钥文件不存在，请核对服务器上的完整路径')
        host.trusted(path)
        info = path.stat()
        host.require(info.st_nlink == 1 and 0 < info.st_size <= 1048576, '证书或私钥文件类型、链接数或大小不符合要求')
        if field == 'privateKeyPath':
            host.require(info.st_mode & 0o077 == 0, '私钥必须仅root可读，请将私钥文件权限设置为600')
        with path.open('rb') as source:
            data = source.read(1048577)
        host.require(0 < len(data) <= 1048576, '证书或私钥文件大小不符合要求')
        contents.append(data)
    return contents


def snapshot(value, contents, host):
    host.require(OPENSSL.is_file(), '服务器缺少OpenSSL，无法验证证书')
    host.trusted(OPENSSL)
    CERTIFICATES.mkdir(mode=0o700, exist_ok=True)
    host.trusted(CERTIFICATES, True)
    fingerprint = hashlib.sha256(contents[0] + b'\0' + contents[1]).hexdigest()
    with tempfile.TemporaryDirectory(prefix='.pending-', dir=CERTIFICATES) as directory:
        pending = Path(directory)
        certificate, key = pending / 'fullchain.pem', pending / 'private.key'
        for path, data in zip((certificate, key), contents):
            path.write_bytes(data)
            os.chmod(path, 0o600)
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(certificate), str(key), password=lambda: '')
        except (ssl.SSLError, OSError):
            raise host.Rejected('证书与私钥不匹配、格式无效或私钥已加密，请提供PEM完整证书链和对应未加密私钥') from None
        verified = host.run([str(OPENSSL), 'verify', '-purpose', 'sslserver', '-verify_hostname', value['domain'],
                             '-untrusted', str(certificate), str(certificate)], check=False)
        host.require(verified.returncode == 0, '证书验证失败：请检查域名覆盖、有效期、完整证书链及系统根证书信任')
        expiry = host.run([str(OPENSSL), 'x509', '-in', str(certificate), '-noout', '-enddate']).stdout.strip()
        host.require(expiry.startswith('notAfter='), '无法读取证书有效期')
        expires_at = datetime.fromtimestamp(ssl.cert_time_to_seconds(expiry.split('=', 1)[1]), timezone.utc).isoformat()
        destination = CERTIFICATES / fingerprint
        if destination.exists():
            host.trusted(destination, True)
            for name, data in zip(('fullchain.pem', 'private.key'), contents):
                host.require(host.trusted(destination / name).read_bytes() == data, '受管证书快照不一致，请检查服务器文件')
        else:
            os.rename(pending, destination)
    return {**value, 'fingerprint': fingerprint, 'certificateFile': str(destination / 'fullchain.pem'),
            'privateKeyFile': str(destination / 'private.key'), 'expiresAt': expires_at,
            'appliedAt': now(), 'lastCheckedAt': now(), 'syncError': None}


def apply(value, host):
    runtime = host.nginx_runtime()
    host.trusted(runtime['binary'])
    host.trusted(runtime['directory'], True)
    changes = []
    for target_path in host.TARGETS.glob('*.json'):
        target = host.load_target(target_path.stem)
        if (target.get('front') or {}).get('kind') != 'nginx':
            continue
        path = runtime['directory'] / ('deploy-console-' + target['slug'] + '.conf')
        # Pending first deployments get HTTPS when their own activation creates the site.
        if path.exists():
            before = host.trusted(path).read_bytes()
            changes.append((path, before, host.Host(target).nginx_content(value).encode()))
    old_settings = host.trusted(SETTINGS).read_bytes() if SETTINGS.exists() else None
    written = []
    try:
        for path, before, after in changes:
            atomic_write(path, after, 0o644, host)
            written.append((path, before))
        host.run([str(runtime['binary']), '-t'])
        host.nginx_reload(runtime)
        save(value, host)
    except Exception:
        for path, before in written:
            atomic_write(path, before, 0o644, host)
        if old_settings is not None:
            atomic_write(SETTINGS, old_settings, 0o600, host)
        else:
            SETTINGS.unlink(missing_ok=True)
        try:
            host.run([str(runtime['binary']), '-t'])
            host.nginx_reload(runtime)
        except Exception:
            raise host.Rejected('HTTPS应用失败，已还原文件，但Nginx重载失败，请检查服务器Nginx日志') from None
        raise host.Rejected('HTTPS应用失败，已还原原配置，请检查Nginx配置与证书文件') from None


def settings(request, host):
    value = configuration(host)
    if request['action'] == 'https-status':
        return {'ok': True, 'message': 'HTTPS配置已读取', 'https': public_status(value)}
    refresh = request['action'] == 'https-refresh'
    if refresh and value is None:
        return {'ok': True, 'message': '尚未配置HTTPS', 'https': public_status(None)}
    try:
        source = validate_input(value if refresh else request.get('https'), host)
        contents = read_sources(source, host)
        fingerprint = hashlib.sha256(contents[0] + b'\0' + contents[1]).hexdigest()
        if refresh and value.get('fingerprint') == fingerprint:
            host.require(datetime.fromisoformat(value['expiresAt']) > datetime.now(timezone.utc), '当前HTTPS证书已过期，请检查ACME续期结果')
            value.update(lastCheckedAt=now(), syncError=None)
            save(value, host)
            return {'ok': True, 'message': 'HTTPS证书未变化', 'https': public_status(value)}
        candidate = snapshot(source, contents, host)
        apply(candidate, host)
        return {'ok': True, 'message': 'HTTPS证书验证通过，已应用到前端站点', 'https': public_status(candidate)}
    except host.Rejected as error:
        if refresh and value is not None:
            value.update(lastCheckedAt=now(), syncError=str(error))
            save(value, host)
        raise


def healthy(control, value, host):
    # Connect locally but verify the public hostname and trust chain with SNI.
    context = ssl.create_default_context()
    with socket.create_connection(('127.0.0.1', control.port('front')), timeout=3) as connection:
        with context.wrap_socket(connection, server_hostname=value['domain']) as secure:
            path = control.target['front'].get('healthPath', '/')
            request = 'GET ' + path + ' HTTP/1.1\r\nHost: ' + value['domain'] + '\r\nConnection: close\r\n\r\n'
            secure.sendall(request.encode('ascii'))
            with http.client.HTTPResponse(secure) as response:
                response.begin()
                return 200 <= response.status < 300

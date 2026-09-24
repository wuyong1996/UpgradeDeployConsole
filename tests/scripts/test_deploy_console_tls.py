"""HTTPS settings and certificate tests; never modifies real Nginx or trust stores."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from test_deploy_console import host, ROOT

spec = importlib.util.spec_from_file_location('console_tls', ROOT / 'deploy/console/tls.py')
tls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tls)


class TlsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(tls, 'SETTINGS', self.root / 'https.json'))
        self.stack.enter_context(patch.object(tls, 'CERTIFICATES', self.root / 'tls'))
        self.stack.enter_context(patch.object(host, 'trusted', side_effect=lambda p, *args: Path(p)))
        self.stack.enter_context(patch.object(host, 'STATE', self.root / 'state'))
        self.stack.enter_context(patch.object(host, 'TARGETS', self.root / 'targets'))
        host.TARGETS.mkdir()
        self.nginx = self.root / 'nginx'
        self.nginx.mkdir()
        self.runtime = {'binary': self.root / 'nginx-bin', 'directory': self.nginx, 'baota': True}
        self.stack.enter_context(patch.object(host, 'nginx_runtime', return_value=self.runtime))
        self.input = {'domain': 'www.example.test', 'certificatePath': '/etc/acme/fullchain.pem', 'privateKeyPath': '/etc/acme/private.key'}
        self.value = {**self.input, 'fingerprint': tls.hashlib.sha256(b'cert\0key').hexdigest(),
                      'certificateFile': '/etc/deploy-console/tls/old/fullchain.pem', 'privateKeyFile': '/etc/deploy-console/tls/old/private.key',
                      'expiresAt': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), 'appliedAt': tls.now()}

    def site(self, slug='project'):
        target = json.loads((ROOT / 'deploy/console/project.example.json').read_text())
        target['slug'] = slug
        (host.TARGETS / (slug + '.json')).write_text(json.dumps(target))
        path = self.nginx / ('deploy-console-' + slug + '.conf')
        path.write_text('original ' + slug)
        return host.Host(target), path

    def test_input_rejects_nginx_injection_url_ip_and_traversal(self):
        self.assertEqual(tls.validate_input(self.input, host), self.input)
        for field, value in [('domain', 'example.test; return 200;'), ('domain', 'https://example.test'), ('domain', '127.0.0.1'),
                             ('domain', '*.example.test'), ('certificatePath', '/root/../etc/file'), ('privateKeyPath', '/etc/file;bad'),
                             ('privateKeyPath', '/etc/acme/fullchain.pem')]:
            with self.subTest(field=field, value=value), self.assertRaises(host.Rejected):
                tls.validate_input({**self.input, field: value}, host)

    def test_acme_wildcard_certificate_paths_preserve_literal_names(self):
        value = {'domain': 'www.halfsuger.top',
                 'certificatePath': '/root/.acme.sh/*.halfsuger.top_ecc/fullchain.cer',
                 'privateKeyPath': '/root/.acme.sh/*.halfsuger.top_ecc/*.halfsuger.top.key'}
        self.assertEqual(tls.validate_input(value, host), value)

    def test_status_does_not_expose_snapshot_or_private_key_contents(self):
        result = tls.public_status({**self.value, 'privateKeyContents': 'SECRET'})
        self.assertTrue(result['configured'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('fingerprint', result)
        self.assertNotIn('certificateFile', result)
        self.assertFalse(tls.public_status(None)['configured'])

    def test_untrusted_source_is_rejected_before_reading(self):
        source = self.root / 'source.pem'
        source.write_text('test')
        with patch.object(host, 'trusted', side_effect=host.Rejected('unsafe')), self.assertRaisesRegex(host.Rejected, 'unsafe'):
            tls.read_sources({**self.input, 'certificatePath': str(source)}, host)

    def test_private_key_with_public_permissions_is_rejected(self):
        certificate, key = self.root / 'cert.pem', self.root / 'key.pem'
        certificate.write_text('cert')
        key.write_text('key')
        os.chmod(key, 0o644)
        with self.assertRaisesRegex(host.Rejected, '私钥必须仅root'):
            tls.read_sources({**self.input, 'certificatePath': str(certificate), 'privateKeyPath': str(key)}, host)

    def test_apply_updates_only_project_sites_and_preserves_stop(self):
        control, path = self.site()
        (control.directory / 'stop-front').touch()
        unrelated = self.nginx / 'unrelated.conf'
        unrelated.write_text('keep')
        with patch.object(host, 'run'), patch.object(host, 'nginx_reload') as reload:
            tls.apply(self.value, host)
        content = path.read_text()
        self.assertIn('listen 8080 ssl;', content)
        self.assertIn('server_name www.example.test;', content)
        self.assertIn('return 503;', content)
        self.assertNotIn('proxy_pass', content)
        self.assertEqual(unrelated.read_text(), 'keep')
        self.assertEqual(tls.configuration(host)['domain'], self.input['domain'])
        reload.assert_called_once_with(self.runtime)

    def test_proxy_marks_https_and_redirects_plain_http_on_same_port(self):
        control, _ = self.site()
        content = control.nginx_content(self.value)
        self.assertIn('X-Forwarded-Proto $scheme', content)
        self.assertIn('proxy_pass http://127.0.0.1:', content)
        self.assertIn('error_page 497 =308 https://www.example.test:8080$request_uri;', content)
        self.assertIn('ssl_protocols TLSv1.2 TLSv1.3;', content)

    def test_nginx_failure_restores_every_site_and_settings(self):
        _, first = self.site()
        _, second = self.site('another')
        tls.save(self.value, host)
        before = tls.SETTINGS.read_bytes()
        with patch.object(host, 'run', side_effect=[host.Rejected('invalid nginx'), None]), patch.object(host, 'nginx_reload'), self.assertRaisesRegex(host.Rejected, '已还原原配置'):
            tls.apply({**self.value, 'domain': 'other.example.test'}, host)
        self.assertEqual(first.read_text(), 'original project')
        self.assertEqual(second.read_text(), 'original another')
        self.assertEqual(tls.SETTINGS.read_bytes(), before)

    def test_unchanged_renewal_does_not_reload(self):
        tls.save(self.value, host)
        with patch.object(tls, 'read_sources', return_value=[b'cert', b'key']), patch.object(tls, 'apply') as apply:
            result = tls.settings({'action': 'https-refresh'}, host)
        self.assertTrue(result['ok'])
        self.assertIsNotNone(result['https']['lastCheckedAt'])
        apply.assert_not_called()

    def test_invalid_renewal_keeps_last_good_snapshot_and_reports_error(self):
        tls.save(self.value, host)
        with patch.object(tls, 'read_sources', return_value=[b'newcert', b'key']), patch.object(tls, 'snapshot', side_effect=host.Rejected('certificate invalid')), patch.object(tls, 'apply') as apply, self.assertRaises(host.Rejected):
            tls.settings({'action': 'https-refresh'}, host)
        saved = tls.configuration(host)
        self.assertEqual(saved['certificateFile'], self.value['certificateFile'])
        self.assertEqual(saved['syncError'], 'certificate invalid')
        apply.assert_not_called()

    def test_valid_renewal_applies_new_snapshot(self):
        tls.save(self.value, host)
        candidate = {**self.value, 'certificateFile': '/etc/deploy-console/tls/new/fullchain.pem'}
        with patch.object(tls, 'read_sources', return_value=[b'newcert', b'key']), patch.object(tls, 'snapshot', return_value=candidate), patch.object(tls, 'apply') as apply:
            self.assertTrue(tls.settings({'action': 'https-refresh'}, host)['ok'])
        apply.assert_called_once_with(candidate, host)

    def test_new_sites_inherit_https_and_status_reports_front_url_only(self):
        control, path = self.site()
        tls.save(self.value, host)
        with patch.object(host, 'extension', return_value=tls), patch.object(host, 'run', return_value=types.SimpleNamespace(stdout='active')), patch.object(host, 'nginx_reload'), patch.object(control, 'tcp', return_value=True), patch.object(control, 'healthy', return_value=True):
            control.nginx()
            status = control.status()
        self.assertIn('listen 8080 ssl;', path.read_text())
        self.assertEqual(status['front']['accessUrl'], 'https://www.example.test:8080/')
        self.assertIsNone(status['back']['accessUrl'])


class RealCertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.openssl = shutil.which('openssl')
        if not cls.openssl:
            raise unittest.SkipTest('OpenSSL is required for real certificate validation')
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        (cls.root / 'openssl.cnf').write_text('[req]\ndistinguished_name=dn\n[dn]\n')
        def run(*args):
            result = subprocess.run([cls.openssl, *args], cwd=cls.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            if result.returncode: raise RuntimeError('test certificate generation failed: ' + result.stderr.decode(errors='replace'))
        run('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-subj', '/CN=Test CA', '-days', '365', '-keyout', 'ca.key', '-out', 'ca.pem', '-config', 'openssl.cnf', '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-addext', 'subjectKeyIdentifier=hash')
        run('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-subj', '/CN=www.example.test', '-keyout', 'server.key', '-out', 'server.csr', '-config', 'openssl.cnf')
        (cls.root / 'extensions.cnf').write_text('subjectAltName=DNS:www.example.test\nbasicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
        run('x509', '-req', '-in', 'server.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-set_serial', '1', '-days', '1', '-out', 'server.pem', '-extfile', 'extensions.cnf')

    def validate(self, domain='www.example.test', certificate='server.pem', key='server.key', verification_time=None):
        def run(argv, timeout=40, check=True):
            if argv[1] == 'verify':
                argv = [*argv[:2], '-CAfile', str(self.root / 'ca.pem'), *(['-attime', str(verification_time)] if verification_time else []), *argv[2:]]
            result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
            if check and result.returncode: raise host.Rejected('OpenSSL test failed')
            return result
        with tempfile.TemporaryDirectory() as directory, patch.object(tls, 'OPENSSL', Path(self.openssl)), patch.object(tls, 'CERTIFICATES', Path(directory) / 'tls'), patch.object(host, 'trusted', side_effect=lambda p, *args: Path(p)), patch.object(host, 'run', side_effect=run):
            contents = [(self.root / certificate).read_bytes() + (self.root / 'ca.pem').read_bytes(), (self.root / key).read_bytes()]
            return tls.snapshot({'domain': domain, 'certificatePath': '/etc/acme/fullchain.pem', 'privateKeyPath': '/etc/acme/private.key'}, contents, host)

    def test_valid_chain_and_matching_key_produce_snapshot(self):
        result = self.validate()
        self.assertIsNotNone(result['expiresAt'])
        self.assertEqual(result['domain'], 'www.example.test')

    def test_wrong_domain_expired_certificate_and_mismatched_key_fail(self):
        expired_at = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp())
        for kwargs in [{'domain': 'other.example.test'}, {'verification_time': expired_at}, {'key': 'ca.key'}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(host.Rejected):
                self.validate(**kwargs)

    def test_local_https_health_checks_certificate_hostname_and_returns_status(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == '/health' and self.headers['Host'] == 'www.example.test' else 400)
                self.end_headers()
            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.root / 'server.pem'), str(self.root / 'server.key'))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            trusted_context = ssl.create_default_context(cafile=str(self.root / 'ca.pem'))
            control = types.SimpleNamespace(port=lambda side: server.server_port, target={'front': {'healthPath': '/health'}})
            with patch.object(tls.ssl, 'create_default_context', return_value=trusted_context):
                self.assertTrue(tls.healthy(control, {'domain': 'www.example.test'}, host))
        finally:
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()

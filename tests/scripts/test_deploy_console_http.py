"""Real Kestrel smoke test, isolated temporary state and loopback port.
Build DeployConsole.Api first. This test never calls a Linux service adapter.
"""
import http.cookiejar
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]


class HttpTests(unittest.TestCase):
    def test_auth_config_and_contract(self):
        with tempfile.TemporaryDirectory(prefix='deploy-console-http-') as temp:
            directory = Path(temp)
            password = directory / 'password.txt'
            password.write_text('admin\n')
            with socket.socket() as reserved:
                reserved.bind(('127.0.0.1', 0))
                port = reserved.getsockname()[1]
            base = 'http://127.0.0.1:' + str(port)
            api = ROOT / 'src/DeployConsole.Api'
            arguments = ['dotnet', str(api / 'bin/Debug/net10.0/DeployConsole.Api.dll'), '--urls', base, '--Console:AllowLoopbackHttp=true', '--Console:PasswordFile=' + str(password), '--Console:DataDirectory=' + str(directory / 'data'), '--Console:TargetsDirectory=' + str(directory / 'targets'), '--Console:HelperPath=' + str(directory / 'unavailable-helper')]
            process = subprocess.Popen(arguments, cwd=api, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.ProxyHandler({}))
                def request(path, method='GET', body=None, headers=None):
                    payload = json.dumps(body).encode() if body is not None else None
                    req = urllib.request.Request(base + path, data=payload, method=method, headers={'Content-Type': 'application/json', **(headers or {})})
                    try:
                        response = opener.open(req, timeout=5)
                    except urllib.error.HTTPError as error:
                        response = error
                    raw = response.read()
                    return response.status, json.loads(raw) if raw and response.headers.get('Content-Type', '').startswith('application/json') else raw, response.headers
                for _ in range(50):
                    try:
                        if request('/health/live')[0] == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(.2)
                self.assertEqual(request('/api/v1/projects')[0], 401)
                self.assertEqual(request('/api/v1/settings/mysql')[0], 401)
                self.assertEqual(request('/api/v1/settings/https')[0], 401)
                self.assertEqual(request('/api/v1/git/branches', 'POST', {'repository': 'https://github.com/example/repo.git', 'slug': 'project'})[0], 401)
                self.assertEqual(request('/api/v1/auth/login', 'POST', {'password': 'wrong'})[0], 401)
                status, session, headers = request('/api/v1/auth/login', 'POST', {'password': 'admin'})
                self.assertEqual(status, 200)
                self.assertIn('httponly', headers['Set-Cookie'].lower())
                self.assertIn('samesite=strict', headers['Set-Cookie'].lower())
                self.assertEqual(request('/api/v1/projects')[1], [])
                input_data = {'version': 0, 'slug': 'project', 'name': 'HTTP test', 'environment': 'test', 'repository': 'https://github.com/example/project.git', 'branch': 'main', 'frontPath': '/www/wwwroot/project/current', 'backPath': '/opt/project/current', 'repositoryPath': '/opt/project/repository'}
                self.assertEqual(request('/api/v1/projects', 'POST', input_data)[0], 403)
                self.assertEqual(request('/api/v1/projects/00000000-0000-0000-0000-000000000001/jobs', 'POST', {'version': 1, 'action': 'delete'})[0], 403)
                write_headers = {'X-CSRF-Token': session['csrfToken'], 'Idempotency-Key': str(uuid.uuid4())}
                https_input = {'domain': 'www.example.test', 'certificatePath': '/etc/acme/fullchain.pem', 'privateKeyPath': '/etc/acme/private.key'}
                self.assertEqual(request('/api/v1/settings/https', 'PUT', https_input)[0], 403)
                self.assertEqual(request('/api/v1/settings/https', 'PUT', {**https_input, 'domain': 'https://example.test'}, write_headers)[0], 400)
                self.assertEqual(request('/api/v1/settings/https', 'PUT', https_input, write_headers)[0], 409)
                mysql_input = {'username': 'root', 'port': 3306, 'password': 'TEST_ONLY_SECRET'}
                self.assertEqual(request('/api/v1/settings/mysql', 'PUT', mysql_input)[0], 403)
                self.assertEqual(request('/api/v1/settings/mysql', 'PUT', {**mysql_input, 'port': 0}, write_headers)[0], 400)
                mysql_code, mysql_result, _ = request('/api/v1/settings/mysql', 'PUT', mysql_input, write_headers)
                self.assertEqual(mysql_code, 409)
                self.assertNotIn('TEST_ONLY_SECRET', json.dumps(mysql_result))
                self.assertNotIn('TEST_ONLY_SECRET', (directory / 'data/state.json').read_text() if (directory / 'data/state.json').exists() else '')
                self.assertEqual(request('/api/v1/git/branches', 'POST', {'repository': 'https://github.com/example/repo.git', 'slug': 'project'})[0], 403)
                self.assertEqual(request('/api/v1/git/branches', 'POST', {'repository': 'file:///etc/passwd', 'slug': 'project'}, write_headers)[0], 400)
                self.assertEqual(request('/api/v1/git/branches', 'POST', {'repository': 'https://127.0.0.1/repo.git', 'slug': 'project'}, write_headers)[0], 400)
                code, project, _ = request('/api/v1/projects', 'POST', input_data, write_headers)
                self.assertEqual(code, 201)
                self.assertEqual(request('/api/v1/projects', 'POST', input_data, write_headers)[1]['id'], project['id'])
                self.assertEqual(request('/api/v1/projects', 'POST', {**input_data, 'name': 'different'}, write_headers)[0], 409)
                write_headers['Idempotency-Key'] = str(uuid.uuid4())
                route = '/api/v1/projects/' + project['id']
                self.assertEqual(request(route + '/deletion')[0], 409)
                no_backup = {'version': 1, 'action': 'delete', 'confirmationSlug': 'project', 'deletionFingerprint': 'a' * 64, 'backupBeforeDelete': False}
                code, problem, _ = request(route + '/jobs', 'POST', no_backup, write_headers)
                self.assertEqual(code, 400); self.assertIn('明确确认不备份', json.dumps(problem, ensure_ascii=False))
                self.assertEqual(request(route + '/jobs', 'POST', {**no_backup, 'confirmWithoutBackup': 'true'}, write_headers)[0], 400)
                self.assertEqual(request(route + '/jobs', 'POST', {'version': 1, 'action': 'delete', 'confirmationSlug': 'project', 'deletionFingerprint': 'bad'}, write_headers)[0], 400)
                code, project, _ = request(route + '/plans/front', 'PUT', {'version': 1, 'autoDeploy': False, 'periodSeconds': 3600}, write_headers)
                self.assertEqual(code, 200)
                self.assertEqual(project['front']['periodSeconds'], 3600)
                self.assertEqual(project['back']['periodSeconds'], 60)
                write_headers['Idempotency-Key'] = str(uuid.uuid4())
                self.assertEqual(request(route, 'PUT', {**input_data, 'version': 1}, write_headers)[0], 409)
                self.assertEqual(request(route + '/status')[1]['ok'], False)
                self.assertEqual(request(route + '/jobs', 'POST', {'version': project['version'], 'action': 'stop-all'}, write_headers)[0], 409)
                self.assertEqual(request('/api/v1/not-found')[0], 404)
                self.assertEqual(request('/openapi/v1.json')[0], 200)
                auto = {**input_data, 'slug': 'automatic', 'frontPath': '/www/wwwroot/automatic/current', 'backPath': '/opt/automatic/current', 'repositoryPath': '/opt/automatic/repository', 'deployAfterSave': True, 'enableSchedule': True}
                write_headers['Idempotency-Key'] = str(uuid.uuid4())
                code, saved, _ = request('/api/v1/projects', 'POST', auto, write_headers)
                self.assertEqual(code, 201)
                self.assertEqual(request('/api/v1/projects', 'POST', auto, write_headers)[1]['id'], saved['id'])
                for _ in range(40):
                    result = request('/api/v1/jobs?projectId=' + saved['id'])[1]
                    if result['items'][0]['state'] == 'failed':
                        break
                    time.sleep(.1)
                self.assertEqual(result['total'], 1)
                self.assertEqual(result['items'][0]['action'], 'prepare')
                self.assertEqual(result['items'][0]['state'], 'failed')
                self.assertFalse(result['items'][0]['result']['ok'])
                self.assertEqual(len(request('/api/v1/projects')[1]), 2)
                password.write_text('changed-for-test\n')
                self.assertEqual(request('/api/v1/projects')[0], 401)
                password.unlink()
                self.assertEqual(request('/api/v1/auth/login', 'POST', {'password': 'admin'})[0], 503)
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == '__main__':
    unittest.main()

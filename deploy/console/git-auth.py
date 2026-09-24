#!/usr/bin/python3 -I
"""Inject a short-lived systemd credential into Git's process environment only."""
import base64
import json
import os
from pathlib import Path
import sys


def environment(credential, original):
    result = {key: value for key, value in original.items() if not key.startswith('GIT_CONFIG_')}
    result.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL='https')
    result['GIT_CONFIG_COUNT'] = '1'
    result['GIT_CONFIG_KEY_0'] = 'http.' + credential['repository'] + '.extraHeader'
    result['GIT_CONFIG_VALUE_0'] = 'Authorization: Basic ' + base64.b64encode((credential['username'] + ':' + credential['secret']).encode()).decode('ascii')
    return result


if __name__ == '__main__':
    try:
        credential = json.loads((Path(os.environ['CREDENTIALS_DIRECTORY']) / 'git-auth').read_text())
        os.execve('/usr/bin/git', ['/usr/bin/git', *sys.argv[1:]], environment(credential, os.environ))
    except Exception:
        # Never expose the credential, Git command, or raw exception in output.
        sys.exit(1)

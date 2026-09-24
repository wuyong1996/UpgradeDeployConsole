#!/usr/bin/python3
"""Pass project-scoped migration secrets from a systemd credential to the child."""
import json
import os
from pathlib import Path
import re
import sys

if __name__ == '__main__':
    try:
        directory = os.environ['CREDENTIALS_DIRECTORY']
        values = json.loads((Path(directory) / 'database').read_text())
        if not isinstance(values, dict) or not all(re.fullmatch(r'(ConnectionStrings|DeploymentDatabase|DeploymentBootstrap)__[A-Za-z][A-Za-z0-9_]*', key)
                                                  and isinstance(value, str) and '\x00' not in value for key, value in values.items()):
            raise ValueError()
        if len(sys.argv) < 3 or sys.argv[1] != '/usr/bin/dotnet':
            raise ValueError()
        os.execve(sys.argv[1], sys.argv[1:], {**os.environ, **values})
    except Exception:
        print('{"status":"conflict","message":"数据库迁移凭据不可用"}', flush=True)
        sys.exit(1)

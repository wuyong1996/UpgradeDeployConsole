#!/usr/bin/env bash
set -Eeuo pipefail
umask 027
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $# -eq 1 ]] || { echo 'Usage: install.sh /absolute/published/console' >&2; exit 1; }
package=$(realpath -- "$1")
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$package/DeployConsole.Api.dll" && -f "$package/wwwroot/index.html" ]] || { echo 'Publish the API and build the frontend first.' >&2; exit 1; }
for program in /usr/bin/dotnet /usr/bin/python3 /usr/bin/sudo /usr/bin/systemd-run /usr/bin/git /usr/bin/openssl; do
  [[ -x "$program" ]] || { echo "Missing dependency: $program" >&2; exit 1; }
done
/usr/bin/dotnet --list-runtimes | grep -Eq '^Microsoft.AspNetCore.App 10\.' || { echo 'Install ASP.NET Core Runtime 10 before continuing.' >&2; exit 1; }
id deploy-console >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/deploy-console --shell /usr/sbin/nologin deploy-console
install -d -o root -g deploy-console -m 0750 /etc/deploy-console /etc/deploy-console/targets
install -d -o deploy-console -g deploy-console -m 0700 /var/lib/deploy-console
install -d -o root -g root -m 0700 /var/lib/deploy-console-host
install -d -o root -g root -m 0755 /usr/local/lib/deploy-console /opt/deploy-console /opt/deploy-console/releases
if [[ ! -e /etc/deploy-console/password.txt && ! -L /etc/deploy-console/password.txt ]]; then
  printf '%s\n' admin > /etc/deploy-console/password.txt
  chown root:deploy-console /etc/deploy-console/password.txt
  chmod 0640 /etc/deploy-console/password.txt
fi
# Refuse unsafe existing password permissions; never replace its contents.
/usr/bin/python3 -I - <<'PY'
import os, stat
p = '/etc/deploy-console/password.txt'
s = os.lstat(p)
assert stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o027, 'Unsafe password file'
PY
release="/opt/deploy-console/releases/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -o root -g root -m 0755 "$release"
cp -a -- "$package/." "$release/"
chown -R root:root "$release"
# Uploaded packages may originate from a restrictive umask (0700/0600).
# The unprivileged console account must be able to traverse and read the package.
# Runtime secrets live outside the release, under /etc/deploy-console.
find "$release" -type d -exec chmod 0755 {} +
find "$release" -type f -exec chmod 0644 {} +
install -o root -g root -m 0755 "$source_dir/host.py" /usr/local/lib/deploy-console/host.py
install -o root -g root -m 0755 "$source_dir/git-auth.py" /usr/local/lib/deploy-console/git-auth.py
install -o root -g root -m 0755 "$source_dir/provision.py" /usr/local/lib/deploy-console/provision.py
install -o root -g root -m 0755 "$source_dir/database.py" /usr/local/lib/deploy-console/database.py
install -o root -g root -m 0755 "$source_dir/database-run.py" /usr/local/lib/deploy-console/database-run.py
install -o root -g root -m 0755 "$source_dir/tls.py" /usr/local/lib/deploy-console/tls.py
install -o root -g root -m 0755 "$source_dir/deletion.py" /usr/local/lib/deploy-console/deletion.py
if [[ ! -e /etc/deploy-console/auto-deploy.json && ! -L /etc/deploy-console/auto-deploy.json ]]; then
  install -o root -g root -m 0600 "$source_dir/auto-deploy.example.json" /etc/deploy-console/auto-deploy.json
fi
install -o root -g root -m 0644 "$source_dir/deploy-console.service" /etc/systemd/system/deploy-console.service
install -o root -g root -m 0644 "$source_dir/deploy-console-https.service" /etc/systemd/system/deploy-console-https.service
install -o root -g root -m 0644 "$source_dir/deploy-console-https.timer" /etc/systemd/system/deploy-console-https.timer
sudoers_tmp=$(mktemp)
trap 'rm -f -- "$sudoers_tmp"' EXIT
printf '%s\n' 'deploy-console ALL=(root) NOPASSWD: /usr/local/lib/deploy-console/host.py ""' > "$sudoers_tmp"
visudo -cf "$sudoers_tmp"
install -o root -g root -m 0440 "$sudoers_tmp" /etc/sudoers.d/deploy-console
if [[ -e /opt/deploy-console/current && ! -L /opt/deploy-console/current ]]; then echo 'Existing current is not a symbolic link.' >&2; exit 1; fi
ln -s -- "$release" /opt/deploy-console/current.next
mv -Tf -- /opt/deploy-console/current.next /opt/deploy-console/current
systemd-analyze verify /etc/systemd/system/deploy-console.service
systemd-analyze verify /etc/systemd/system/deploy-console-https.service /etc/systemd/system/deploy-console-https.timer
systemctl daemon-reload
systemctl enable deploy-console.service
systemctl restart deploy-console.service
systemctl enable --now deploy-console-https.timer
echo 'Console installed on loopback port 5088. Configure a separate HTTPS proxy using nginx.example.conf.'
echo 'Existing password and project state were preserved.'

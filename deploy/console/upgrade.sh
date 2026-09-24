#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $# -le 1 ]] || { echo 'Usage: upgrade.sh [/root/deploy-console-server-VERSION.tar.gz]' >&2; exit 1; }
archive=${1:-/root/deploy-console-server-20260924-delete-options.tar.gz}
[[ "$archive" =~ ^/root/deploy-console-server-([A-Za-z0-9-]+)\.tar\.gz$ ]] || { echo '请使用/root下的完整升级包路径。' >&2; exit 1; }
version=${BASH_REMATCH[1]}
payload="deploy-console-upload-$version"
legacy_prefix=upgrade
case "$version" in
  *-delete|*-delete-legacy) legacy_prefix=delete;;
  *-https|*-https-acme) legacy_prefix=https;;
  *-ports) legacy_prefix=ports;;
  *-start-all) legacy_prefix=start-all;;
esac
upgrade_dir=''
if [[ -f "$archive" && ! -L "$archive" ]]; then
  upgrade_dir=$(mktemp -d /root/deploy-console-upgrade.XXXXXX)
  tar -xzf "$archive" -C "$upgrade_dir"
  bash "$upgrade_dir/$payload/console/install.sh" "$upgrade_dir/$payload/published"
elif [[ -e "$archive" || -L "$archive" ]]; then
  echo '升级包不是普通文件，未安装或清理。' >&2
  exit 1
else
  printf '未找到升级包，跳过安装：%s\n' "$archive"
fi

# A missing archive is not an installation failure. Only a healthy installed copy
# can justify removing old extracted copies; never remove an uninstalled payload.
curl --fail --connect-timeout 2 --max-time 5 --retry 8 --retry-connrefused --retry-delay 1 \
  http://127.0.0.1:5088/health/live | \
  python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("status") == "healthy" else 1)'
shopt -s nullglob dotglob
candidates=(/root/deploy-console-upgrade.?????? "/root/$payload")
if [[ "$legacy_prefix" != upgrade ]]; then
  candidates+=(/root/deploy-console-"$legacy_prefix".??????)
fi
for directory in "${candidates[@]}"; do
  [[ -e "$directory" || -L "$directory" ]] || continue
  if [[ ! -d "$directory" || -L "$directory" || "$(realpath -e -- "$directory")" != "$directory" ]]; then
    printf '跳过未确认的路径：%s\n' "$directory"
    continue
  fi
  if [[ "$directory" == "/root/$payload" ]]; then
    source_dir=$directory
  else
    entries=("$directory"/*)
    if [[ ${#entries[@]} -ne 1 || "${entries[0]}" != "$directory/$payload" ]]; then
      printf '保留含其他文件或其他版本的目录：%s\n' "$directory"
      continue
    fi
    source_dir="$directory/$payload"
  fi
  if [[ ! -d "$source_dir" || -L "$source_dir" || "$(realpath -e -- "$source_dir")" != "$source_dir" || ! -f "$source_dir/console/install.sh" ]]; then
    printf '保留无法识别的解压目录：%s\n' "$directory"
    continue
  fi
  matches=true
  for file in DeployConsole.Api.dll wwwroot/index.html; do
    cmp -s -- "$source_dir/published/$file" "/opt/deploy-console/current/$file" || matches=false
  done
  for file in host.py git-auth.py provision.py database.py database-run.py tls.py deletion.py; do
    if [[ -f "$source_dir/console/$file" ]]; then
      cmp -s -- "$source_dir/console/$file" "/usr/local/lib/deploy-console/$file" || matches=false
    fi
  done
  if [[ "$matches" != true ]]; then
    printf '保留与当前安装内容不同的解压目录：%s\n' "$directory"
    continue
  fi
  if ! python3 -c 'import pathlib,re,sys
p=pathlib.Path(sys.argv[1]); info=pathlib.Path("/proc/self/mountinfo")
mounts=[pathlib.Path(re.sub(r"\\([0-7]{3})",lambda m:chr(int(m[1],8)),line.split()[4])) for line in info.read_text().splitlines()] if info.is_file() else []
sys.exit(0 if info.is_file() and not any(m==p or p in m.parents for m in mounts) else 1)' "$directory"; then
    printf '保留包含挂载点或无法核对挂载关系的目录：%s\n' "$directory"
    continue
  fi
  rm -rf --one-file-system -- "$directory"
  printf '已清理解压目录：%s\n' "$directory"
  if [[ "$directory" == "$upgrade_dir" ]]; then upgrade_dir=''; fi
done
if [[ -n "$upgrade_dir" ]]; then
  echo '本次解压内容未能与当前安装内容对应，保留升级包及目录，请核对安装结果。' >&2
  exit 1
fi
# The optional legacy checksum file is only unlinked, never read or required.
rm -f -- "$archive" "$archive.sha256"
printf '%s\n' '面板健康，本次升级包及已核对的同版解压目录已清理；不存在的文件已跳过。'

#!/usr/bin/env bash
set -Eeuo pipefail

release_sha="${1:?Expected the Git commit SHA}"
if [[ ! "$release_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid release SHA" >&2
    exit 2
fi

root=/opt/meeting-analysis
releases="$root/releases"
venv="$root/venv"
archive="/tmp/meeting-analysis-${release_sha}.tar.gz"
release="$releases/$release_sha"
staging="$releases/.${release_sha}.staging.$$"

if [[ ! -r "$archive" ]]; then
    echo "Release archive is missing" >&2
    exit 1
fi
if [[ ! -x "$venv/bin/python" ]]; then
    echo "Python 3.12 virtual environment is not prepared" >&2
    exit 1
fi

database=/var/lib/meeting-analysis/meetings.sqlite3
if [[ -f "$database" ]]; then
active_count="$("$venv/bin/python" - "$database" <<'PY'
import sqlite3
import sys

database_uri = f"file:{sys.argv[1]}?mode=ro"
with sqlite3.connect(database_uri, uri=True, timeout=5) as connection:
    count = connection.execute(
        "SELECT count(*) FROM meetings WHERE status LIKE 'processing_%'"
    ).fetchone()[0]
print(count)
PY
)"
    if [[ "$active_count" != 0 ]]; then
        echo "Deployment postponed: active meeting processing is in progress" >&2
        exit 75
    fi
fi

mkdir -p "$releases"
if [[ ! -d "$release" ]]; then
    mkdir -m 0750 "$staging"
    tar -xzf "$archive" --strip-components=1 -C "$staging"
    if [[ ! -f "$staging/app/main.py" || ! -f "$staging/requirements.lock" ]]; then
        echo "Release archive is incomplete" >&2
        exit 1
    fi
    chgrp -R meeting-analysis "$staging"
    "$venv/bin/python" -m pip install --disable-pip-version-check --requirement "$staging/requirements.lock"
    if [[ ! -x "$venv/bin/uvicorn" ]]; then
        echo "Application dependencies were not installed" >&2
        exit 1
    fi
    mv "$staging" "$release"
fi

previous="$(readlink -f "$root/current" 2>/dev/null || true)"
new_link="$root/.current-${release_sha}"
ln -sfn "$release" "$new_link"
mv -Tf "$new_link" "$root/current"

if ! sudo -n /usr/bin/systemctl restart meeting-analysis; then
    if [[ -n "$previous" ]]; then
        ln -sfn "$previous" "$new_link"
        mv -Tf "$new_link" "$root/current"
        sudo -n /usr/bin/systemctl restart meeting-analysis || true
    fi
    exit 1
fi

healthy=false
for attempt in {1..15}; do
    if "$venv/bin/python" -c "from urllib.request import urlopen; response = urlopen('http://127.0.0.1:8000/health', timeout=2); raise SystemExit(0 if response.status == 200 else 1)"; then
        healthy=true
        break
    fi
    sleep 2
done

if [[ "$healthy" != true ]]; then
    echo "Health check failed after restart" >&2
    if [[ -n "$previous" ]]; then
        ln -sfn "$previous" "$new_link"
        mv -Tf "$new_link" "$root/current"
        sudo -n /usr/bin/systemctl restart meeting-analysis || true
    fi
    exit 1
fi

rm -f "$archive"
echo "Deployed $release_sha"

#!/usr/bin/env bash
# Isolated PostgreSQL bootstrap and custom-format restore for fictional demo fixtures.
set -euo pipefail
umask 077
unset PGHOST PGHOSTADDR PGPORT PGUSER PGPASSWORD PGSERVICE PGDATABASE PGOPTIONS PGSSLMODE
action=${1:?setup, restore or stop}
evidence=${2:?path to a dedicated local evidence directory}
mkdir -p "$evidence"
evidence=$(realpath "$evidence")

if [[ "$action" == "setup" ]]; then
  [[ ! -e "$evidence/cluster" && ! -e "$evidence/.env.rehearsal" ]]
  work=$(mktemp -d /tmp/supportlens-demo.XXXXXXXX)
  port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
  mkdir "$work/socket"
  printf '%s\n' "$work" > "$evidence/cluster"
  printf '%s\n' "$port" > "$evidence/port"
  share=()
  if [[ -n "${PG_SHARE_DIR:-}" ]]; then share=(-L "$PG_SHARE_DIR"); fi
  initdb --pgdata="$work/data" --username=fixture_admin --auth-local=trust --auth-host=scram-sha-256 "${share[@]}" >/dev/null
  openssl req -new -x509 -newkey rsa:2048 -nodes -days 2 -subj '/CN=SupportLens rehearsal' \
    -addext 'subjectAltName=IP:127.0.0.1' -keyout "$work/data/server.key" -out "$work/data/server.crt" 2>/dev/null
  chmod 600 "$work/data/server.key"
  pg_ctl -D "$work/data" -l "$work/server.log" -w start -o "-p $port -h 127.0.0.1 -k $work/socket -c ssl=on -c jit=off" >/dev/null
  runtime_pass=$(openssl rand -hex 24)
  owner_pass=$(openssl rand -hex 24)
  psql -Xq --set=ON_ERROR_STOP=1 -h "$work/socket" -p "$port" -U fixture_admin -d postgres <<SQL
CREATE ROLE supportlens_demo_app LOGIN PASSWORD '$runtime_pass';
CREATE ROLE supportlens_demo_migrate LOGIN PASSWORD '$owner_pass';
CREATE DATABASE "SupportLens_demo" OWNER supportlens_demo_migrate TEMPLATE template0;
SQL
  cp "$work/data/server.crt" "$evidence/rehearsal-ca.pem"
  {
    printf 'DEMO_DATABASE_URL=postgresql://supportlens_demo_app:%s@127.0.0.1:%s/SupportLens_demo\n' "$runtime_pass" "$port"
    printf 'DEMO_MIGRATION_DATABASE_URL=postgresql://supportlens_demo_migrate:%s@127.0.0.1:%s/SupportLens_demo\n' "$owner_pass" "$port"
    printf 'DEMO_RESTORE_DATABASE_URL=postgresql://supportlens_demo_migrate:%s@127.0.0.1:%s/SupportLens_demo_restore\n' "$owner_pass" "$port"
  } > "$evidence/.env.rehearsal"
  echo 'Isolated PostgreSQL cluster started on loopback with TLS.'
  exit
fi

work=$(<"$evidence/cluster")
port=$(<"$evidence/port")
[[ "$work" =~ ^/tmp/supportlens-demo\.[[:alnum:]]{8}$ && -f "$work/data/PG_VERSION" && "$port" =~ ^[0-9]{4,5}$ ]]

case "$action" in
  restore)
    [[ ! -e "$evidence/fictional-demo.dump" ]]
    psql -Xq --set=ON_ERROR_STOP=1 -h "$work/socket" -p "$port" -U fixture_admin -d postgres \
      -c 'CREATE DATABASE "SupportLens_demo_restore" OWNER supportlens_demo_migrate TEMPLATE template0'
    pg_dump -h "$work/socket" -p "$port" -U fixture_admin --format=custom \
      --no-owner --no-privileges -f "$evidence/fictional-demo.dump" SupportLens_demo
    pg_restore -h "$work/socket" -p "$port" -U fixture_admin --role=supportlens_demo_migrate --single-transaction \
      --exit-on-error --no-owner --no-privileges -d SupportLens_demo_restore "$evidence/fictional-demo.dump"
    echo 'Custom-format backup restored to the separate rehearsal database.'
    ;;
  stop)
    pg_ctl -D "$work/data" -m fast -w stop >/dev/null
    ;;
  *)
    exit 2
    ;;
esac

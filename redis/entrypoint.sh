#!/bin/sh
set -eu

ACL_FILE=/data/users.acl
REDIS_PASSWORD=${REDIS_PASSWORD:-}

if [ -z "$REDIS_PASSWORD" ]; then
  echo "user default on nopass ~* &* +@all" > "$ACL_FILE"
else
  echo "user default off" > "$ACL_FILE"
  echo "user proxy on >${REDIS_PASSWORD} ~capabilities:* ~pending:* ~telegram:last_update_id ~ratelimit:* &* +ping +select +client +get +set +exists +scan +incr +expire +ttl +del +publish +subscribe" >> "$ACL_FILE"
fi

for env_name in $(env | cut -d= -f1 | grep '^REDIS_USER_.*_PASSWORD$' || true); do
  user=$(printf '%s' "$env_name" | sed 's/^REDIS_USER_//; s/_PASSWORD$//' | tr 'A-Z' 'a-z')
  case "$user" in
    *[!a-z0-9_]*|'') echo "skipping invalid ACL user env $env_name" >&2; continue ;;
  esac
  upper=$(printf '%s' "$user" | tr 'a-z' 'A-Z')
  id=$(printenv "REDIS_USER_${upper}_ID" || printf '%s' "$user")
  case "$id" in
    *[!a-z0-9_-]*|'') echo "invalid service id '$id'" >&2; exit 1 ;;
  esac
  pass=$(printenv "$env_name")
  if [ -z "$pass" ]; then
    if [ -n "$REDIS_PASSWORD" ]; then
      echo "REDIS_USER_${upper}_PASSWORD is required when REDIS_PASSWORD is set" >&2
      exit 1
    fi
    pass_rule=nopass
  else
    pass_rule=">${pass}"
  fi
  echo "user ${user} on ${pass_rule} ~capabilities:${id} &cmd:${id} &telegram:outgoing &capabilities:changed +ping +select +client +get +set +del +publish +subscribe" >> "$ACL_FILE"
done

exec redis-server --notify-keyspace-events Ex --aclfile "$ACL_FILE"
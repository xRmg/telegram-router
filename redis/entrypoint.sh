#!/bin/sh
set -eu

ACL_FILE=/data/users.acl
REDIS_PASSWORD=${REDIS_PASSWORD:-}

if [ -z "$REDIS_PASSWORD" ]; then
  echo "user default on nopass ~* &* +@all" > "$ACL_FILE"
else
  echo "user default on >${REDIS_PASSWORD} ~* &* +@all" > "$ACL_FILE"
fi

exec redis-server --notify-keyspace-events Ex --aclfile "$ACL_FILE"
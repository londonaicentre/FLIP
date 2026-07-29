#!/bin/sh
# wait-for-postgres.sh


set -e

cmd="$@"

>&2 echo "⏳ Waiting for Postgres to be available on port $1..."
until pg_isready -h localhost -p $1; do
  >&2 echo "."
  sleep 1
done

>&2 echo "✅ Postgres is up - executing command "

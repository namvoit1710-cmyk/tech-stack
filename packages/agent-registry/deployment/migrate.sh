#!/bin/sh
set -eu

echo "Running Alembic migrations..."

alembic upgrade head

if [ $? -eq 0 ]; then
  echo "Alembic migrations completed successfully."
else
  echo "Alembic migrations failed. Check the logs."
  exit 1
fi
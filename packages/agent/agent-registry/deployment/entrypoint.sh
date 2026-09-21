#!/bin/sh
set -eu

/app/deployment/migrate.sh

exec uvicorn main:app --host 0.0.0.0 --port 8000
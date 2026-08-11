#!/usr/bin/env bash
set -e
rm -f jobs.db jobs.db-wal jobs.db-shm
sqlite3 jobs.db < schema.sql
echo "Database rebuilt."

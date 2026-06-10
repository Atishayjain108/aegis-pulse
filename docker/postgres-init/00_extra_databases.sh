#!/bin/bash
# Creates extra databases needed by optional services.
# This script runs automatically on first postgres initialization (empty data volume).
# For existing instances, run: docker exec aegis-postgres psql -U aegis_app -d aegis -c "CREATE DATABASE langfuse OWNER aegis_app;"
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE langfuse OWNER $POSTGRES_USER'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec
EOSQL

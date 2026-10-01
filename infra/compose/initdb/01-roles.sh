#!/bin/sh
# Cria os roles da aplicação (ver docs/specs/security.md). Roda apenas na primeira
# inicialização do volume; também é montado pelos testes (Testcontainers).
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_pw="$REGISTA_OWNER_PASSWORD" -v app_pw="$REGISTA_APP_PASSWORD" \
  -v dbname="$POSTGRES_DB" <<'SQL'
CREATE ROLE regista_owner LOGIN PASSWORD :'owner_pw'
  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE regista_app LOGIN PASSWORD :'app_pw'
  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

ALTER DATABASE :"dbname" OWNER TO regista_owner;
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO regista_app;

ALTER SCHEMA public OWNER TO regista_owner;
GRANT USAGE ON SCHEMA public TO regista_app;
SQL

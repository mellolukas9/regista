-- Cria o banco do Prefect separado do banco da aplicação
SELECT 'CREATE DATABASE prefect OWNER ' || current_user
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'prefect')\gexec

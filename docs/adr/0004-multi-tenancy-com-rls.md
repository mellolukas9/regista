# ADR 0004: Isolamento entre clientes com Row-Level Security

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O piloto filtrava `client_id` manualmente em cada query e teve vazamentos (runs, logs e agendamentos de outros clientes acessíveis). Isolamento é a regra central do produto.

## Decisão

Banco único com `tenant_id` em toda tabela de cliente e políticas RLS com `FORCE ROW LEVEL SECURITY`. A aplicação usa role sem `BYPASSRLS`; cada transação define `app.tenant_id` com `set_config(..., true)`. Administradores da plataforma têm leitura entre tenants por `app.platform_admin`; escrita sempre no tenant de contexto. Detalhes em `docs/specs/security.md`.

## Consequências

Uma query sem filtro não vaza dados. Exige disciplina de abrir transação com tenant em todo acesso e testes de isolamento por rota.

## Alternativas descartadas

Banco por cliente (operação cara); apenas filtros na aplicação (já falhou no piloto).

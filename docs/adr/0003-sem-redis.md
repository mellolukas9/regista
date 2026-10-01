# ADR 0003: Sem Redis

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O Redis estava provisionado no piloto sem nenhum consumidor. Com Postgres como fila e tarefas internas via Procrastinate, ele não tem função.

## Decisão

Não usar Redis. Rate limit, tarefas internas e notificações usam Postgres ou memória do processo.

## Consequências

Menos infraestrutura para operar e proteger. Se surgir necessidade real (cache de alto volume), reavaliar com nova ADR.

## Alternativas descartadas

Manter Redis "para o futuro".

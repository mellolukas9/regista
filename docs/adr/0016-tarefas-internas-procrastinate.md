# ADR 0016: Tarefas internas com Procrastinate

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Alertas, itens abandonados, máquinas offline, consolidação e retenção precisam rodar em segundo plano, com retry, sem adicionar broker.

## Decisão

Usar Procrastinate (fila de tarefas Python sobre Postgres) para tarefas internas do backend. Não usar para distribuir trabalho aos agentes (isso é a tabela `jobs`).

## Consequências

Uma dependência a mais, mas sem infraestrutura nova.

## Alternativas descartadas

Celery/RQ/ARQ (exigem Redis ou RabbitMQ); loops ad hoc em `asyncio` (sem retry nem visibilidade).

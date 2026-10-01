# ADR 0002: Remover o Prefect; PostgreSQL como fila e fonte de verdade

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O piloto usava Prefect OSS para disparo, estado e logs, com um loop de polling sincronizando o estado com o banco da aplicação. O Prefect OSS não tem isolamento por cliente (apenas autenticação básica com uma credencial compartilhada), o que exigiria expor o servidor às máquinas dos clientes com acesso total. Com agente próprio e filas de itens, quase todas as funções do Prefect passam a ser do Regista.

## Decisão

Remover o Prefect. Jobs, itens, lotes, agendamentos e logs ficam no PostgreSQL, com retirada atômica via `FOR UPDATE SKIP LOCKED` e notificação via `LISTEN/NOTIFY`.

## Consequências

Uma única fonte de verdade, status em tempo real, sem sincronização entre sistemas. É preciso implementar a máquina de estados de jobs e itens, retries e agendamento.

## Alternativas descartadas

Manter Prefect atrás de proxy filtrando por fila (frágil); um servidor Prefect por cliente (operação cara); Prefect Cloud (pago e não self-hosted); Temporal (pesado para o caso); Celery/RQ (exigem expor o broker).

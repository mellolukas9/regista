# ADR 0015: Agendador próprio com croniter e advisory lock

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Com a saída do Prefect, o motor de agendamento (inexistente no piloto) precisa ser construído.

## Decisão

Laço no backend calcula `next_fire_at` com `croniter` no fuso do agendamento e cria jobs; `pg_try_advisory_lock` garante que só uma instância dispare. Disparos perdidos são executados uma vez ao voltar.

## Consequências

Simples, transacional e sem dependência externa.

## Alternativas descartadas

APScheduler com job store; pg_cron (agendamento dentro do banco, menos visível para a aplicação).

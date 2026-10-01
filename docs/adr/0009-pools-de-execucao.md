# ADR 0009: Pools de execução em vez de máquina fixa

- **Status:** aceita
- **Data:** 2026-10

## Contexto

No piloto cada bot apontava para uma máquina e fila específicas. Mover execução (máquina do cliente → VM → nuvem) exigiria mudar o bot.

## Decisão

Bots apontam para um pool; um pool contém uma ou mais máquinas. Tipos: `on_prem`, `client_cloud`, `internal`.

## Consequências

Trocar onde um robô roda é configuração. Permite várias máquinas consumindo a mesma fila.

## Alternativas descartadas

Manter `machine_id` no bot.

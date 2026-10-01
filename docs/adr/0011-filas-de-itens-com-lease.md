# ADR 0011: Filas de itens com trava com prazo

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Processos de RPA tratam itens de negócio (linhas, processos). É preciso saber o resultado de cada item, tentar de novo de forma inteligente e não perder itens quando uma máquina cai, inclusive com robôs async processando vários itens em paralelo.

## Decisão

Tabela `queue_items` com retirada atômica (`SKIP LOCKED`), trava com prazo renovada pelo SDK, distinção entre falha de negócio (final) e de aplicação (retry com espera crescente) e retorno automático de itens abandonados. Logs por item via `contextvars`.

## Consequências

Rastreio linha a linha e reprocessamento só das falhas. Exige idempotência nos robôs quando repetir for perigoso.

## Alternativas descartadas

Tratar o job inteiro como unidade (sem visibilidade por item).

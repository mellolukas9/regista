# ADR 0011: Filas de itens com trava com prazo

- **Status:** aceita
- **Data:** 2026-10 (revisada em 2026-10-02 com o handoff de interface)

## Contexto

Processos de RPA tratam itens de negócio (linhas, processos). É preciso saber o resultado de cada item, tentar de novo de forma inteligente e não perder itens quando uma máquina cai, inclusive com robôs async processando vários itens em paralelo.

## Decisão

Tabela `queue_items` com retirada atômica (`SKIP LOCKED`) e trava com prazo renovada pelo SDK. Falha de aplicação gera nova tentativa automática na mesma execução até `max_attempts`; falha de negócio é final. Item que ainda está em andamento quando a execução termina (ou cuja trava vence) vira `abandoned` e **só volta à fila por "Reprocessar"**, que mantém o histórico de tentativas (`item_attempts`). Logs por item via `contextvars`.

## Consequências

Rastreio linha a linha, histórico completo de tentativas e reprocessamento só das falhas. Itens abandonados exigem ação humana (decisão de produto do handoff de interface, para evitar repetir ações em sistemas de destino sem alguém conferir). Exige idempotência nos robôs quando repetir for perigoso.

## Alternativas descartadas

Tratar o job inteiro como unidade (sem visibilidade por item).

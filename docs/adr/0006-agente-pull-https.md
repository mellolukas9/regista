# ADR 0006: Agente pull-only por HTTPS com long-polling

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O agente roda em redes de clientes, muitas vezes atrás de firewall e proxy corporativo. Abrir portas de entrada é inaceitável.

## Decisão

O agente só inicia conexões de saída HTTPS (443) para a API. Recebe trabalho por long-polling (`GET /agent/jobs/next?wait=30`), acordado no servidor por `LISTEN/NOTIFY`.

## Consequências

Funciona através de proxies; nenhuma regra de firewall de entrada; latência abaixo de um segundo. WebSocket/SSE podem ser adicionados depois para streaming, se necessário.

## Alternativas descartadas

WebSocket persistente (mais frágil em proxies); gRPC (bloqueado em muitas redes); push com porta aberta no cliente.

# ADR 0012: Lotes com reconciliação

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O cliente que envia uma planilha precisa saber exatamente o que aconteceu com cada linha, inclusive as que nem entraram na fila.

## Decisão

Cada fonte vira um `batch`. O dispatcher registra linhas lidas, enfileiradas e rejeitadas (com motivo); o lote só fecha se `lidas = enfileiradas + rejeitadas`. Relatório por referência estável; devolução do resultado na planilha ocorre no ambiente do cliente ao fim do lote.

## Consequências

Nenhuma linha some sem explicação; relatório funciona mesmo sem o Regista ver dados sensíveis.

## Alternativas descartadas

Relatório apenas dos itens enfileirados.

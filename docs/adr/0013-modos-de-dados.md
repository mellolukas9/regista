# ADR 0013: Modos de dados por fila

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Itens podem conter dados pessoais (CPF, processos). A LGPD e clientes do nicho jurídico pedem minimização.

## Decisão

Separar controle (sempre no Regista) de conteúdo. Modo por fila: `reference` (padrão; só ponteiro), `central` (criptografado com chave por tenant), `e2e` (reservado, fora do MVP). Mascaramento de CPF/CNPJ/e-mail no SDK, campos visíveis por fila e retenção configurável.

## Consequências

O painel pode não mostrar o conteúdo em filas sensíveis; o relatório completo é gerado no ambiente do cliente.

## Alternativas descartadas

Todo conteúdo central em texto puro; todo conteúdo sempre fora do Regista (perde praticidade para clientes simples).

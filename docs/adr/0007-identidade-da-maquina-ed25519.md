# ADR 0007: Identidade da máquina por par de chaves Ed25519

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Era desejado um cadastro por "hash key" visível no painel. Uma chave reutilizável guardada em texto no banco ou na máquina é fácil de vazar.

## Decisão

Chave de registro de uso único (exibida uma vez, hash no banco, expira em 24h) usada apenas para cadastrar a chave pública de um par Ed25519 gerado na máquina. Depois, desafio assinado → token de acesso de 15 minutos.

## Consequências

Vazamento do banco não permite se passar por máquinas. Revogação imediata. VMs devem ser cadastradas depois de clonadas.

## Alternativas descartadas

Chave fixa por máquina (API key); mTLS com certificados (mais complexo de distribuir; pode evoluir para isso).

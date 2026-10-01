# ADR 0001: Monorepo e convenções

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O piloto tinha backend, frontend e scripts de worker sem fronteiras claras nem tooling comum. A v2 terá API, painel, agente, SDK e robôs que evoluem juntos.

## Decisão

Monorepo único `regista` com `apps/api`, `apps/web`, `agent`, `sdk`, `bots`, `infra`, `docs`. Workspace `uv` na raiz para os pacotes Python; npm em `apps/web`. Código e commits em inglês, documentação e interface em pt-BR, Conventional Commits, uma branch por marco. Ambiente de desenvolvimento Windows: comandos precisam funcionar no PowerShell.

## Consequências

Uma mudança de protocolo entre API, SDK e agente acontece num único PR. CI precisa rodar Python e Node.

## Alternativas descartadas

Repositórios separados por componente (mais atrito de versionamento entre API, SDK e agente).

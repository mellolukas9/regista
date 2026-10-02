# Regista

Orquestração de robôs Python + Playwright que rodam no ambiente do cliente, com filas de itens, rastreio linha a linha, lotes com relatório e controle central num painel web.

> Projeto em reconstrução (v2). Veja `docs/STATUS.md` para o estado atual.

## Como funciona, em resumo

- O **painel** (Next.js) e a **API** (FastAPI + PostgreSQL) ficam na infraestrutura da Artemisys.
- O **agente** é instalado na máquina, VM ou nuvem do cliente. Ele só faz conexões de saída por HTTPS, busca trabalho, executa robôs assinados e devolve status, logs e evidências.
- Os **robôs** consomem **filas de itens** (cada linha de planilha, cada processo) usando o SDK `regista`, com rastreio individual de sucesso, falha e tentativas.

## Documentação

| Documento | Conteúdo |
|---|---|
| `docs/STATUS.md` | Onde o projeto está agora e qual é o próximo passo |
| `docs/ROADMAP.md` | Marcos M0–M8 com critérios de pronto |
| `docs/ARCHITECTURE.md` | Visão geral, componentes, stack e estrutura de pastas |
| `docs/adr/` | Registro das decisões de arquitetura |
| `docs/specs/` | Modelo de dados, orquestração, agente, segurança, frontend e design system (handoff de interface) |
| `docs/runbooks/` | Procedimentos operacionais (implantação em cliente) |
| `docs/apresentacao/` | Documento de apresentação da arquitetura (HTML) |

## Desenvolvimento

Pré-requisitos: Windows com PowerShell, Docker Desktop, [uv](https://docs.astral.sh/uv/), Node.js LTS e Git.

Os comandos de execução, testes e lint estão em `CLAUDE.md` (seção Comandos).

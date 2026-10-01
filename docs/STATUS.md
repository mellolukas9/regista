# Status do projeto

_Atualize este arquivo ao final de cada marco ou sessão de trabalho relevante._

## Agora

- **Marco atual:** M0 — Fundação
- **Situação:** não iniciado. O repositório contém apenas a documentação de base.
- **Próximo passo:** planejar e executar o M0 conforme `docs/ROADMAP.md`.

## Contexto

O Regista começou como um piloto (início de 2026) para orquestrar automações locais com Prefect OSS, FastAPI e Next.js. O piloto validou o fluxo, mas suas premissas não servem para um produto multi-cliente: o Prefect OSS não é multi-tenant e exigiria expor o servidor às máquinas dos clientes; o isolamento por cliente dependia de filtros manuais e tinha vazamentos; não havia filas de itens, testes nem autenticação robusta.

A v2 é uma reescrita do zero com as decisões registradas em `docs/adr/`. Nenhum código do piloto foi mantido.

## Decisões em aberto

| Tema | Quando decidir | Observação |
|---|---|---|
| Região de hospedagem (São Paulo x EUA) | Antes do M8 | Custo x preferência de clientes por dados no Brasil |
| Certificado de assinatura de código do executável Windows | Antes do M8 | Necessário para o MSI não ser bloqueado por SmartScreen/antivírus |
| Provedor de identidade externo (SSO) | Fora do MVP | Autenticação própria agora, preparada para SSO depois |

## Registro

| Data | Marco | O que aconteceu |
|---|---|---|
| 2026-10 | — | Documentação de base da v2 criada (arquitetura, ADRs, specs, roadmap). |

# Status do projeto

_Atualize este arquivo ao final de cada marco ou sessão de trabalho relevante._

## Agora

- **Marco atual:** M1 — Autenticação e tenants
- **Situação:** M0 concluído (PR #1, CI verde). Existem o esqueleto do monorepo, `GET /health`, o banco com RLS provada por teste e o frontend base. Ainda não há autenticação, usuários nem telas de operação.
- **Próximo passo:** planejar o M1 conforme `docs/ROADMAP.md` e aguardar aprovação antes de implementar.

## Contexto

O Regista começou como um piloto (início de 2026) para orquestrar automações locais com Prefect OSS, FastAPI e Next.js. O piloto validou o fluxo, mas suas premissas não servem para um produto multi-cliente: o Prefect OSS não é multi-tenant e exigiria expor o servidor às máquinas dos clientes; o isolamento por cliente dependia de filtros manuais e tinha vazamentos; não havia filas de itens, testes nem autenticação robusta.

A v2 é uma reescrita do zero com as decisões registradas em `docs/adr/`. Nenhum código do piloto foi mantido.

## Decisões em aberto

| Tema | Quando decidir | Observação |
|---|---|---|
| Região de hospedagem (São Paulo x EUA) | Antes do M8 | Custo x preferência de clientes por dados no Brasil |
| Certificado de assinatura de código do executável Windows | Antes do M8 | Necessário para o MSI não ser bloqueado por SmartScreen/antivírus |
| Provedor de identidade externo (SSO) | Fora do MVP | Autenticação própria agora, preparada para SSO depois |
| S3 local de desenvolvimento | Início do M3 (ADR) | A MinIO saiu do M0; escolher a alternativa e registrar em ADR |

## Registro

| Data | Marco | O que aconteceu |
|---|---|---|
| 2026-10 | — | Documentação de base da v2 criada (arquitetura, ADRs, specs, roadmap). |
| 2026-09-30 | M0 | Fundação concluída: workspace uv (`apps/api`, `sdk`, `agent`), FastAPI com `/health`, SQLAlchemy async + Alembic, Postgres 18 com roles `regista_owner`/`regista_app`, migration `0001` (`tenants` + RLS com `FORCE`), `tenant_session`, testes de RLS com Testcontainers, Next 16 com tema e proxy `/api/*`, pre-commit e CI. **Correção do padrão RLS** (`docs/specs/security.md`): a política única `FOR ALL` deixava um administrador da plataforma apagar/alterar linhas de outros tenants; passou a haver políticas por comando (SELECT com admin; INSERT/UPDATE/DELETE só no tenant de contexto; INSERT em `tenants` só para admin da plataforma). A ADR 0004 continua valendo. Outras decisões: PKs com `uuidv7()` nativo do PG18; MinIO fora do M0 (ADR do S3 local no início do M3); TypeScript 6.0.3 e ESLint 9 (versões mais recentes suportadas por typescript-eslint e pelos plugins do eslint-config-next); Ryuk do Testcontainers desligado nos testes (falha de porta no Docker Desktop do Windows). |

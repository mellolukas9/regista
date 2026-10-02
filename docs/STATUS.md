# Status do projeto

_Atualize este arquivo ao final de cada marco ou sessão de trabalho relevante._

## Agora

- **Marco atual:** M0 — Fundação
- **Situação:** a branch `m0-foundation` (PR #1) já entrega a infraestrutura, a API, o RLS provado por teste e o CI. Falta alinhar o frontend ao `docs/specs/design-system.md` (tokens azuis, shell com sidebar e topbar) e conferir o critério de pronto no Windows. O merge do PR #1 é feito pelo responsável do projeto depois da revisão.
- **Próximo passo:** fechar o M0 (frontend do design system) e, depois do merge, abrir o M1 numa branch nova a partir da `main`. **No planejamento do M1, a leitura completa de todas as ADRs e de todas as specs (`agent.md`, `orchestration.md`, `data-model.md`, `security.md`, `design-system.md`) é obrigatória** antes de propor o plano.

## Decisões já aprovadas para o M0 (não perguntar de novo)

| Tema | Decisão |
|---|---|
| PostgreSQL | 18 (imagem `postgres:18`; o volume monta em `/var/lib/postgresql`) |
| Python | 3.13 em `.python-version`, `requires-python >=3.12` |
| SQLAlchemy | 2.1.x; se houver incompatibilidade com asyncpg/Alembic, 2.0.x |
| TypeScript | a versão mais recente **oficialmente suportada** pelo `typescript-eslint` e pelo Next.js (não usar a 7.x nativa ainda) |
| Node.js | 24 LTS |
| Next.js / Tailwind | Next 16 (o `next lint` não existe mais: `npm run lint` chama o ESLint direto) / Tailwind v4 CSS-first |
| UUID v7 | avaliar `uuidv7()` nativo do PostgreSQL 18 como default das PKs; se mantiver na aplicação, justificar em uma linha |
| RLS | políticas separadas por comando, `INSERT` em `tenants` só para admin da plataforma (já refletido em `docs/specs/security.md`) |
| Roles do banco | criados por script de init (`infra/compose/initdb/`) e pela fixture de testes; migrations com grants explícitos |
| S3 local | fora do M0; escolha por ADR no início do M3 |
| Proxy do Next | `/api/*` → API removendo o prefixo |
| Estrutura | mínima: sem pastas vazias de módulos futuros |
| `.gitattributes` | LF padrão; CRLF para `*.bat`, `*.cmd`, `*.ps1` |
| Push e PR | autorizado para `mellolukas9/regista`, após conferir `gh auth status` |

## Contexto

O Regista começou como um piloto (início de 2026) para orquestrar automações locais com Prefect OSS, FastAPI e Next.js. O piloto validou o fluxo, mas suas premissas não servem para um produto multi-cliente. A v2 é uma reescrita do zero com as decisões registradas em `docs/adr/`. Nenhum código do piloto foi mantido.

A interface foi desenhada e entregue como handoff em `docs/specs/design-system.md` (azul Artemisys, telas, textos, permissões e decisões de produto na seção 11). As specs foram atualizadas para refletir essas decisões.

## Decisões em aberto

| Tema | Quando decidir | Observação |
|---|---|---|
| Senha esquecida | Antes do M1 | Proposta: sem autoatendimento; "Reenviar convite" (design-system §11.8) |
| Limites operacionais | Antes dos marcos que os usam | "Sem sinal" após 2 min e alerta após 15 min (M2/M7); uma execução por máquina (M3); retenção de 7 a 365 dias (M5); notificações por 30 dias (M7) |
| "Enviar planilha" em fila no modo Referência | Antes do M6 | Proposta: o navegador lê o arquivo e envia só referência, linha, nomes das colunas e hash |
| S3 local de desenvolvimento | Início do M3 | MinIO community sem imagens publicadas; escolher alternativa por ADR |
| Região de hospedagem (São Paulo x EUA) | Antes do M8 | Custo x preferência de clientes por dados no Brasil |
| Certificado de assinatura de código do executável Windows | Antes do M8 | Necessário para o MSI não ser bloqueado |
| Provedor de identidade externo (SSO) | Fora do MVP | Autenticação própria agora, preparada para SSO depois |

## Registro

| Data | Marco | O que aconteceu |
|---|---|---|
| 2026-09-30 | — | Documentação de base da v2 criada (arquitetura, ADRs, specs, roadmap). |
| 2026-09-30 | M0 | Plano do M0 aprovado com ajustes (ver "Decisões já aprovadas"). Spec de RLS corrigida: políticas separadas por comando. |
| 2026-10-02 | — | Pasta local perdida. Handoff de interface incorporado (`docs/specs/design-system.md`); specs, ROADMAP e ADR 0011 atualizados com as decisões de produto. |
| 2026-09-30 | M0 | Primeira execução do M0 (branch `m0-foundation`, PR #1): workspace uv, FastAPI com `/health`, SQLAlchemy async + Alembic, Postgres 18 com roles `regista_owner`/`regista_app`, migration `0001` (`tenants` + RLS com `FORCE`), `tenant_session`, testes de RLS com Testcontainers, Next 16 com proxy `/api/*`, pre-commit e CI. PKs com `uuidv7()` nativo do PG18; TypeScript 6.0.3 e ESLint 9 (versões mais recentes suportadas pelo typescript-eslint e pelo eslint-config-next); Ryuk do Testcontainers desligado nos testes (falha de porta no Docker Desktop do Windows). |
| 2026-10-02 | M0 | Pasta local recuperada a partir de `origin/m0-foundation`; docs reconciliados arquivo a arquivo (docs novos prevalecem; mantidos `.gitignore`, `uuidv7()` nativo, Comandos do `CLAUDE.md` e políticas `tenant_*` da branch). `ARCHITECTURE.md` passou a PostgreSQL 18. |

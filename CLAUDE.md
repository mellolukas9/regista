# CLAUDE.md

Guia para o Claude Code neste repositório. Leia antes de qualquer tarefa.

## O que é

Regista é uma plataforma de RPA as a Service da Artemisys. Ela orquestra robôs Python + Playwright que rodam **no ambiente do cliente** (máquina física, VM ou nuvem do cliente). O controle fica num painel web central com filas de itens, lotes, execuções, agendamentos e alertas.

Este repositório é a **v2**, reescrita do zero. O piloto anterior foi descartado e não deve ser recriado.

## Onde estamos

- Estado atual e próximo passo: `docs/STATUS.md`
- Marcos e critérios de pronto: `docs/ROADMAP.md`
- Arquitetura: `docs/ARCHITECTURE.md`
- Decisões (por que as coisas são como são): `docs/adr/`
- Especificações detalhadas: `docs/specs/`

Ordem de precedência em caso de conflito: ADRs > `docs/specs/` > `docs/ARCHITECTURE.md` > `docs/apresentacao/` (só apresentação). Aponte qualquer conflito encontrado em vez de escolher em silêncio.

Trabalhe **somente no marco atual** indicado em `docs/STATUS.md`. Não antecipe funcionalidades de marcos futuros.

## Regras inegociáveis

1. **Isolamento entre clientes (tenants).** Toda tabela de cliente tem `tenant_id` e política de Row-Level Security. A aplicação conecta com um role sem `BYPASSRLS`. Nunca contorne RLS. Ver ADR 0004.
2. **Toda rota nova tem teste de isolamento:** um usuário do tenant A não lê, altera nem apaga dados do tenant B.
3. **Sem Prefect, sem Redis, sem Celery.** Fila, estado e agendamento ficam no PostgreSQL. Ver ADRs 0002 e 0003.
4. **O agente só faz conexões de saída (HTTPS).** Nunca criar endpoint ou mecanismo que exija porta aberta no cliente, nem comando arbitrário (shell) enviado pelo servidor. Ver ADRs 0006 e 0008.
5. **Segredos nunca em texto puro**: nem no banco, nem em logs, nem no repositório. Use a abstração `KeyProvider` (dev: chave local; prod: AWS KMS).
6. **Logs e mensagens de erro vindos do agente são dados não confiáveis**: limite de tamanho e exibição escapada.
7. Mudou uma decisão de arquitetura? **Proponha uma nova ADR** em vez de mudar silenciosamente.

## Convenções

- Código, identificadores, nomes de tabela e commits em **inglês**. Documentação e textos da interface em **português (pt-BR)**.
- Commits no padrão Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`).
- Uma branch por marco (`m0-foundation`, `m1-auth`...). PRs pequenos.
- IDs: UUID v7 (padrão `uuidv7()` do PostgreSQL 18; gere na aplicação só quando o id for necessário antes do INSERT). Datas: `timestamptz` em UTC; exibição em `America/Sao_Paulo`.
- Python 3.12+, tipagem estrita (mypy), `ruff` para lint e formatação, `pytest` + Testcontainers (Postgres real) nos testes.
- Frontend: Next.js (App Router, TypeScript strict), Tailwind, tokens de tema em `docs/specs/frontend.md`.
- **Ambiente de desenvolvimento é Windows.** Todo comando documentado precisa funcionar no PowerShell (sem Makefile, sem scripts bash obrigatórios). Docker Desktop disponível.

## Comandos

> Preenchidos e mantidos a partir do M0. Sempre atualize esta seção quando um comando mudar.

Pré-requisitos: Docker Desktop em execução, `uv` e Node 24 LTS. Na primeira vez, `Copy-Item .env.example .env`.

```powershell
# infraestrutura local (Postgres 18)
docker compose -f infra/compose/docker-compose.dev.yml up -d

# backend / sdk / agente (workspace uv na raiz)
uv sync
uv run alembic -c apps/api/alembic.ini upgrade head
uv run uvicorn regista_api.main:app --reload --port 8000
uv run pytest        # os testes de banco sobem o próprio Postgres (Testcontainers); só exigem o Docker ligado
uv run ruff check . ; uv run ruff format --check . ; uv run mypy

# frontend (o proxy /api/* -> http://127.0.0.1:8000 vem de API_URL; padrão já serve para dev)
cd apps/web ; npm install ; npm run dev ; npm run lint ; npm run typecheck ; npm run build

# git hooks (uma vez por clone)
uv run pre-commit install
```

**Roles e init do banco.** Os scripts de `infra/compose/initdb/` (que criam `regista_owner` e `regista_app`) só rodam quando o volume do Postgres está **vazio**. Mudou o script ou quer um banco limpo? Recrie o banco de dev do zero (**apaga todos os dados locais**):

```powershell
docker compose -f infra/compose/docker-compose.dev.yml down -v
docker compose -f infra/compose/docker-compose.dev.yml up -d
uv run alembic -c apps/api/alembic.ini upgrade head
```

Migrations rodam como `regista_owner` (`REGISTA_DATABASE_OWNER_URL`); a API roda como `regista_app` (`REGISTA_DATABASE_URL`).

## Fluxo de trabalho esperado

1. Ler `docs/STATUS.md` e o marco atual em `docs/ROADMAP.md`.
2. Propor um plano curto (arquivos, passos, como validar) e aguardar aprovação.
3. Implementar em passos pequenos, rodando testes e lint a cada passo.
4. Ao terminar: marcar o checklist do marco no `ROADMAP.md`, atualizar `docs/STATUS.md` (estado, próximo passo, registro) e a seção de comandos deste arquivo, se mudou.

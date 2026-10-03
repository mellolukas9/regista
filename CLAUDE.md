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
- Interface (tokens, componentes, telas, textos, permissões, decisões de produto): `docs/specs/design-system.md`

Ordem de precedência em caso de conflito: ADRs > `docs/specs/` (para interface, textos e permissões, `design-system.md`) > `docs/ARCHITECTURE.md` > `docs/apresentacao/` (só apresentação, com cores antigas). Aponte qualquer conflito encontrado em vez de escolher em silêncio.

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
- Frontend: Next.js (App Router, TypeScript strict), Tailwind v4, shadcn/ui; tokens, componentes e textos exatamente como em `docs/specs/design-system.md`.
- **Ambiente de desenvolvimento é Windows.** Todo comando documentado precisa funcionar no PowerShell (sem Makefile, sem scripts bash obrigatórios). Docker Desktop disponível.

## Comandos

> Preenchidos e mantidos a partir do M0. Sempre atualize esta seção quando um comando mudar.

Pré-requisitos: Docker Desktop em execução, `uv` e Node 24 LTS. Na primeira vez, `Copy-Item .env.example .env` e preencha `REGISTA_MASTER_KEY` (a API não sobe sem ela; o `.env` não é versionado):

```powershell
uv run python -c "from regista_api.core.keys import LocalKeyProvider; print(LocalKeyProvider.generate_key())"
```

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

**Atualizar o Procrastinate.** A versão é fixa (`==`) em `apps/api/pyproject.toml` e o schema dele vive no banco, aplicado pela migration `0003`. Nunca rode o `procrastinate schema --apply` nem troque só a versão. Para subir de versão: (1) troque a versão fixa e rode `uv sync`; (2) crie uma migration Alembic nova que aplica, como `regista_owner`, os arquivos de `procrastinate/sql/migrations/` entre a versão antiga e a nova (em ordem), e refaz os grants a `regista_app` (DML nas tabelas, sequences e EXECUTE nas funções novas); (3) confirme que `uv run pytest` passa, incluindo os testes do worker. O registro da decisão fica na ADR 0018.

**Equipe Artemisys e dados de exemplo.** A equipe vive num cliente interno oculto e se gerencia só pelo CLI `regista-admin` (sem tela no MVP; nunca aceita senha por argumento, sempre convite). Em dev, os e-mails (convites) são impressos no console:

```powershell
uv run regista-admin create-platform-admin --email pessoa@artemisys.com.br --name "Pessoa"
uv run regista-admin resend-platform-admin-invite --email pessoa@artemisys.com.br   # invalida senha, MFA e sessões
uv run regista-admin remove-platform-admin --email pessoa@artemisys.com.br          # recusa o último admin ativo
uv run regista-admin seed-dev   # só em dev e banco vazio; imprime senhas e chaves TOTP aleatórias uma única vez
```

O seed cria os clientes e usuários do §10 de `docs/specs/design-system.md` (`admin@`, `operacao@`, `financeiro@` e `estagio@escritorio-exemplo.com.br`, mais `equipe@artemisys.example.com`). Cadastre a chave TOTP impressa em um app autenticador; nenhuma senha é versionada. Para recomeçar, recrie o banco de dev (bloco acima).

## Fluxo de trabalho esperado

1. Ler `docs/STATUS.md` e o marco atual em `docs/ROADMAP.md`.
2. Propor um plano curto (arquivos, passos, como validar) e aguardar aprovação.
3. Implementar em passos pequenos, rodando testes e lint a cada passo. **Ao final de cada passo: commit e push** (`git push`), para o trabalho nunca ficar só na máquina local.
4. Ao terminar: marcar o checklist do marco no `ROADMAP.md`, atualizar `docs/STATUS.md` (estado, próximo passo, registro) e a seção de comandos deste arquivo, se mudou.

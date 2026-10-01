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
- IDs: UUID (preferencialmente v7 gerado na aplicação). Datas: `timestamptz` em UTC; exibição em `America/Sao_Paulo`.
- Python 3.12+, tipagem estrita (mypy), `ruff` para lint e formatação, `pytest` + Testcontainers (Postgres real) nos testes.
- Frontend: Next.js (App Router, TypeScript strict), Tailwind, tokens de tema em `docs/specs/frontend.md`.
- **Ambiente de desenvolvimento é Windows.** Todo comando documentado precisa funcionar no PowerShell (sem Makefile, sem scripts bash obrigatórios). Docker Desktop disponível.

## Comandos

> Preenchidos e mantidos a partir do M0. Sempre atualize esta seção quando um comando mudar.

```powershell
# infraestrutura local (Postgres, MinIO)
docker compose -f infra/compose/docker-compose.dev.yml up -d

# backend / sdk / agente (workspace uv na raiz)
uv sync
uv run alembic -c apps/api/alembic.ini upgrade head
uv run uvicorn regista_api.main:app --reload --port 8000
uv run pytest
uv run ruff check . ; uv run ruff format --check . ; uv run mypy

# frontend
cd apps/web ; npm install ; npm run dev ; npm run lint ; npm run typecheck ; npm run build
```

## Fluxo de trabalho esperado

1. Ler `docs/STATUS.md` e o marco atual em `docs/ROADMAP.md`.
2. Propor um plano curto (arquivos, passos, como validar) e aguardar aprovação.
3. Implementar em passos pequenos, rodando testes e lint a cada passo.
4. Ao terminar: marcar o checklist do marco no `ROADMAP.md`, atualizar `docs/STATUS.md` (estado, próximo passo, registro) e a seção de comandos deste arquivo, se mudou.

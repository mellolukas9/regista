# Arquitetura — Regista v2

> Fonte de verdade técnica. O HTML em `docs/apresentacao/` é material de apresentação; havendo divergência de detalhe (nomes de pastas, campos), prevalecem este documento, as ADRs e as specs.

## Visão geral

```
 Infraestrutura Artemisys                          Ambiente do cliente
 ┌──────────────────────────────┐                  ┌───────────────────────────────┐
 │ Painel web (Next.js)         │                  │ Agente Regista (serviço)      │
 │        │ HTTPS               │   HTTPS 443      │   │                           │
 │        ▼                     │ ◄─────────────── │   ├─ executa robô assinado    │
 │ API Regista (FastAPI)        │  só conexão de   │   │   (Python + Playwright)   │
 │        │                     │  saída, iniciada │   │      │                    │
 │        ▼                     │  pelo agente     │   │      ▼                    │
 │ PostgreSQL (RLS)             │                  │   │  sistemas do cliente      │
 │ S3 (artefatos) + KMS (chaves)│                  │   │  (sites, ERP, planilhas)  │
 └──────────────────────────────┘                  └───────────────────────────────┘
```

O painel nunca fala com o agente. O agente nunca fala com o banco. Toda regra de isolamento entre clientes mora na API e no próprio Postgres (Row-Level Security).

## Princípios

1. **Execução no ambiente do cliente.** Robôs rodam na máquina física, VM ou nuvem do próprio cliente. Execução na infraestrutura da Artemisys só se houver demanda (ADR 0014).
2. **Segurança nos dois sentidos.** Uma máquina de cliente comprometida não alcança outros clientes; um Regista comprometido não executa código arbitrário nas máquinas dos clientes (`docs/specs/security.md`).
3. **PostgreSQL como fonte única de verdade.** Fila de jobs, fila de itens, agendamento e logs (ADR 0002).
4. **Um agente, vários modos.** Serviço em segundo plano, sessão de usuário (robôs com tela) e, no futuro, modo único para containers (ADR 0010).
5. **Pools, não máquinas.** O robô aponta para um pool; mover a execução é configuração (ADR 0009).

## Componentes

| Componente | Onde roda | Responsabilidade |
|---|---|---|
| Painel (`apps/web`) | Artemisys | Operação: bots, pools, máquinas, filas, lotes, execuções, agendamentos, alertas |
| API (`apps/api`) | Artemisys | Autenticação, regras de negócio, distribuição de jobs e itens, agendador, alertas |
| PostgreSQL | Artemisys | Estado de tudo, com RLS por tenant |
| Tarefas internas (Procrastinate) | Artemisys | Alertas, itens abandonados, máquinas offline, consolidação, retenção |
| S3 + KMS | AWS (S3 local compatível em dev, definido no M3) | Artefatos por URL pré-assinada; chaves de criptografia por tenant |
| Agente (`agent`) | Cliente | Identidade da máquina, heartbeat, busca de jobs, execução isolada, envio de logs |
| SDK (`sdk`) | Dentro do robô e do agente | API dos robôs: itens, lotes, logs |

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2 async, asyncpg, Alembic, pydantic-settings, structlog |
| Tarefas internas | Procrastinate (sobre Postgres) |
| Agendamento | croniter + `pg_advisory_lock` |
| Criptografia | `cryptography` (Ed25519, AES-GCM), argon2-cffi, pyotp; AWS KMS em produção |
| Banco | PostgreSQL 18 (`uuidv7()` nativo) |
| Frontend | Next.js (App Router, TS strict), Tailwind v4, shadcn/ui, TanStack Query e Table, Recharts, lucide-react, fonte Geist (ver `docs/specs/design-system.md`) |
| Agente | Python 3.12+, httpx, uv (ambientes por versão de robô), PyInstaller + WinSW + WiX (MSI) |
| Robôs | Python + Playwright (API async) |
| Testes | pytest, Testcontainers (Postgres real), pytest-asyncio |
| Infra | Docker Compose (dev e VPS), Caddy (HTTPS em produção), GitHub Actions |

**Não usar:** Prefect, Redis, Celery/RQ, NextAuth (ver ADRs).

## Estrutura de pastas

```
regista/
├── apps/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── alembic.ini
│   │   ├── migrations/              # Alembic, incluindo políticas RLS
│   │   ├── src/regista_api/
│   │   │   ├── main.py
│   │   │   ├── core/                # config, db (sessão com tenant), segurança, logging
│   │   │   ├── auth/                # login, MFA, sessões, papéis
│   │   │   ├── tenants/             # tenants, usuários
│   │   │   ├── machines/            # pools, cadastro, heartbeat, revogação
│   │   │   ├── bots/                # bots, versões, pacotes
│   │   │   ├── jobs/                # execuções, long-polling, logs
│   │   │   ├── queues/              # filas, itens, lotes, relatórios
│   │   │   ├── schedules/           # agendador
│   │   │   ├── alerts/              # regras e envio
│   │   │   ├── secrets/             # KeyProvider, segredos
│   │   │   ├── audit/               # trilha de auditoria
│   │   │   └── tasks/               # tarefas Procrastinate
│   │   └── tests/
│   └── web/                         # Next.js
│       ├── app/                     # rotas
│       ├── components/              # Sidebar, StatusPill, DataTable, LogViewer...
│       └── lib/                     # cliente da API, tema
├── agent/
│   ├── pyproject.toml
│   ├── src/regista_agent/
│   │   ├── cli.py                   # enroll, run, diagnose
│   │   ├── enroll.py
│   │   ├── transport.py             # HTTPS, proxy, tokens curtos
│   │   ├── runner.py                # pacote, assinatura, uv, execução
│   │   ├── modes/                   # service.py, session.py, oneshot.py
│   │   ├── policy.py                # allowlist local, kill switch
│   │   └── diagnose.py
│   ├── packaging/                   # WinSW, WiX, assinatura (M8)
│   └── tests/
├── sdk/
│   ├── pyproject.toml
│   ├── src/regista/                 # next_item, item_scope, batches, logger, masking
│   └── tests/
├── bots/
│   ├── _template/                   # template async com SDK
│   └── demo_busca_google/           # robô de demonstração (M3)
├── infra/
│   ├── compose/                     # docker-compose.dev.yml (M0), docker-compose.prod.yml (M8)
│   └── cloudformation/              # EC2 com agente na conta do cliente (M8, opcional)
├── docs/
│   ├── STATUS.md
│   ├── ROADMAP.md
│   ├── ARCHITECTURE.md
│   ├── adr/
│   ├── specs/
│   ├── runbooks/
│   └── apresentacao/
├── .github/workflows/
├── pyproject.toml                   # workspace uv
├── CLAUDE.md
└── README.md
```

## Especificações

- `docs/specs/data-model.md` — tabelas e campos
- `docs/specs/orchestration.md` — jobs, itens, retry, lotes, agendamento
- `docs/specs/agent.md` — cadastro, protocolo, modos, execução
- `docs/specs/security.md` — RLS, autenticação, ameaças, modos de dados
- `docs/specs/frontend.md` — como usar o handoff e telas por marco
- `docs/specs/design-system.md` — handoff de interface (tokens, componentes, telas, textos, decisões de produto)

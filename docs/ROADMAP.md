# Roadmap da v2

Cada marco é uma fatia vertical: termina funcionando de ponta a ponta, testado e com o frontend correspondente. Um marco só começa quando o anterior cumpre o critério de pronto.

## Escopo do MVP

**Dentro:** autenticação com MFA e RLS; agente com cadastro por chave e modos serviço e sessão; pacotes de robô assinados; disparo manual; filas e itens com SDK async; lotes com reconciliação e relatório; agendador; alertas por e-mail; painel dark; instalador MSI; deploy em VPS com HTTPS.

**Fora (não implementar no MVP):** execução em containers (Fargate) e modo único em produção; conector na nuvem do cliente; modo de dados ponta a ponta; SSO/SAML; relatório em PDF; alertas por WhatsApp; billing.

---

## M0 — Fundação

**Objetivo:** esqueleto do monorepo com tooling, CI, banco com RLS funcionando e frontend base.

- [ ] Estrutura de pastas conforme `docs/ARCHITECTURE.md`
- [ ] Workspace `uv` na raiz com os pacotes `apps/api`, `sdk`, `agent` (Python 3.12+)
- [ ] `apps/api`: FastAPI com `pydantic-settings`, `structlog`, endpoint `GET /health` (verifica conexão com o banco)
- [ ] SQLAlchemy async + asyncpg + Alembic configurados
- [ ] `infra/compose/docker-compose.dev.yml` com PostgreSQL 16+ e MinIO (S3 local)
- [ ] Dois roles no Postgres: `regista_owner` (migrations, dono das tabelas) e `regista_app` (runtime, **sem** `BYPASSRLS`)
- [ ] Migration inicial: tabela `tenants` + padrão de RLS documentado em `docs/specs/security.md` (incluindo `FORCE ROW LEVEL SECURITY`)
- [ ] Helper de sessão que abre transação e executa `set_config('app.tenant_id', ..., true)` (e `app.platform_admin`)
- [ ] Teste com Testcontainers provando o RLS: dois tenants, consulta sem `WHERE` retorna apenas linhas do tenant da sessão; sem tenant definido, retorna zero linhas
- [ ] `apps/web`: Next.js (versão estável atual, App Router, TS strict) + Tailwind + tokens de tema de `docs/specs/frontend.md` + layout base com sidebar vazia e fonte Geist
- [ ] Proxy de desenvolvimento no Next (`/api/*` → FastAPI) para cookies first-party
- [ ] `sdk` e `agent`: pacotes com `pyproject`, módulo vazio e um teste trivial
- [ ] Lint e tipos: `ruff`, `mypy`, `eslint`, `tsc`; `pre-commit`; `.editorconfig`; `.gitignore`; `.env.example`
- [ ] CI (GitHub Actions): Python (ruff, mypy, pytest) e web (lint, typecheck, build)
- [ ] Seção Comandos do `CLAUDE.md` conferida no PowerShell

**Pronto quando:** `docker compose ... up -d`, migrations, `uv run pytest` (com o teste de RLS) e `npm run build` funcionam no Windows; `/health` responde; CI verde no GitHub.

---

## M1 — Autenticação e tenants

- [ ] Tabelas `users`, `sessions`, `audit_log` com RLS
- [ ] Senha com argon2id; bloqueio progressivo após tentativas erradas; rate limit no login
- [ ] Sessão no servidor com cookie httpOnly, `Secure`, `SameSite=Lax`; proteção CSRF nas rotas que alteram dados
- [ ] MFA por TOTP (obrigatório para administradores), com códigos de recuperação
- [ ] Papéis: `tenant_admin`, `operator`, `viewer` + flag `is_platform_admin` (equipe Artemisys)
- [ ] Seletor de cliente para administradores da plataforma (define o contexto do tenant na sessão)
- [ ] Redefinição de senha por token (envio de e-mail impresso no console em dev)
- [ ] Revogação de sessões (minhas sessões / todas)
- [ ] Fixture de teste que roda **toda rota autenticada** contra outro tenant e espera 404/403
- [ ] Frontend: login, configuração e verificação de MFA, layout autenticado com sidebar, tela de usuários

**Pronto quando:** login com MFA funciona no painel; teste de isolamento cobre todas as rotas; nenhuma senha, segredo TOTP ou token de sessão aparece em log.

---

## M2 — Agente: identidade e presença

- [ ] Tabelas `pools`, `machines`, `enrollment_keys`
- [ ] Painel gera chave de registro de uso único (exibida uma vez; banco guarda só o hash; expira em 24h)
- [ ] `regista-agent enroll --url --key`: gera par Ed25519 local, envia chave pública, recebe `machine_id`
- [ ] Autenticação do agente por desafio assinado → token de acesso curto (15 min) com escopo de máquina e tenant
- [ ] Heartbeat; tarefa interna (Procrastinate) marca máquina offline após X minutos sem sinal
- [ ] Revogação de máquina no painel (efeito imediato)
- [ ] Modo serviço e modo sessão de usuário (ver `docs/specs/agent.md`); execução local via `uv run regista-agent`
- [ ] `regista-agent diagnose` (conexão, proxy, certificado, permissões, versão do Python)
- [ ] Armazenamento protegido da chave privada (ACL restrita / DPAPI no Windows)
- [ ] Frontend: pools e máquinas com status online/offline, gerar chave, revogar

**Pronto quando:** uma VM ou a própria máquina de dev se cadastra, aparece online, cai para offline ao parar o agente e para de funcionar ao ser revogada.

---

## M3 — Disparo manual de ponta a ponta

- [ ] Tabelas `bots`, `jobs`, `job_logs` (particionada por mês)
- [ ] `POST /jobs` pelo painel; `GET /agent/jobs/next?wait=30` com long-polling acordado por `LISTEN/NOTIFY`
- [ ] Runner do agente executa robô a partir de pasta local **somente com flag de desenvolvimento** (assinatura vem no M4)
- [ ] Envio de logs em lote; screenshots via URL pré-assinada (MinIO em dev, S3 em prod)
- [ ] Cancelamento de job pelo painel
- [ ] Robô de demonstração `bots/demo_busca_google` (Playwright async, pesquisa um termo e tira screenshot)
- [ ] Status ao vivo no painel (SSE)
- [ ] Frontend: telas Bots e Execuções com painel lateral de logs (ver `docs/specs/frontend.md`)

**Pronto quando:** clicar em "Executar agora" roda o robô de demonstração pelo agente e o painel mostra estados, logs e screenshot sem recarregar a página.

---

## M4 — Pacotes de robô assinados

- [ ] Tabela `bot_versions`
- [ ] CLI `regista-pack`: empacota o robô, calcula sha256 e assina com Ed25519 (chave privada fora do repositório e do servidor)
- [ ] Upload da versão pelo painel/CLI; o servidor guarda pacote, hash e assinatura
- [ ] Agente verifica assinatura com a chave pública embutida antes de executar; recusa pacote inválido
- [ ] Ambiente isolado por versão com `uv` (cache local)
- [ ] Lista local de robôs permitidos por máquina e kill switch local
- [ ] Flag de desenvolvimento do M3 desabilitada em configuração de produção

**Pronto quando:** pacote adulterado é recusado pelo agente e o painel mostra o motivo; robô fora da lista local não executa.

---

## M5 — Filas de itens

- [ ] Tabelas `queues`, `queue_items`
- [ ] SDK async (`httpx`): `next_item`, `item_scope`, `success`, `fail(business=...)`, `release`, renovação automática da trava
- [ ] Retirada atômica com `FOR UPDATE SKIP LOCKED`; trava com prazo (`locked_until`)
- [ ] Regras de retry (negócio x aplicação), espera crescente, `defer_until`
- [ ] Tarefa interna que marca itens abandonados e devolve à fila
- [ ] Logs por item via `contextvars`; concorrência configurável por robô
- [ ] Mascaramento de CPF, CNPJ e e-mail em erros e logs no SDK
- [ ] Modos de dados `reference` (padrão) e `central` (payload criptografado via `KeyProvider`)
- [ ] Frontend: Filas, tabela de itens com paginação no servidor, detalhe do item (logs, tentativas, evidências), reprocessar

**Pronto quando:** o robô de demonstração processa uma fila com concorrência 3; matar o agente no meio faz os itens voltarem à fila e serem concluídos depois.

---

## M6 — Lotes e relatórios

- [ ] Tabelas `batches`, `batch_rejections`
- [ ] SDK do dispatcher: `create_batch`, `add_item`, `reject_row`, `close_batch`
- [ ] Invariante de reconciliação garantida: linhas lidas = enfileiradas + rejeitadas (lote não fecha se não bater)
- [ ] Relatório do lote por referência, exportação CSV e XLSX, reprocessar só as falhas
- [ ] Helper do SDK para devolver Status/Data/Mensagem à planilha original no fim do lote (dentro do ambiente do cliente)
- [ ] Campos visíveis configuráveis por fila
- [ ] Frontend: Lotes e relatório do lote

**Pronto quando:** uma planilha de teste com linhas válidas, inválidas e com falha gera um relatório que reconcilia 100% das linhas e uma planilha devolvida com o resultado.

---

## M7 — Agendamento e alertas

- [ ] Tabela `schedules`; agendador com `croniter` + advisory lock (uma instância dispara)
- [ ] Prévia em linguagem natural e próximas 3 execuções
- [ ] Tabela `alerts`; envio por e-mail (SMTP)
- [ ] Eventos: job falhou, máquina sem sinal, lote concluído (com resumo), falhas de negócio acima de limite
- [ ] Frontend: Agendamentos (com atalhos de frequência) e Alertas

**Pronto quando:** um agendamento de dias úteis dispara no horário, e desligar a máquina gera e-mail de alerta.

---

## M8 — Produção e primeiro cliente

- [ ] Empacotamento do agente: PyInstaller + WinSW + instalador MSI (WiX), assinatura de código
- [ ] Autoatualização assinada do agente
- [ ] Deploy em VPS: Docker Compose de produção, Caddy com HTTPS, CORS e hosts por ambiente
- [ ] Backup diário do Postgres para S3 com teste de restauração; Sentry
- [ ] `KeyProvider` de produção com AWS KMS
- [ ] Template CloudFormation para EC2 com agente na conta do cliente (opcional no MVP)
- [ ] Runbook `docs/runbooks/implantacao-cliente.md` validado em uma implantação real

**Pronto quando:** o primeiro cliente está em produção seguindo o runbook.

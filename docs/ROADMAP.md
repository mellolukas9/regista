# Roadmap da v2

Cada marco é uma fatia vertical: termina funcionando de ponta a ponta, testado e com o frontend correspondente. Um marco só começa quando o anterior cumpre o critério de pronto.

## Escopo do MVP

**Dentro:** clientes, convites, autenticação com MFA e RLS; agente com cadastro por chave e modos serviço e sessão; pacotes de robô assinados; disparo manual; filas e itens com SDK async; lotes com reconciliação e relatório; agendador; alertas por e-mail; painel dark; instalador MSI; deploy em VPS com HTTPS.

A interface de cada marco segue `docs/specs/design-system.md` (mapa de telas por marco em `docs/specs/frontend.md`).

**Fora (não implementar no MVP):** execução em containers (Fargate) e modo único em produção; conector na nuvem do cliente; modo de dados ponta a ponta; SSO/SAML; relatório em PDF; alertas por WhatsApp; billing.

---

## M0 — Fundação

**Objetivo:** esqueleto do monorepo com tooling, CI, banco com RLS funcionando e frontend base.

- [x] Estrutura de pastas conforme `docs/ARCHITECTURE.md`
- [x] Workspace `uv` na raiz com os pacotes `apps/api`, `sdk`, `agent` (Python 3.12+)
- [x] `apps/api`: FastAPI com `pydantic-settings`, `structlog`, endpoint `GET /health` (verifica conexão com o banco)
- [x] SQLAlchemy async + asyncpg + Alembic configurados
- [x] `infra/compose/docker-compose.dev.yml` **só com PostgreSQL 18** (S3 local fica para o M3, com ADR) e scripts de init dos roles em `infra/compose/initdb/`
- [x] Dois roles no Postgres: `regista_owner` (migrations, dono das tabelas) e `regista_app` (runtime, **sem** `BYPASSRLS`)
- [x] Migration inicial: schema `app` com funções, tabela `tenants` e padrão de RLS de `docs/specs/security.md` (políticas separadas por comando, `FORCE ROW LEVEL SECURITY`, grants explícitos); helper `core/rls.py`
- [x] Helper de sessão que abre transação e executa `set_config('app.tenant_id', ..., true)` (e `app.platform_admin`)
- [x] Teste com Testcontainers provando o RLS (com tabela-sonda criada só no teste): role sem `BYPASSRLS`; sem tenant = zero linhas; tenant A sem `WHERE` vê só A; escrita cruzada bloqueada; admin da plataforma lê tudo mas não altera nem apaga fora do contexto; conexão reaproveitada não herda tenant; guarda que exige RLS em toda tabela com `tenant_id`
- [x] `apps/web`: Next.js (versão estável atual, App Router, TS strict) + Tailwind v4 com os tokens da seção 2 de `docs/specs/design-system.md` + Geist via `next/font` + shell vazio (sidebar de 248px e topbar, menu recolhível abaixo de 900px)
- [x] Proxy de desenvolvimento no Next (`/api/*` → FastAPI, removendo o prefixo: `/api/health` → `/health`)
- [x] `sdk` e `agent`: pacotes com `pyproject`, módulo vazio e um teste trivial
- [x] Lint e tipos: `ruff`, `mypy`, `eslint`, `tsc`; `pre-commit`; `.editorconfig`; `.gitignore`; `.env.example`; `.gitattributes` (LF padrão, CRLF para `*.bat`, `*.cmd`, `*.ps1`)
- [x] CI (GitHub Actions): Python (ruff, mypy, pytest) e web (lint, typecheck, build)
- [x] Seção Comandos do `CLAUDE.md` conferida no PowerShell

**Pronto quando:** `docker compose ... up -d`, migrations, `uv run pytest` (com o teste de RLS) e `npm run build` funcionam no Windows; `/health` responde; CI verde no GitHub.

---

## M1 — Clientes, autenticação e usuários

- [x] Tabelas `users`, `invitations`, `recovery_codes`, `sessions`, `audit_log` com RLS
- [x] Clientes: criação só pela equipe Artemisys (nome + e-mail do primeiro Admin do cliente, que recebe convite)
- [x] Convites: link de uso único → definir senha → configurar MFA; status "Convite enviado" até o primeiro acesso; "Reenviar convite"; "Remover acesso"
- [x] Senha com argon2id; bloqueio progressivo após tentativas erradas; rate limit no login
- [x] Sessão no servidor com cookie httpOnly, `Secure`, `SameSite=Lax`; proteção CSRF nas rotas que alteram dados
- [x] MFA por TOTP obrigatório para todos, com 10 códigos de recuperação de uso único
- [x] Papéis: `tenant_admin`, `operator`, `viewer` + flag `is_platform_admin` (equipe Artemisys)
- [x] Seletor de cliente para a equipe Artemisys (cookie `rg_client` validado pela API; vazio = "Todos os clientes", leitura consolidada)
- [x] Permissões por papel aplicadas no servidor conforme a matriz de `design-system.md` §4
- [x] Sem redefinição de senha por autoatendimento no MVP ("Reenviar convite" cobre o caso; ver decisões em aberto). E-mails impressos no console em dev
- [x] Revogação de sessões (minhas sessões / todas)
- [x] Fixture de teste que roda **toda rota autenticada** contra outro tenant e espera 404/403
- [x] Frontend: Login e MFA (7.1), Clientes (7.16), Usuários (7.17), Minhas sessões (7.18), shell autenticado com seletor de cliente

**Pronto quando:** login com MFA funciona no painel; teste de isolamento cobre todas as rotas; nenhuma senha, segredo TOTP ou token de sessão aparece em log.

---

## M2 — Agente: identidade e presença

- [ ] Tabelas `pools`, `machines`, `enrollment_keys`, `machine_events`
- [ ] Painel gera chave de registro de uso único (KeyReveal: exibida uma vez; banco guarda só o hash; expira em 24h; "Gerar nova chave" invalida a anterior); máquina fica `pending` até o cadastro
- [ ] `regista-agent enroll --url --key`: gera par Ed25519 local, envia chave pública, recebe `machine_id`
- [ ] Autenticação do agente por desafio assinado → token de acesso curto (15 min) com escopo de máquina e tenant
- [ ] Heartbeat; tarefa interna (Procrastinate) marca máquina `offline` após 2 minutos sem sinal; histórico em `machine_events`
- [ ] Revogação de máquina no painel (efeito imediato)
- [ ] Modo serviço e modo sessão de usuário (ver `docs/specs/agent.md`); execução local via `uv run regista-agent`
- [ ] `regista-agent diagnose` (conexão, proxy, certificado, permissões, versão do Python)
- [ ] Armazenamento protegido da chave privada (ACL restrita / DPAPI no Windows)
- [ ] Frontend: Máquinas e pools (7.13) e Detalhe da máquina (7.14)

**Pronto quando:** uma VM ou a própria máquina de dev se cadastra, aparece online, cai para offline ao parar o agente e para de funcionar ao ser revogada.

---

## M3 — Disparo manual de ponta a ponta

- [ ] ADR curta escolhendo o S3 local de desenvolvimento (a edição community do MinIO deixou de publicar imagens)
- [ ] Tabelas `bots`, `jobs`, `job_logs` (particionada por mês), `artifacts`; cadastro de bot só pela equipe Artemisys
- [ ] Distribuição: uma execução por máquina; sem máquina livre, o job fica `pending`
- [ ] `POST /jobs` pelo painel; `GET /agent/jobs/next?wait=30` com long-polling acordado por `LISTEN/NOTIFY`
- [ ] Runner do agente executa robô a partir de pasta local **somente com flag de desenvolvimento** (assinatura vem no M4)
- [ ] Envio de logs em lote; screenshots via URL pré-assinada (S3 local escolhido na ADR em dev, S3 em prod)
- [ ] Cancelamento de job pelo painel
- [ ] Robô de demonstração `bots/demo_busca_google` (Playwright async, pesquisa um termo e tira screenshot)
- [ ] Painel atualiza por polling a cada 15 s, com indicador de sincronização
- [ ] Frontend: Bots (7.5), Detalhe do bot (7.6, sem versões), Execuções (7.3), Detalhe da execução (7.4), Dashboard inicial (7.2)

**Pronto quando:** clicar em "Executar agora" roda o robô de demonstração pelo agente e o painel mostra estados, logs e captura de tela, atualizando sozinho.

---

## M4 — Pacotes de robô assinados

- [ ] Tabela `bot_versions`
- [ ] CLI `regista-pack`: empacota o robô, calcula sha256 e assina com Ed25519 (chave privada fora do repositório e do servidor)
- [ ] Publicação de versão (só equipe Artemisys) com nota do que mudou; o servidor guarda pacote, hash e assinatura
- [ ] Frontend: aba de versões do Detalhe do bot (7.6)
- [ ] Agente verifica assinatura com a chave pública embutida antes de executar; recusa pacote inválido
- [ ] Ambiente isolado por versão com `uv` (cache local)
- [ ] Lista local de robôs permitidos por máquina e kill switch local
- [ ] Flag de desenvolvimento do M3 desabilitada em configuração de produção

**Pronto quando:** pacote adulterado é recusado pelo agente e o painel mostra o motivo; robô fora da lista local não executa.

---

## M5 — Filas de itens

- [ ] Tabelas `queues`, `queue_items`, `item_attempts`; criação de fila só pela equipe Artemisys; nome `[a-z0-9-]+` imutável
- [ ] SDK async (`httpx`): `next_item`, `item_scope`, `success`, `fail(business=...)`, `release`, renovação automática da trava
- [ ] Retirada atômica com `FOR UPDATE SKIP LOCKED`; trava com prazo (`locked_until`)
- [ ] Regras de tentativa: falha de aplicação tenta de novo na mesma execução até `max_attempts`; falha de negócio é final; job `failed` se qualquer item falhar
- [ ] Itens `in_progress` viram `abandoned` ao fim da execução ou quando a trava vence (sem voltar sozinhos); "Reprocessar" (item, falhas da fila) volta `failed`/`abandoned` para `new` mantendo as tentativas
- [ ] `action_hint` por item montado pelo backend a partir do último motivo
- [ ] Logs por item via `contextvars`; concorrência configurável por robô
- [ ] Mascaramento de CPF, CNPJ e e-mail em erros e logs no SDK
- [ ] Modos de dados `reference` (padrão) e `central` (payload criptografado via `KeyProvider`)
- [ ] Frontend: Filas (7.8), Detalhe da fila (7.9), Detalhe do item (7.10)

**Pronto quando:** o robô de demonstração processa uma fila com concorrência 3; matar o agente no meio deixa a execução como Falhou e os itens em andamento como Abandonado; "Reprocessar falhas" os conclui numa nova execução, mantendo o histórico de tentativas.

---

## M6 — Lotes e relatórios

- [ ] Tabelas `batches`, `batch_rejections`
- [ ] "Enviar planilha" no painel (`.xlsx`/`.csv` até 10 MB), respeitando o modo de dados da fila (ver `security.md`)
- [ ] Rejeições padronizadas: referência repetida no lote, referência vazia, item já concluído na fila
- [ ] SDK do dispatcher: `create_batch`, `add_item`, `reject_row`, `close_batch`
- [ ] Invariante de reconciliação garantida: linhas lidas = enfileiradas + rejeitadas (lote não fecha se não bater)
- [ ] Relatório do lote por referência, exportação CSV e XLSX, "Reprocessar falhas" (Falhou + Abandonado), "Cancelar lote" (só itens `new`)
- [ ] Helper do SDK para devolver Status/Data/Mensagem à planilha original no fim do lote (dentro do ambiente do cliente)
- [ ] Campos visíveis configuráveis por fila
- [ ] Frontend: Lotes (7.11) e Relatório do lote (7.12)

**Pronto quando:** uma planilha de teste com linhas válidas, inválidas e com falha gera um relatório que reconcilia 100% das linhas e uma planilha devolvida com o resultado.

---

## M7 — Agendamento e alertas

- [ ] Tabela `schedules`; agendador com `croniter` + advisory lock (uma instância dispara)
- [ ] Prévia em linguagem natural e próximas 3 execuções
- [ ] Tabelas `alerts` e `notifications`; envio por e-mail (SMTP), uma vez por ocorrência, com link para o alvo
- [ ] Eventos de `design-system.md` §11.6: execução falhou, execução pendente há mais de 30 min, máquina sem sinal há mais de 15 min, itens com falha ou abandonados (um e-mail por execução), item abandonado
- [ ] Notificações do sino (lida/não lida, "Marcar todas como lidas", contagem na topbar), guardadas por 30 dias
- [ ] API devolve as próximas 3 datas de cada agendamento e os disparos das próximas 48 h
- [ ] Frontend: Agendamentos (7.7 e aba do bot), Alertas (7.15), sino de notificações, Dashboard completo (7.2)

**Pronto quando:** um agendamento de dias úteis dispara no horário, e desligar a máquina gera e-mail de alerta e notificação no sino.

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

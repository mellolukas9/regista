# Status do projeto

_Atualize este arquivo ao final de cada marco ou sessão de trabalho relevante._

## Agora

- **Marco atual:** M4 — Pacotes de robô assinados (o M3 está pronto; PR #4 aguardando revisão e merge pelo responsável do projeto)
- **Situação:** M3 concluído: bots, execuções ("jobs"), logs em lote e capturas de tela por URL pré-assinada (SeaweedFS em dev), "Executar agora" até o robô rodar pelo agente real, long-polling acordado por `LISTEN/NOTIFY`, cancelamento pelo heartbeat, revogar cancela a execução em andamento, `machine_lost` pelo worker, partições mensais de `job_logs`, robô de demonstração `bots/demo_busca_wikipedia`, e as telas Bots, Detalhe do bot, Execuções, Detalhe da execução, Dashboard e os itens do M3 em Máquinas, com polling de 15 s e o indicador de sincronização.
- **Próximo passo:** depois do merge do PR #4, abrir a branch `m4-signed-packages` a partir da `main` e planejar o M4 (`regista-pack`, `bot_versions`, verificação de assinatura no agente, ambiente por versão com `uv`, lista local de robôs permitidos e kill switch; a flag `REGISTA_DEV_UNSIGNED` deixa de ser necessária). Ler `docs/specs/agent.md` e a ADR 0008 antes do plano, que deve ser aprovado antes de implementar.
- **Pendências para decisão:** ESLint 10 (depois do M3, abaixo) e o teste do `enroll` real em console elevado antes do primeiro cliente (abaixo).

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

## Decisões aprovadas para o M1 (não perguntar de novo)

| Tema | Decisão |
|---|---|
| Senha esquecida | Sem autoatendimento no MVP. "Reenviar convite" (Admin do cliente ou equipe Artemisys) invalida a senha e o MFA atuais, encerra as sessões do usuário e obriga a definir senha e configurar MFA de novo |
| E-mail | Único **global** (`citext unique` em `users`). Convidar um e-mail que existe em outro cliente dá erro genérico, sem revelar qual cliente |
| Login e RLS | A busca por e-mail, token de convite e token de sessão (antes de existir cliente na sessão) usa funções `SECURITY DEFINER` mínimas (`app.lookup_login`, `app.lookup_invitation`, `app.lookup_session`), com `search_path` fixo e `EXECUTE` só para `regista_app`. A aplicação nunca usa o role owner e RLS nunca é contornado de forma genérica |
| Equipe Artemisys | Tenant interno oculto (`tenants.is_internal`, só um, nunca desativado, fora de Clientes, do seletor e das visões consolidadas). `is_platform_admin` só no tenant interno, e todo usuário dele é platform admin, garantido por trigger no banco |
| Bootstrap e gestão da equipe | CLI `regista-admin` (criar, reenviar convite, remover; recusa remover o último admin da plataforma ativo). Sem senha por argumento; nada por migration ou seed de produção. **Tela de gestão da equipe Artemisys fica fora do M1** |
| Trocar senha | Em Minhas sessões: senha atual + nova + código TOTP; encerra as outras sessões, audita e envia e-mail |
| E-mail transacional | Interface `EmailSender`; em dev imprime no console. SMTP real no M7/M8 |
| Prazos padrão | Convite 7 dias; sessão 12 h de inatividade e 7 dias no total; sessão parcial (antes do MFA concluído) 10 min. Configuráveis em `Settings` |
| Cidade da sessão | Fora do M1 (exigiria GeoIP). A lista mostra aparelho, navegador e último uso |
| Cabeçalhos | `Cache-Control: no-store` em `/auth/*` e `/account/*`; `nosniff` na API; no Next, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff` e `Referrer-Policy`. A CSP completa de scripts fica para o M8 |

**Risco conhecido e aceito no MVP:** o bloqueio progressivo por conta permite que alguém bloqueie a conta de outra pessoa errando a senha de propósito. Mitigação: teto de 15 min e rate limit por e-mail. Evolução possível: considerar o IP no bloqueio.

## Decisões aprovadas para o M2 (não perguntar de novo)

| Tema | Decisão |
|---|---|
| "Gerar nova chave" | Vale para qualquer máquina não revogada. Em máquina já cadastrada, abre um AlertDialog avisando que o agente atual para quando a nova chave for usada; se ela expirar sem uso, nada muda. O uso gera o evento `re_enrolled` (não repete `enrolled`) e o `audit_log` guarda quem gerou e quando foi usada |
| Chave privada no Windows | DPAPI `LocalMachine` com entropia fixa + ACL em `%ProgramData%\Regista\keys\`, herança desligada, acesso só para a conta que roda o agente (`--agent-account`; padrão `NT SERVICE\RegistaAgent` em modo serviço, obrigatória em modo sessão), SYSTEM e Administradores; nunca para quem rodou o `enroll`. O `diagnose` confere a ACL |
| Worker | Processo separado (`regista-worker`), role `regista_app` sem `BYPASSRLS`. Tarefas entre clientes: leitura com flag de plataforma, escrita com o tenant da linha. Três terminais em dev (API, worker, web) |
| Token do agente | `rga1.<payload>.<mac>`, 15 min, HMAC-SHA256 pelo `KeyProvider.mac`; `credential_version` derruba o agente antigo no recadastro |
| Procrastinate | Versão fixa; atualizar por migration Alembic (procedimento no `CLAUDE.md`) |
| Relógio do agente | Renovação e backoff com `time.monotonic` |
| Nome da máquina | `[a-z0-9][a-z0-9-]{0,62}`, único por cliente entre as não revogadas |
| Textos | `design-system.md` §13, aprovados pelo responsável com ajustes (banner por modo, endereço no KeyReveal, plural do contador) |
| Fluxo | Ao final de cada passo: commit **e** push (regra registrada no `CLAUDE.md`) |

**Risco aceito no MVP (ADR 0018):** com DPAPI `LocalMachine`, um administrador da máquina consegue extrair a chave privada e usá-la. Mitigação: revogar a máquina no painel invalida a identidade na hora. Evolução: guardar a chave no TPM (provedor de chaves da plataforma); o resto do agente não muda.

## Decisões aprovadas para o M3 (não perguntar de novo)

| Tema | Decisão |
|---|---|
| S3 local | SeaweedFS (ADR 0019); o teste de fumaça (`test_s3_smoke.py`) é o critério de aceite. Em produção, S3 da AWS por role IAM: API e worker se recusam a subir com chave estática ou endpoint local |
| Distribuição | Canal único `regista_jobs`, uma conexão asyncpg de escuta por processo, esperas em memória, retirada com `SKIP LOCKED` filtrada por cliente e pool, uma execução por máquina garantida por índice único (ADR 0020) |
| Partições de `job_logs` | Sem `DEFAULT`; função `app.ensure_job_log_partitions` (migration e tarefa diária do worker); partição faltando = 503 + log ERROR e `/health` `degraded` quando falta a do mês seguinte |
| Limites | Captura até 5 MB e 20 por execução; log de 4 KB por linha, 20 000 linhas e 5 MB por execução; tempo máximo de 2 h; tudo em `Settings` |
| Runner em dev | `REGISTA_DEV_UNSIGNED=1` só com `REGISTA_ENVIRONMENT=dev` (o agente assume `prod`); em produção o agente se recusa a iniciar e o servidor recusa criar execução enquanto não houver versão assinada |
| Escopo das telas | Dashboard do cliente sem gráfico e Detalhe da execução sem a aba "Itens" (voltam no M5); "Executar agora" em Execuções leva a Bots; sem "Agendar", "Próxima" e versões até M7 e M4 |
| Robô de demonstração | `bots/demo_busca_wikipedia` (a Wikipédia é estável e não pede verificação anti-robô; o Google pedia) |
| Textos | `design-system.md` §14, aprovados na revisão do M3 |

## Pendências do fim do M2

- **`npm audit`: 5 vulnerabilidades "high", todas em ferramentas de desenvolvimento.** `npm audit --omit=dev` dá 0: nada disso vai para o app em produção. A cadeia é `braces` → `micromatch` → `fast-glob` → `@next/eslint-plugin-next` → `eslint-config-next`, usada só pelo linter, que processa os padrões de arquivo do próprio repositório (o ataque pede padrões de glob aninhados fundo, que não vêm de fora). O `braces 3.0.3` é a última versão publicada e não há versão corrigida, então **não existe correção sem `--force`**; e o `npm audit fix --force` sugerido **rebaixaria** o `eslint-config-next` para a 14, o que quebra o projeto (Next 16). **Decidido: aceitar o risco**, registrado aqui, e rodar `npm audit` de novo a cada atualização do Next ou do ESLint.
- **ESLint 9.39.5 "não suportado" (decidido: depois do M3).** É o aviso da linha 9.x, que saiu para manutenção (`npm view eslint dist-tags`: `maintenance` 9.39.5, `latest` 10.12.0). Subir para a 10 é mudança de versão maior: o `eslint-config-next` 16.3.8 aceita `eslint >=9` no peer, mas os plugins que ele traz (react, import, jsx-a11y) podem não estar prontos. **Pendência: depois do M3, testar a 10 numa branch própria** e só adotar se `npm run lint` passar sem avisos; do contrário, ficar na 9.39.5 até o Next trazer suporte.

## Contexto

O Regista começou como um piloto (início de 2026) para orquestrar automações locais com Prefect OSS, FastAPI e Next.js. O piloto validou o fluxo, mas suas premissas não servem para um produto multi-cliente. A v2 é uma reescrita do zero com as decisões registradas em `docs/adr/`. Nenhum código do piloto foi mantido.

A interface foi desenhada e entregue como handoff em `docs/specs/design-system.md` (azul Artemisys, telas, textos, permissões e decisões de produto na seção 11). As specs foram atualizadas para refletir essas decisões.

## Decisões em aberto

| Tema | Quando decidir | Observação |
|---|---|---|
| Limites operacionais | Antes dos marcos que os usam | "Sem sinal" após 2 min e alerta após 15 min (M2/M7); uma execução por máquina (decidido no M3); retenção de 7 a 365 dias (M5); notificações por 30 dias (M7) |
| "Enviar planilha" em fila no modo Referência | Antes do M6 | Proposta: o navegador lê o arquivo e envia só referência, linha, nomes das colunas e hash |
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
| 2026-10-02 | M0 | Frontend alinhado ao `design-system.md`: tokens da seção 2 (azul Artemisys), Geist, shell com sidebar de 248px (itens não navegáveis), topbar (caminho, sincronização, busca e sino desabilitados) e menu recolhível abaixo de 900px; `lucide-react` adicionado. Critério de pronto conferido no Windows: 15 testes, ruff, mypy, lint, typecheck e build verdes; `/health` e `/api/health` (proxy) respondem. Sem `shadcn init` (adiado até o primeiro marco que precisar de componentes). |
| 2026-10-02 | M1 | Branch `m1-auth` aberta. Testes da API passam a usar `httpx.AsyncClient` + `ASGITransport` (helper `api_client` em `apps/api/tests/conftest.py`, que também executa o lifespan do app) no lugar do `TestClient`; o `StarletteDeprecationWarning` do M0 deixou de aparecer. |
| 2026-10-02 | M1 | Leitura completa e obrigatória feita (CLAUDE.md, STATUS, ROADMAP, ARCHITECTURE, ADRs 0001–0016, specs `agent`, `orchestration`, `data-model`, `security`, `frontend` e `design-system` inteiros). Plano do M1 aprovado com ajustes (ver "Decisões aprovadas para o M1"). ADR 0017 proposta. |
| 2026-10-02 | M1 | Backend do M1 pronto (passos 2 a 9): tabelas e RLS (`0002`), funções `SECURITY DEFINER` mínimas (no PG18 o flag é ligado e restaurado dentro do corpo da função, não com `SET` na definição), fluxos de convite/senha/MFA/sessão, CSRF, bloqueio progressivo, rate limit no Postgres, auditoria, `regista-admin` e `seed-dev`. Varredura de isolamento por descoberta automática de rotas (rota sem marcador, parâmetro sem entrada no registro ou modelo sem `examples` falha o teste; checada por mutação), matriz de permissões do §4 e teste de ausência de segredos em log. CI verde no Linux. |
| 2026-10-02 | M1 | Frontend do M1 (passos 10 a 13): shadcn/ui com os tokens do §2, telas de Login e MFA, convite, Clientes, Usuários e Minhas sessões, shell com seletor de cliente, rodapé do usuário e favicon. Conferido no navegador de ponta a ponta contra o stack de dev (convite do `estagio@` até o painel, login com TOTP, Leitor sem acesso a Usuários, equipe Artemisys entrando em um cliente), o que revelou e corrigiu a perda do token do convite no modo estrito do React. Nenhuma senha, segredo TOTP ou token apareceu no log da API. |
| 2026-10-03 | M2 | Pasta local perdida de novo e refeita a partir do GitHub (passos 1 a 8 estavam enviados). Ajustes pós-plano conferidos e completados (`agent/README.md`, procedimento do Procrastinate); nova regra de commit e push por passo. |
| 2026-10-03 | M2 | Telas 7.13 e 7.14, sidebar com contador de "sem sinal", seletor de cliente com "N sem sinal" e coluna Máquinas em Clientes. Conferido no navegador contra o stack de dev: cadastrar pool e máquina, KeyReveal (sem Esc, "Concluir" só após o checkbox), máquina online, "Sem sinal" com banner e contador, revogação com nome digitado, 390 px. Docs fechados (ADR 0018 aceita, specs, runbook, ROADMAP). |
| 2026-10-03 | M3 | Plano aprovado e M3 implementado em 14 passos (PR #4): ADRs 0019 e 0020; migration `0004`; bots, execuções, distribuição por long-polling, cancelamento, `machine_lost`, logs, capturas por URL pré-assinada; runner do agente em modo de desenvolvimento; robô de demonstração; telas Bots, Execuções, Detalhe da execução e Dashboard. Conferido no navegador com o agente real (Executar agora, cancelar no meio, agente derrubado, revogar com execução em andamento); a conferência achou um traceback no thread de logs ao revogar, corrigido com teste. O robô de demonstração passou do Google (verificação anti-robô) para a Wikipédia. |

## Desvios e escolhas do M1 para a revisão

- **Reenviar convite no último Admin ativo é permitido.** O plano previa a trava também aqui, mas é o caminho de quem esqueceu a senha (e a Artemisys sempre pode fazê-lo); a trava de último Admin vale para mudar papel e remover acesso.
- **TanStack Table v8 (8.21.3, fixada).** O `latest` do npm já é a v9, com API diferente; a v8 é a estável que a documentação do projeto assume. Migrar quando houver motivo.
- **Textos novos** (convite, trocar senha, reenviar convite, erros) estão em `design-system.md` §12; os toasts "Cliente criado. Convite enviado para …", "Convite enviado para …", "Papel salvo", "Acesso removido", "Sessões encerradas", "Sessão encerrada", "Outras sessões encerradas" e o aviso de "Todos os clientes" em Usuários foram propostos aqui e ainda não estão no documento.
- **Colunas de Máquinas, Bots e Última execução da tela Clientes** ficam para os marcos que criam esses dados; a cidade da sessão (GeoIP) ficou fora.
- **"Gerar novos códigos"** mostra os códigos em `/login/recovery-codes?from=account` (como no design), mantidos só em memória.
- **Tabela de Usuários** põe "Reenviar convite" num menu "Mais ações" para a linha ficar compacta; no mobile continua como botão.
- **Cabeçalhos:** CSP completa de scripts fica para o M8.

## Desvios e escolhas do M2 para a revisão

- **Conferência no navegador sem o `regista-agent` real.** O `enroll` grava a chave numa pasta que só um console elevado consegue escrever (por desenho), e a sessão de desenvolvimento não era elevada. A conferência usou um script que fala o mesmo protocolo (enroll, desafio, token, heartbeat) por HTTP. O agente real é coberto pelo teste ponta a ponta e, no Windows, pelo job `agent-windows` da CI (inclui "enroll por um usuário, leitura pela conta do agente"). Falta rodar o agente real num console elevado antes do primeiro cliente.
- **Não conferido no navegador:** a visão de Operador/Leitor (sem botões; coberta pela matriz de permissões do servidor) e "Gerar nova chave" numa máquina já cadastrada (coberto pelo teste da API; o botão e o diálogo foram escritos mas não exercitados na tela).
- **A API ganhou dois campos para as telas:** `key_created_at` em máquinas ("Chave gerada há 10 min, ainda não usada") e `machines_total`/`machines_online` em Clientes.
- **Aba Execuções, coluna "Agora" e linha "Roda: …"** ficam para o M3 (dependem de `bots` e `jobs`); a aba na URL (`?tab=`) também, pois por ora só existe o Histórico.
- **`enroll` sem elevação** passou a explicar o que fazer (antes mostrava um traceback).
- **Erro no processo:** a pasta `.playwright-mcp/` (capturas da conferência) foi commitada por engano e removida no commit seguinte. **Decidido manter no histórico, sem force-push.** Conferido: as capturas só têm dados de uma conta temporária de dev (segredo TOTP, códigos de recuperação e uma chave de registro), e todos estão mortos: o usuário está `disabled` e sem sessão, a chave foi usada e a máquina está revogada. Nenhum cookie de sessão, CSRF, token `rga1` ou link de convite aparece nelas.

## Desvios e escolhas do M3 para a revisão

- **Conferência no navegador sem console elevado.** O `enroll` grava a chave numa pasta que só um console elevado escreve. A conferência rodou o agente real com só o passo da ACL substituído (como o teste ponta a ponta). **Falta rodar o `enroll` real em console elevado antes do primeiro cliente** (já era pendência do M2).
- **Leitor não conferido no navegador** (o cliente do seed não tem bot); vale a matriz de permissões do servidor (`test_permissions.py`).
- **Robô morto à força.** Se o agente for morto sem aviso (corte de energia), o robô que ele iniciou pode continuar rodando; a execução vira "falhou" por `machine_lost` e o robô fica por conta de quem religar a máquina. Na conferência manual o robô não ficou órfão, mas isso não está garantido; no serviço do Windows depende do empacotamento do M8.
- **Rota nova fora da spec:** `POST /agent/artifacts/{id}/uploaded` (o servidor confere o objeto no S3 antes de marcar a captura). Também novos: `current_job_id` no heartbeat e o limite de corpo de 256 KB só para `/agent/logs`.
- **Dependências novas:** `boto3` (API), `psutil` (agente), `recharts` (web) e o grupo `bots` com o `playwright` (não entra no CI).
- **`/health` agora pode responder `degraded`** (HTTP 200) quando falta a partição do mês seguinte de `job_logs`.
- **Dev:** o `seed-dev` cria o pool "Artemisys – Demonstração" e o bot de demonstração só em banco vazio; num banco antigo, o bot se cadastra pelo painel (ver `CLAUDE.md`).

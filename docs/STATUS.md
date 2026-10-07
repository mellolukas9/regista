# Status do projeto

_Atualize este arquivo ao final de cada marco ou sessão de trabalho relevante._

## Agora

- **Marco atual:** M4b — Separação de privilégios entre agente e robô (PR #6 em rascunho; antes do M5)
- **Situação:** implementado e testado (ver o registro de 2026-10-07): hospedeiro do robô com identidade própria e sem credencial, canal por named pipe com identidade conferida pelo kernel, matriz de permissões de `%ProgramData%\Regista` como código, pasta e ambiente novos por execução, instalador mínimo de serviços, `robot_host_unavailable`, robô `isolation_probe` e testes com serviços e contas de verdade na CI do Windows. **Falta a conferência manual em console elevado** (roteiro abaixo) e, depois dela, marcar a ADR 0022 como aceita, retirar o pré-requisito 1 desta página, passar o marco atual para o M5 e deixar o PR pronto (`gh pr ready`, sem merge).
- **Próximo passo:** conferência manual do M4b pelo responsável; depois o fechamento descrito acima e, após o merge, a branch `m5-queues` e o plano do M5.
- **Pendências para decisão:** ESLint 10 (depois do M3, abaixo).

## PRÉ-REQUISITOS OBRIGATÓRIOS ANTES DO PRIMEIRO CLIENTE EM PRODUÇÃO

Não são evolução opcional: o primeiro cliente não entra em produção sem os dois.

1. **O robô deve rodar com uma conta separada, de privilégio menor** (ADR 0022, proposta), que só lê o cache e o ambiente da própria versão e **não enxerga `keys\`**. Hoje o robô roda com a mesma conta do agente e, verificado no código e nas ACLs herdadas de `%ProgramData%`, consegue:
   - **(a) `packages\` e `envs\`:** alterar ou apagar os de **qualquer** versão. A adulteração do zip é detectada (o hash é conferido a cada execução), mas o ambiente em `envs\` é reaproveitado **sem reverificação**: código plantado ali persiste e roda nas próximas execuções;
   - **(b) `uv-cache\`:** alterar o cache de onde os ambientes são montados;
   - **(c) `keys\`:** **ler** `machine.key` (DPAPI com escopo de máquina e entropia fixa no código) e `identity.json`, e portanto se passar pela máquina junto ao servidor;
   - além disso: editar `agent.toml` (lista de robôs) e o arquivo `PAUSED`, e todos os usuários locais leem `packages\`, `envs\`, `logs\` e `agent.toml` (herança de `ProgramData`). Só `python\` e `browsers\` ficam somente leitura (ACL do `setup`, com teste no CI do Windows).
2. **Chaves de produção (ativa e reserva) geradas e listadas em `libs/pkg/src/regista_pkg/trusted_keys.py`.** A lista está vazia: sem chave confiável nenhum pacote roda em produção (falha segura). Procedimento, backup e roteiro de vazamento em `docs/runbooks/chave-de-assinatura.md`.

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

## Decisões aprovadas para o M4 (não perguntar de novo)

| Tema | Decisão |
|---|---|
| Dependências do robô | Wheels dentro do pacote assinado, instaladas offline com `uv` (`--no-index --require-hashes`); a máquina do cliente nunca acessa o PyPI (ADR 0021) |
| Plataforma | **Limite do MVP: pacotes só para Windows 64 bits** (`win_amd64`). Dependência que só tem sdist faz o `regista-pack` falhar apontando o pacote; sdist nunca entra. Outras plataformas ficam como evolução |
| Runtime | Python e Chromium preparados por `regista-agent setup` (elevado), versões exatas declaradas no manifesto, em `%ProgramData%\Regista\python` e `\browsers`, escrita só para Administradores e SYSTEM; a execução nunca baixa nada. **Risco aceito:** o Chromium do CDN do Playwright só tem HTTPS, sem hash verificável (evolução: MSI offline no M8) |
| Cliente no manifesto | A assinatura cobre `tenant_id`, `package_name` e versão; o agente compara com o `tenant_id` do cadastro (`keys\identity.json`). `regista-pack --client` repetível |
| Chaves | Ativa e reserva, ambas confiáveis nos agentes desde o primeiro MSI; privada cifrada com senha na máquina de build, **nunca na CI nem nos segredos do GitHub** (só chaves de teste geradas na hora). Roteiro de vazamento em `docs/runbooks/chave-de-assinatura.md` |
| Flag de dev | `REGISTA_DEV_UNSIGNED` **continua, só em dev**; o ROADMAP foi corrigido |
| Kill switch | Só local (`PAUSED`, `allow`/`disallow`); o painel mostra apenas a indicação "Pausada nesta máquina" via heartbeat (`machines.paused_locally`), sem ação de pausar ou retomar |
| Máquinas antigas | As cadastradas antes do M4 não têm o `tenant_id` local: precisam de "Gerar nova chave" e novo `enroll` |

## Pendências para o M7

- **Alerta para a equipe Artemisys quando uma execução termina com `package_invalid`.** Motivos como `signature_invalid`, `unknown_key` e `wrong_client` podem indicar adulteração (ou um servidor comprometido entregando pacote errado), e hoje só aparecem no banner da execução, que ninguém precisa abrir. O M7 (alertas e notificações) deve incluir esse evento, destinado à equipe Artemisys e não ao cliente.

## Pendências para o início do M4 (resolvidas)

- **Resolvidas no passo 0:** os testes instáveis e o teste que falhava por causa do `.env` local (ver o registro de 2026-10-07). A pendência do Chromium está decidida acima e entra nos passos 8 e 9 do plano.

Texto original das pendências, para referência:

- **Testes dependentes de tempo real.** `test_worker.py::test_two_workers_run_each_tick_once` e o e2e `test_jobs_e2e.py::test_a_machine_that_goes_away_in_the_middle_makes_the_run_machine_lost` falharam numa rodada local completa (a suíte estava sob carga: 10 min seguidos de contêineres e processos) e passaram isolados e na CI. **Investigar a causa e torná-los determinísticos (relógio e tarefas controlados pelo teste, sem esperar tempo real), não só aumentar timeouts.** Antes, reproduzir sob carga para achar o que de fato atrasa (a rodada do worker, a varredura de "Sem sinal" ou a espera do heartbeat).

- **Onde o Chromium do Playwright fica em produção.** Em dev o robô acha o navegador em `%LOCALAPPDATA%\ms-playwright` do usuário que roda o agente. Em produção o agente roda como `NT SERVICE\RegistaAgent` (modo `service`) ou como o usuário dedicado (modo `session`), e essa conta não enxerga o `%LOCALAPPDATA%` de outro usuário: o robô não acharia o navegador. O Chromium precisa ficar num local legível pela conta do agente, por exemplo `PLAYWRIGHT_BROWSERS_PATH=%ProgramData%\Regista\browsers`, com a mesma regra de permissões da pasta do agente (leitura só para a conta do agente, `SYSTEM` e `Administradores`), instalado pelo agente ou pelo instalador. **Considerar no desenho dos ambientes por versão com `uv` do M4** (de onde o navegador vem, quem o instala, como é versionado junto com o `playwright` do robô e como o `diagnose` confere). O `PLAYWRIGHT_BROWSERS_PATH` já é repassado ao robô pelo runner (`robot.py`, lista de permissão do ambiente).

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
| 2026-10-07 | M4 | Branch `m4-packages`, PR #5 em rascunho. Plano aprovado com ajustes (chave fora da CI, só wheels `win_amd64`, runbook com reserva e roteiro de vazamento, kill switch só como indicação). **Passo 0:** carga de CPU sozinha (8 e 14 processos) não reproduziu as instabilidades (16 de 16 rodadas passaram); a falha original veio de uma suíte inteira com contêineres competindo. A análise do código achou o que dependia de tempo real e o teste do worker passou a controlar o relógio (dois deferidores periódicos recebem ticks de horário fixo e a fila é esvaziada a cada tick); nos e2e o limite de "Sem sinal" subiu para 1 h e a máquina "parada" é simulada recuando o `last_seen_at`. Uma primeira versão do teste novo falhou 1 de 3 rodadas completas (sobra de um job de varredura de outro teste segurando o `queueing_lock`) e foi corrigida. Evidência: os dois testes 20 de 20 sob 14 processos de carga; 3 de 3 rodadas completas (449 passaram, 6 pulados). O teste `test_prod_accepts_the_iam_role_setup...` falhava só localmente porque o `.env` entrava nas settings do teste; os testes agora ignoram o `.env`. **Passo 1:** ADR 0021, runbook, specs e §15 proposta. |
| 2026-10-07 | M4 | M4 concluído em 13 passos (PR #5). Libs novas `regista_pkg` e `regista-pack`, migration `0005`, rotas de versões, rotas de pacote e de runtime para o agente, `release`, aba Versões. **Conferência manual com o agente real** (pilha isolada, robô de demonstração assinado): versão assinada rodou e a captura saiu; troca de versão pela tela (a execução seguinte usou a nova); pacote adulterado no S3 recusado (`hash_mismatch`); pacote assinado para outro cliente servido por um "servidor comprometido" recusado (`wrong_client`) e a publicação dele pela tela recusada pelo servidor; robô fora da lista local recusado; kill switch (execução fica pendente, "Pausada nesta máquina" no painel, retomar roda); `setup --from-server --wheels` instalou Python 3.13.1 e o Chromium 1243. Os textos de recusa conferem com a §15. |
| 2026-10-07 | M4b | Branch `m4b-robot-isolation`, PR #6 em rascunho. Plano aprovado (opção 2: agente sempre como serviço, hospedeiro do robô sem credenciais; ambiente novo por execução; instalador mínimo) com três ajustes: texto da §16, risco residual na ADR com teste de hospedeiro adulterado e teste de persistência por HKCU. **Spike:** venv por execução ~0,5 a 1 s com Playwright (limite era 5 s); o `uv` **não** reconfere o hash do cache (um item envenenado passou), então a instalação é sempre `--no-cache`; Chromium headless roda sob `NT SERVICE\RegistaRobot`; Job Object mata filho e neto; o pipe entre serviços identifica o cliente pelo token, e o hospedeiro, que não consegue abrir o processo do agente, confere o servidor pelo PID que o gerenciador de serviços informa; `PythonPath` e `Environment` do HKCU não afetam o venv. Nenhum critério de parada. **Implementação:** `layout.py` (matriz), `rundir.py`, `safefs.py`, `environment.py` (novo por execução), `hostproto.py`, `launcher.py`, `host.py`, `winpipe.py`, `winjob.py`, `winservice.py`, `service.py`, `hostlink.py`; migration `0006`; texto da §16 no painel. **Achados no caminho:** o `QueryFullProcessImageName` de um serviço sobre outro serviço falha (a checagem do executável é dispensada, não reprovada); o `python.exe` de um venv é um iniciador (o Python roda num processo filho), e mesmo assim funciona como serviço (o gerenciador de serviços acompanha o processo que se conecta a ele); por isso a checagem de executável usa a imagem do próprio processo; uma leitura do pipe que estoura o prazo não pode perder metade de um quadro (buffer persistente). |

## Desvios e escolhas do M1 para a revisão

- **Reenviar convite no último Admin ativo é permitido.** O plano previa a trava também aqui, mas é o caminho de quem esqueceu a senha (e a Artemisys sempre pode fazê-lo); a trava de último Admin vale para mudar papel e remover acesso.
- **TanStack Table v8 (8.21.3, fixada).** O `latest` do npm já é a v9, com API diferente; a v8 é a estável que a documentação do projeto assume. Migrar quando houver motivo.
- **Textos novos** (convite, trocar senha, reenviar convite, erros) estão em `design-system.md` §12; os toasts "Cliente criado. Convite enviado para …", "Convite enviado para …", "Papel salvo", "Acesso removido", "Sessões encerradas", "Sessão encerrada", "Outras sessões encerradas" e o aviso de "Todos os clientes" em Usuários foram propostos aqui e ainda não estão no documento.
- **Colunas de Máquinas, Bots e Última execução da tela Clientes** ficam para os marcos que criam esses dados; a cidade da sessão (GeoIP) ficou fora.
- **"Gerar novos códigos"** mostra os códigos em `/login/recovery-codes?from=account` (como no design), mantidos só em memória.
- **Tabela de Usuários** põe "Reenviar convite" num menu "Mais ações" para a linha ficar compacta; no mobile continua como botão.
- **Cabeçalhos:** CSP completa de scripts fica para o M8.

## Desvios e escolhas do M2 para a revisão

- **Conferência no navegador sem o `regista-agent` real.** O `enroll` grava a chave numa pasta que só um console elevado consegue escrever (por desenho), e a sessão de desenvolvimento não era elevada. A conferência usou um script que fala o mesmo protocolo (enroll, desafio, token, heartbeat) por HTTP. O agente real é coberto pelo teste ponta a ponta e, no Windows, pelo job `agent-windows` da CI (inclui "enroll por um usuário, leitura pela conta do agente"). O `enroll` real em console elevado foi testado manualmente pelo responsável, com sucesso, no M2 e no M3.
- **Não conferido no navegador:** a visão de Operador/Leitor (sem botões; coberta pela matriz de permissões do servidor) e "Gerar nova chave" numa máquina já cadastrada (coberto pelo teste da API; o botão e o diálogo foram escritos mas não exercitados na tela).
- **A API ganhou dois campos para as telas:** `key_created_at` em máquinas ("Chave gerada há 10 min, ainda não usada") e `machines_total`/`machines_online` em Clientes.
- **Aba Execuções, coluna "Agora" e linha "Roda: …"** ficam para o M3 (dependem de `bots` e `jobs`); a aba na URL (`?tab=`) também, pois por ora só existe o Histórico.
- **`enroll` sem elevação** passou a explicar o que fazer (antes mostrava um traceback).
- **Erro no processo:** a pasta `.playwright-mcp/` (capturas da conferência) foi commitada por engano e removida no commit seguinte. **Decidido manter no histórico, sem force-push.** Conferido: as capturas só têm dados de uma conta temporária de dev (segredo TOTP, códigos de recuperação e uma chave de registro), e todos estão mortos: o usuário está `disabled` e sem sessão, a chave foi usada e a máquina está revogada. Nenhum cookie de sessão, CSRF, token `rga1` ou link de convite aparece nelas.

## Desvios e escolhas do M3 para a revisão

- **Conferência no navegador sem console elevado.** O `enroll` grava a chave numa pasta que só um console elevado escreve. A conferência rodou o agente real com só o passo da ACL substituído (como o teste ponta a ponta). O `enroll` real em console elevado já foi testado manualmente pelo responsável, com sucesso (M2 e M3).
- **Leitor não conferido no navegador** (o cliente do seed não tem bot); vale a matriz de permissões do servidor (`test_permissions.py`).
- **Robô morto à força.** Se o agente for morto sem aviso (corte de energia), o robô que ele iniciou pode continuar rodando; a execução vira "falhou" por `machine_lost` e o robô fica por conta de quem religar a máquina. Na conferência manual o robô não ficou órfão, mas isso não está garantido; no serviço do Windows depende do empacotamento do M8.
- **Rota nova fora da spec:** `POST /agent/artifacts/{id}/uploaded` (o servidor confere o objeto no S3 antes de marcar a captura). Também novos: `current_job_id` no heartbeat e o limite de corpo de 256 KB só para `/agent/logs`.
- **Dependências novas:** `boto3` (API), `psutil` (agente), `recharts` (web) e o grupo `bots` com o `playwright` (não entra no CI).
- **`/health` agora pode responder `degraded`** (HTTP 200) quando falta a partição do mês seguinte de `job_logs`.
- **Dev:** o `seed-dev` cria o pool "Artemisys – Demonstração" e o bot de demonstração só em banco vazio; num banco antigo, o bot se cadastra pelo painel (ver `CLAUDE.md`).

## Desvios e escolhas do M4 para a revisão

- **Estrutura.** Duas libs novas no workspace, fora do plano original: `libs/pkg` (`regista_pkg`: formato, mensagem assinada, verificação, extração segura e a lista de chaves confiáveis; API, agente e `regista-pack` importam a mesma implementação, então não podem divergir) e `tools/pack` (`regista-pack`). A lista de chaves confiáveis é uma só (a API e o agente leem `regista_pkg.trusted_keys`).
- **Achados da conferência manual e da CI, corrigidos:**
  - uma execução "atribuída" a um agente que morreu (long poll respondido depois da queda; falha antiga do M3) ficava presa: o heartbeat agora devolve à fila a execução atribuída há mais de 60 s (`assigned_orphan_seconds`) que o agente não reconhece;
  - pausa durante um long poll: nova rota `POST /agent/jobs/{id}/release`, e o agente devolve a execução;
  - uma nova tentativa de upload substitui a anterior incompleta (antes dava "versão já publicada");
  - `setup`: `--wheels` (Playwright a partir das wheels de um pacote, sem PyPI), proxy e CA repassados ao `uv`, pasta do ambiente com 20 caracteres do hash (limite de 260 caracteres do Windows);
  - o SeaweedFS do teste ficava sem volumes depois de ~60 buckets e devolvia `InternalError`; o limite foi aumentado. Duas asserções de listagem dependiam do tamanho do banco compartilhado.
- **Publicação nos testes ponta a ponta** é feita direto no banco e no S3 (as rotas têm testes próprios): o objetivo é provar o que o agente faz com o que o servidor entrega, inclusive um servidor que mente.
- **Máquinas cadastradas antes do M4** não têm o `identity.json`: o `diagnose` avisa e é preciso "Gerar nova chave" e novo `enroll`.
- **Máquina de build:** o `regista-pack build` precisa de acesso ao PyPI (resolve o lockfile e baixa as wheels); o cliente nunca precisa.
- **Limites aceitos:** pacotes só para Windows 64 bits (`win_amd64`); o Chromium do CDN do Playwright só tem HTTPS, sem hash verificável (saída: MSI offline do M8); o ambiente reaproveitado por versão pode ser alterado por um robô comprometido (ver os pré-requisitos acima e a ADR 0022).
- **CORS do bucket:** em dev a API configura o CORS do bucket para a origem do painel (o SeaweedFS suporta); em produção a regra do bucket é da infraestrutura (M8). O CORS só é aplicado quando a API sobe: com o S3 fora do ar nessa hora, é preciso reiniciar a API.
- **Capturas de tela no repositório.** As capturas da conferência manual (`m4-*.png`) entraram por engano num commit e foram removidas no seguinte; ficam no histórico, sem force-push (como no M2). Contêm só a interface de uma pilha descartável de dev; nenhuma senha, chave ou token.
- **Não conferido no navegador:** a visão de Operador e Leitor da aba Versões (sem botões; coberta pela matriz de permissões do servidor) e a tela mobile (390 px) da aba.

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
# infraestrutura local (Postgres 18 e o S3 local SeaweedFS, ADR 0019)
docker compose -f infra/compose/docker-compose.dev.yml up -d

# backend / sdk / agente (workspace uv na raiz)
uv sync
uv run alembic -c apps/api/alembic.ini upgrade head
uv run uvicorn regista_api.main:app --reload --port 8000
uv run regista-worker   # tarefas internas (marca máquinas sem sinal, limpa rate limit); 2º terminal
uv run pytest        # os testes de banco sobem o próprio Postgres (Testcontainers); só exigem o Docker ligado
uv run ruff check . ; uv run ruff format --check . ; uv run mypy

# frontend (o proxy /api/* -> http://127.0.0.1:8000 vem de API_URL; padrão já serve para dev)
cd apps/web ; npm install ; npm run dev ; npm run lint ; npm run typecheck ; npm run build

# git hooks (uma vez por clone)
uv run pre-commit install
```

**Três terminais em desenvolvimento:** API (`uvicorn`), worker (`regista-worker`, sem ele ninguém vira "Sem sinal", as execuções de máquina perdida não terminam e as partições dos logs não são criadas) e web (`npm run dev`). Para rodar robôs, um quarto: o agente em modo de desenvolvimento (abaixo).

**Agente (`regista-agent`).** No painel, cadastre a máquina (Máquinas, "Cadastrar máquina") e copie a chave de registro (aparece uma vez). Em dev, `REGISTA_HOME` aponta o agente para uma pasta própria (configuração, chave e logs) em vez de `%ProgramData%\Regista`. **Desde o M4b (ADR 0022) o robô nunca roda com a conta do agente em produção**: quem o inicia é o hospedeiro do robô (`regista-agent host`, serviço `RegistaRobot`). Em desenvolvimento, `REGISTA_DEV_DIRECT_ROBOT=1` (com `REGISTA_ENVIRONMENT=dev`; recusado em produção) mantém o robô como filho do agente, como era:

```powershell
$env:REGISTA_HOME = "$env:TEMP\regista-agent-dev"
uv run regista-agent enroll --url http://127.0.0.1:8000 --key rgk_...   # PowerShell ELEVADO (Administrador)
uv run regista-agent run        # laço de sinal; Ctrl+C para; sai com código 3 se a máquina for revogada
uv run regista-agent diagnose   # conexão, proxy, TLS, horário, permissões da chave
```

O `enroll` grava a chave numa pasta restrita a SYSTEM, Administradores e à conta do agente (`--agent-account`; ver `agent/README.md`), então um console sem elevação recebe "Sem permissão para gravar a chave". **VMs são cadastradas depois de clonadas.**

**S3 local (capturas de tela).** O `docker compose ... up -d` sobe também o SeaweedFS (porta 8333, credenciais só de dev em `infra/compose/seaweedfs/s3.json`). A API precisa das variáveis `REGISTA_S3_*` do `.env.example`; num `.env` antigo, copie o bloco "S3 local" do `.env.example`. Em produção a API e o worker se recusam a subir com chave de acesso ou endpoint local (só role IAM).

**Atualizar um banco de dev que já existe (sem recriar).** Depois de baixar o M3 ou o M4, aplique as migrations novas (a `0004` cria `bots`, `jobs`, `job_logs`, `artifacts`; a `0005` cria `bot_versions`), com o Postgres ligado:

```powershell
docker compose -f infra/compose/docker-compose.dev.yml up -d
uv run alembic -c apps/api/alembic.ini upgrade head
```

Os dados que já estão no banco ficam. O `seed-dev` só roda em banco vazio, então o bot de demonstração não aparece sozinho num banco antigo: cadastre-o pelo painel. Entre como equipe Artemisys (`equipe@artemisys.example.com`), escolha o cliente na barra lateral (por exemplo "Artemisys (demonstração)"), crie um pool em **Máquinas** se ainda não houver, e em **Bots** clique em **Cadastrar bot** com nome `Busca na Wikipédia`, pacote `demo_busca_wikipedia` e o pool. Só a equipe Artemisys cadastra bots, e o nome do pacote tem de ser igual ao nome da pasta em `bots/`.

**Rodar um robô de ponta a ponta (desenvolvimento).** Uma vez: `uv sync --group bots` e `uv run playwright install chromium` (o robô de demonstração usa o Playwright do próprio workspace; os ambientes por versão com `uv` são do M4). Cadastre a máquina no painel (como acima) e rode o agente com a flag de desenvolvimento, que o agente **recusa em produção**:

```powershell
$env:REGISTA_HOME = "$env:TEMP\regista-agent-dev"
$env:REGISTA_ENVIRONMENT = "dev"
$env:REGISTA_DEV_UNSIGNED = "1"
$env:REGISTA_DEV_DIRECT_ROBOT = "1"   # sem hospedeiro (só em dev)
$env:REGISTA_DEV_BOTS_DIR = "$PWD\bots"
uv run regista-agent enroll --url http://127.0.0.1:8000 --key rgk_...   # PowerShell ELEVADO, só na primeira vez
uv run regista-agent run
```

Em **Bots**, **Executar agora**: o painel mostra estados, logs e a captura de tela, atualizando sozinho. Mais opções do agente em `agent/README.md`.

**Agente como serviço, com o hospedeiro do robô (M4b).** Em um PowerShell **elevado**, com o programa instalado em uma pasta só de administradores (veja o roteiro em `docs/runbooks/implantacao-cliente.md`; em desenvolvimento acrescente `--allow-insecure-path`):

```powershell
regista-agent enroll --url https://<regista> --key rgk_...     # modo Sessão: --robot-account <usuário dedicado>
regista-agent setup --from-server      # grava as permissões das pastas, instala Python e Chromium
regista-agent allow isolation_probe
regista-agent service install --start  # serviços RegistaAgent e RegistaRobot (ou a tarefa de logon, no modo Sessão)
regista-agent service status
regista-agent diagnose
regista-agent service uninstall
```

`bots/isolation_probe` é um robô inofensivo que **tenta** ler a chave, alterar a configuração, o kill switch, os pacotes e a própria versão (tudo tem de ser NEGADO) e roda normalmente; serve à CI do Windows e à conferência manual. Os testes com serviços de verdade (`agent/tests/test_windows_host.py`) só rodam com `REGISTA_TEST_REAL_SERVICES=1` em console elevado (o job `agent-windows` da CI).

**Pacotes assinados (M4).** Em produção o agente só roda pacotes assinados. O `regista-pack` roda na máquina de build da Artemisys (precisa do PyPI); a chave de assinatura **nunca** vai para o repositório, o servidor ou a CI (ADR 0021 e `docs/runbooks/chave-de-assinatura.md`):

```powershell
# uma vez: gera a chave (cifrada com senha, FORA do repositório); a pública vai para regista_pkg/trusted_keys.py
uv run regista-pack keygen --out $env:USERPROFILE\.regista\signing --name ativa
# por versão: um pacote assinado por cliente (--client pode repetir); o Python é exato (X.Y.Z)
uv run regista-pack build bots\demo_busca_wikipedia --version 1.0.0 --client <id-do-cliente> --key $env:USERPROFILE\.regista\signing\ativa.rgkey --python 3.13.1 --out dist
uv run regista-pack verify dist\<pacote>.rgpkg dist\<pacote>.rgsig --keys <chave-publica>.json   # confere hash, assinatura e conteúdo
```

O robô declara as dependências em `requirements.txt` (a pasta do robô é o nome do pacote). A equipe Artemisys publica pelo painel: **Bots**, o bot, aba **Versões**, **Publicar versão** (arquivos `.rgpkg` e `.rgsig`). Em dev, a API e o agente só confiam em chaves extras com `REGISTA_DEV_TRUSTED_KEYS` (arquivo JSON com a chave pública, formato do `<chave>.pub.json`); em produção essa variável é recusada.

Na máquina do agente (comandos de `regista-agent`; em produção, console **elevado**):

```powershell
uv run regista-agent setup --from-server [--wheels <pasta de wheels>]   # Python e Chromium exatos que os bots do pool pedem
uv run regista-agent allow demo_busca_wikipedia      # lista local de robôs permitidos (vazia = nada roda)
uv run regista-agent disallow demo_busca_wikipedia
uv run regista-agent pause                           # kill switch local; `resume` desliga (o painel só mostra "Pausada nesta máquina")
uv run regista-agent diagnose                        # chaves confiáveis, cliente, lista, kill switch, runtimes
```

Em dev, `setup`, `allow` e `pause` não exigem console elevado com `REGISTA_ENVIRONMENT=dev`. Para rodar um robô **assinado** de ponta a ponta em dev sem instalar os serviços (com `REGISTA_DEV_DIRECT_ROBOT=1`): gere a chave de teste, faça o `build` para o cliente, publique pelo painel, rode `setup --from-server` (ou aponte `REGISTA_DEV_PYTHON` e `REGISTA_DEV_BROWSERS_PATH` para um Python e uma pasta de navegadores existentes) e `allow` do pacote. A flag `REGISTA_DEV_UNSIGNED` continua existindo, só em dev, para robôs de uma pasta sem versão.

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

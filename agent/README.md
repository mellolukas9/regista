# regista-agent

Agente do Regista: roda no ambiente do cliente e só faz conexões de saída (HTTPS) para a API.

## Comandos

```powershell
uv run regista-agent enroll --url https://regista.exemplo.com --key rgk_... [--robot-account USUARIO] [--force]
uv run regista-agent run [--mode service|session]   # --mode só confirma o modo do cadastro
uv run regista-agent service install|uninstall|status   # serviços RegistaAgent e RegistaRobot (elevado)
uv run regista-agent host [--mode service|session]      # hospedeiro do robô (o serviço/tarefa roda isto)
uv run regista-agent diagnose
uv run regista-agent setup --from-server [--wheels PASTA]   # Python e Chromium exatos (console elevado)
uv run regista-agent allow|disallow PACOTE                   # lista local de robôs permitidos (console elevado)
uv run regista-agent pause|resume                            # kill switch local (console elevado)
```

Em desenvolvimento, `REGISTA_HOME` aponta para uma pasta própria (configuração, chave e logs), para não usar `%ProgramData%\Regista`.

## Robôs: pacotes assinados (M4)

Em produção o agente só roda **pacotes assinados** pela Artemisys (ADR 0021). Para cada execução, nesta ordem:

1. kill switch local (arquivo `PAUSED`): ligado, o agente não pede execução; a lista local `allowed_bots` (em `agent.toml`): vazia ou sem o robô, a execução falha com `robot_not_allowed`;
2. baixa o pacote por uma URL pré-assinada (cache em `packages\<pacote>\<sha256>.rgpkg`), confere o **hash e a assinatura antes de abrir o zip**, e confere que o `tenant_id` do manifesto é o do cadastro (`keys\identity.json`), o nome do pacote e a versão. Pacote de outro cliente é recusado mesmo que o servidor o envie. Recusa: `package_invalid`, com um motivo de lista fechada (`hash_mismatch`, `signature_invalid`, `unknown_key`, `wrong_client`, `wrong_package`, `wrong_version`, `unsafe_archive`, `too_large`, `malformed_package`) e o detalhe só nos logs da execução;
3. extrai entrada por entrada (zip slip, links, nomes do Windows, bombas) na pasta da execução (`runs\<id>\build`), de onde só o código da versão é copiado para `package\`;
4. confere o runtime (Python exato e Chromium que o manifesto declara, em `python\` e `browsers\`): faltando, `runtime_missing`; **o agente nunca baixa nada ao executar**;
5. cria um **ambiente novo para esta execução** (`runs\<id>\venv`) com `uv`, **offline**, a partir das wheels do pacote (`--offline --no-index --require-hashes --no-cache --link-mode=copy`); falha: `environment_failed`;
6. pede ao **hospedeiro do robô** que o execute; sem hospedeiro: `robot_host_unavailable`. A pasta da execução é apagada sempre.

`regista-agent setup` (console elevado) prepara a máquina: instala as versões exatas pedidas (`--python X.Y.Z --playwright X.Y.Z`, ou `--from-server` para ler o que os bots do pool pedem), com escrita só para Administradores e SYSTEM em `python\` e `browsers\`. `--wheels PASTA` instala o Playwright das wheels de um pacote, sem acessar o PyPI. O hash do Python é conferido pelo `uv`; o Chromium vem do CDN do Playwright só por HTTPS (risco aceito; saída: MSI offline do M8). O `diagnose` mostra as chaves em que o agente confia, o cliente, a lista local, o kill switch e os runtimes (inclusive os que faltam para as versões em uso do pool).

## O robô não roda com a conta do agente (M4b, ADR 0022)

No Windows o agente é **sempre um serviço** (`NT SERVICE\RegistaAgent`), o único com acesso a `keys\`, `agent.toml` e `PAUSED` (os dois últimos só leitura). O robô é iniciado pelo **hospedeiro** (`regista-agent host`), com identidade própria e sem credencial alguma:

| Modo | Onde o hospedeiro roda | Conta do robô |
|---|---|---|
| `service` | serviço `RegistaRobot` | `NT SERVICE\RegistaRobot` (virtual, sem senha) |
| `session` | tarefa de logon `Regista\RobotHost` do usuário dedicado, na sessão dele | o usuário dedicado (`--robot-account`), que não pode ser administrador |

Agente e hospedeiro falam por um named pipe criado pelo agente (instância única, só o hospedeiro abre, identidade conferida pelo kernel nos dois sentidos, protocolo de mão única). A matriz de permissões de `%ProgramData%\Regista` (quem lê e escreve o quê) está em `docs/specs/security.md` e é gravada pelo `setup`. Instalar: `setup --from-server`, depois `service install --start` (console elevado, programa em pasta só de administradores; em desenvolvimento `--allow-insecure-path`). `diagnose` confere serviços, contas, pipe e permissões. Sem hospedeiro a execução falha com `robot_host_unavailable`; nunca há fallback para a conta do agente. Em desenvolvimento, `REGISTA_DEV_DIRECT_ROBOT=1` (com `REGISTA_ENVIRONMENT=dev`; recusado em produção) mantém o robô como filho do agente.

## Rodar robôs em desenvolvimento (M3)

Para robôs de uma pasta local sem versão, **só em desenvolvimento** (em produção o agente recusa iniciar com a flag e não roda nada sem versão):

```powershell
$env:REGISTA_HOME = "$env:TEMP\regista-agent-dev"
$env:REGISTA_ENVIRONMENT = "dev"          # sem isto o agente assume produção
$env:REGISTA_DEV_UNSIGNED = "1"           # em produção o agente recusa iniciar com isto ligado
$env:REGISTA_DEV_BOTS_DIR = "$PWD\bots"   # <pasta>/<nome do pacote>/main.py
uv run regista-agent run
```

Outros ajustes (todos `REGISTA_*`): `DEV_PYTHON` (interpretador do robô; padrão o do próprio agente), `JOB_PRIORITY` (`below_normal` ou `normal`), `CANCEL_GRACE_SECONDS` (15), `LOG_FLUSH_SECONDS` (2), `POLL_WAIT_SECONDS` (30).

O robô roda como processo filho, sem shell, com um ambiente mínimo (nenhum token nem variável do agente), numa pasta temporária por execução. Cancelar ou passar do prazo para o robô e tudo que ele iniciou. Limite conhecido: se o agente for morto à força (corte de energia), o robô que ele iniciou pode ficar rodando até alguém encerrá-lo; a execução vira "falhou" por `machine_lost`.

Playwright e navegador (uma vez, na raiz do repositório): `uv sync --group bots` e `uv run playwright install chromium`.

## Máquinas clonadas

> **VMs são cadastradas depois de clonadas.** A imagem-modelo não pode ter um agente cadastrado (rode o `sysprep` antes de clonar). O `enroll` recusa sobrescrever uma identidade existente sem `--force`.

## Chave privada e conta do agente

O par Ed25519 é gerado na máquina e a chave privada nunca sai dela. Fica em `<home>\keys\machine.key`:

- **Windows:** protegida por DPAPI (escopo da máquina, com entropia fixa do Regista) e pela ACL da pasta `keys`, com herança desligada.
- **Linux:** arquivo `0600` em pasta `0700`, com dono igual à conta do agente.

Quem pode ler a chave:

| Conta | Acesso |
|---|---|
| Conta que **roda o agente** | leitura |
| `SYSTEM` | total |
| `Administradores` | total |
| Quem rodou o `enroll` | **nenhum** (a menos que seja Administrador) |
| `Users`, `Authenticated Users`, `Everyone` | nenhum |

A conta do agente depende do modo:

| Modo | Conta do agente | `--agent-account` |
|---|---|---|
| `service` | `NT SERVICE\RegistaAgent` (conta virtual do serviço) | opcional; esse é o padrão |
| `session` | `NT SERVICE\RegistaAgent` também (desde o M4b); o usuário dedicado é a **conta do robô** (`--robot-account`, obrigatório) | opcional; só muda em testes |

O `enroll` roda como Administrador porque grava em `%ProgramData%` e define a ACL; mesmo assim, o acesso vai para a conta do agente, nunca para quem executou o comando. A conta fica gravada em `agent.toml`.

`regista-agent diagnose` reprova (erro) se a conta do agente não consegue ler a chave, se a herança estiver ligada ou se qualquer outra conta (fora `SYSTEM` e `Administradores`) tiver acesso.

**Limite aceito:** um administrador da máquina consegue extrair a chave. A evolução prevista é guardá-la no TPM, sem mudar o resto do agente.

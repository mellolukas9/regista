# regista-agent

Agente do Regista: roda no ambiente do cliente e só faz conexões de saída (HTTPS) para a API.

## Comandos

```powershell
uv run regista-agent enroll --url https://regista.exemplo.com --key rgk_... [--agent-account CONTA] [--force]
uv run regista-agent run [--mode service|session]   # --mode só confirma o modo do cadastro
uv run regista-agent diagnose
```

Em desenvolvimento, `REGISTA_HOME` aponta para uma pasta própria (configuração, chave e logs), para não usar `%ProgramData%\Regista`.

## Rodar robôs em desenvolvimento (M3)

Antes dos pacotes assinados (M4), o agente só executa robôs de uma pasta local, e **só em desenvolvimento**:

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
| `session` | o usuário dedicado do Windows que fica logado | **obrigatório** |

O `enroll` roda como Administrador porque grava em `%ProgramData%` e define a ACL; mesmo assim, o acesso vai para a conta do agente, nunca para quem executou o comando. A conta fica gravada em `agent.toml`.

`regista-agent diagnose` reprova (erro) se a conta do agente não consegue ler a chave, se a herança estiver ligada ou se qualquer outra conta (fora `SYSTEM` e `Administradores`) tiver acesso.

**Limite aceito:** um administrador da máquina consegue extrair a chave. A evolução prevista é guardá-la no TPM, sem mudar o resto do agente.

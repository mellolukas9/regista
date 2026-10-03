# regista-agent

Agente do Regista: roda no ambiente do cliente e só faz conexões de saída (HTTPS) para a API.

## Comandos

```powershell
uv run regista-agent enroll --url https://regista.exemplo.com --key rgk_... [--agent-account CONTA] [--force]
uv run regista-agent run [--mode service|session]   # --mode só confirma o modo do cadastro
uv run regista-agent diagnose
```

Em desenvolvimento, `REGISTA_HOME` aponta para uma pasta própria (configuração, chave e logs), para não usar `%ProgramData%\Regista`.

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

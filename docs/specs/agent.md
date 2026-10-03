# Agente Regista

Único componente instalado no ambiente do cliente. Não abre portas, não aceita comandos arbitrários e só executa pacotes assinados pela Artemisys.

## Cadastro de uma máquina

1. Administrador cria a máquina no painel, dentro de um pool.
2. A API gera uma **chave de registro de uso único** (exibida uma vez; banco guarda só o hash; expira em 24h).
3. Na máquina, em um console **elevado** (Administrador no Windows): `regista-agent enroll --url https://<regista> --key <chave> [--agent-account <conta>]`. O `enroll` recusa sobrescrever uma identidade existente sem `--force`.
4. O agente gera um par **Ed25519 localmente** e envia a chave pública com a chave de registro.
5. A API valida, queima a chave de registro, grava a chave pública e responde com `machine_id`.
6. Daqui em diante, para obter acesso: o agente pede um desafio (nonce), assina com a chave privada e troca por um **token de acesso de 15 minutos** com escopo `machine_id` + `tenant_id`.

A chave privada nunca sai da máquina. Revogar a máquina no painel invalida novos tokens imediatamente e os existentes na próxima verificação (a API checa `revoked_at` em toda requisição de agente); a execução em andamento nela é cancelada. Sem sinal por 2 minutos, a máquina fica `offline`. O modo (`service` ou `session`) é escolhido no cadastro da máquina e aparece no detalhe.

**VMs clonadas:** o cadastro deve acontecer depois da clonagem. Uma imagem-modelo nunca contém agente cadastrado (no Windows, rodar sysprep antes de clonar).

## Protocolo

Todas as chamadas são HTTPS iniciadas pelo agente. Corpo JSON. Limites de tamanho em todas as entradas.

| Chamada | Uso |
|---|---|
| `POST /agent/enroll` | Cadastro: chave de registro + chave pública + informações da máquina |
| `POST /agent/challenge` | Obter nonce |
| `POST /agent/token` | Nonce assinado → token de acesso curto |
| `POST /agent/heartbeat` | Status, versão, recursos livres; resposta inclui cancelamentos pendentes |
| `GET /agent/jobs/next?wait=30` | Long-polling por job do pool da máquina |
| `POST /agent/jobs/{id}/start` | Robô iniciou |
| `POST /agent/jobs/{id}/complete` | Sucesso |
| `POST /agent/jobs/{id}/fail` | Falha com código e mensagem mascarada |
| `GET /agent/packages/{bot_version_id}` | Pacote + sha256 + assinatura (M4) |
| `GET /agent/jobs/{id}/secrets` | Segredos do job, uma única vez, somente durante a execução |
| `POST /agent/logs` | Logs em lote (com `item_id` quando houver) |
| `POST /agent/artifacts/presign` | URL pré-assinada para upload direto ao S3 |

O robô (via SDK) usa um **token de job**, derivado pelo agente para aquele job, com escopo restrito às filas e ao lote do job:

`POST /queues/{name}/items/next`, `POST /items/{id}/renew`, `/success`, `/fail`, `/release`, `POST /batches`, `POST /batches/{id}/items`, `/reject`, `/close`.

## Modos de execução

| Modo | Como roda | Para quê |
|---|---|---|
| `service` | Serviço Windows (WinSW) ou systemd, com usuário dedicado sem privilégio de admin | Robôs web headless; roda em segundo plano |
| `session` | Inicia no login de um usuário dedicado (Agendador de Tarefas "ao fazer logon"); login automático e tela sem bloqueio | Robôs que precisam de janela visível ou aplicativo desktop |
| `oneshot` | Pega um job, executa, encerra | Containers e execução elástica (fora do MVP; manter o código preparado) |

Serviços Windows rodam na sessão 0, sem área de trabalho: headless funciona, automação de janelas não. Por isso `session` existe desde o M2.

Configuração por arquivo (`%ProgramData%\Regista\agent.toml` no Windows) com sobreposição por variáveis de ambiente `REGISTA_*`. Nada específico de Windows no núcleo; diferenças isoladas em `modes/`.

## Execução de um robô

1. Recebe job com `bot_version_id` e parâmetros. Nunca um comando de shell.
2. Confere a lista local de robôs permitidos (`policy.py`) e o kill switch local.
3. Baixa o pacote (cache local por versão) e verifica sha256 e assinatura Ed25519 com a chave pública **embutida no agente**.
4. Cria ou reaproveita o ambiente da versão com `uv` (`uv venv` + `uv pip install` a partir do pacote/lockfile).
5. Busca os segredos do job e entrega ao processo do robô por variáveis de ambiente do processo filho (nunca em disco).
6. Executa o robô como processo filho com token de job, prioridade configurável (padrão abaixo do normal em estações de trabalho) e timeout.
7. Encaminha logs e artefatos; reporta resultado; apaga temporários.

No M3, antes da assinatura existir, o runner aceita uma pasta local apenas com `REGISTA_DEV_UNSIGNED=1`; essa opção é proibida em configuração de produção a partir do M4.

## Operação

- `regista-agent diagnose`: conexão, resolução de DNS, proxy, certificado TLS, horário do sistema, permissões, Python/uv disponíveis.
- Suporte a proxy corporativo (configuração e variáveis padrão `HTTPS_PROXY`).
- Log local rotativo do agente e registro local do que foi executado (robô, versão, horário, resultado) para auditoria do TI do cliente.
- Kill switch local: arquivo/flag que suspende novas execuções sem depender do painel.
- Autoatualização assinada (M8): o agente baixa nova versão de si mesmo, valida assinatura e troca.
- Armazenamento da chave privada (ADR 0018): no Windows, DPAPI com escopo da máquina e entropia fixa, em `%ProgramData%\Regista\keys\` com herança desligada e acesso só para a conta que roda o agente (`--agent-account`; padrão `NT SERVICE\RegistaAgent` no modo `service`, obrigatória no modo `session`), `SYSTEM` e `Administradores`, nunca para quem rodou o `enroll`. Limite aceito: um administrador da máquina extrai a chave; a evolução é o TPM. O `diagnose` confere a ACL. Detalhes em `agent/README.md`.
- Renovação do token e backoff usam relógio monotônico, não o horário do sistema.

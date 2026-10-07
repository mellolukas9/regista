# Agente Regista

Único componente instalado no ambiente do cliente. Não abre portas, não aceita comandos arbitrários e só executa pacotes assinados pela Artemisys.

## Cadastro de uma máquina

1. Administrador cria a máquina no painel, dentro de um pool.
2. A API gera uma **chave de registro de uso único** (exibida uma vez; banco guarda só o hash; expira em 24h).
3. Na máquina, em um console **elevado** (Administrador no Windows): `regista-agent enroll --url https://<regista> --key <chave> [--agent-account <conta>]`. O `enroll` recusa sobrescrever uma identidade existente sem `--force`.
4. O agente gera um par **Ed25519 localmente** e envia a chave pública com a chave de registro.
5. A API valida, queima a chave de registro, grava a chave pública e responde com `machine_id` e `tenant_id`. O agente grava o `tenant_id` em `keys\identity.json` (mesma ACL da chave privada): é com ele que confere de qual cliente é cada pacote (M4).
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
| `POST /agent/heartbeat` | Status, versão, recursos livres e `current_job_id`; resposta inclui `cancellations` (execuções desta máquina que devem parar) |
| `GET /agent/jobs/next?wait=30` | Long-polling por job do pool da máquina |
| `POST /agent/jobs/{id}/start` | Robô iniciou |
| `POST /agent/jobs/{id}/release` | Devolve à fila uma execução já atribuída que o agente não vai iniciar (o kill switch local ligou durante o long-polling); com cancelamento pedido, ela termina `cancelled` (M4) |
| `POST /agent/jobs/{id}/complete` | Sucesso |
| `POST /agent/jobs/{id}/fail` | Falha com código e mensagem mascarada; no `package_invalid`, também um `reason` de lista fechada (validado no servidor). O painel mostra texto fixo por código e motivo; a mensagem livre só vai nos logs da execução |
| `GET /agent/packages/{bot_version_id}` | Manifesto, sha256, assinatura, `key_id` e uma URL pré-assinada de GET (120 s) do pacote (M4). Só de uma versão que está numa execução `assigned`/`running` desta máquina; senão 404 |
| `GET /agent/runtimes` | Runtimes (Python, Playwright, revisão do Chromium) das versões em uso dos bots do pool da máquina; alimenta `setup --from-server` e o `diagnose` (M4) |
| `GET /agent/jobs/{id}/secrets` | Segredos do job, uma única vez, somente durante a execução |
| `POST /agent/logs` | Logs em lote (com `item_id` quando houver) |
| `POST /agent/artifacts/presign` | URL pré-assinada para upload direto ao S3 |
| `POST /agent/artifacts/{id}/uploaded` | Confirma o upload; o servidor confere o objeto no S3 |

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
2. Confere o kill switch local (se ligado, o agente nem pede execução) e a lista local de robôs permitidos (`policy.py`; lista vazia não roda nada). Fora da lista: `robot_not_allowed`.
3. Baixa o pacote (cache local por versão) e verifica sha256 e assinatura Ed25519 com as chaves públicas **embutidas no agente** (mais de uma, por `key_id`), **antes de abrir o zip**. Depois confere que o `tenant_id` do manifesto é o gravado no cadastro (`keys\identity.json`, nunca um valor da resposta da execução), que `package_name` é o do bot e que a versão é a da execução. Qualquer falha: `package_invalid`, com o motivo. Extrai com proteção contra caminhos maliciosos (caminho absoluto, `..`, nome reservado do Windows, link simbólico, duplicata ignorando maiúsculas, limites de arquivos e de tamanho).
4. Confere o runtime que o manifesto declara (Python, Playwright, Chromium) em `%ProgramData%\Regista\python` e `\browsers`; se faltar, `runtime_missing` e o agente **não baixa nada**. Cria ou reaproveita o ambiente da versão com `uv`, **offline**, a partir de `wheels/` e `requirements.lock` do pacote (falha: `environment_failed`). Versões antigas são limpas (mantêm-se as 3 mais recentes por pacote).
5. Busca os segredos do job e entrega ao processo do robô por variáveis de ambiente do processo filho (nunca em disco).
6. Executa o robô como processo filho com token de job, prioridade configurável (padrão abaixo do normal em estações de trabalho) e timeout.
7. Encaminha logs e artefatos; reporta resultado; apaga temporários.

Em dev (a flag continua existindo, só em dev), o runner aceita uma pasta local (`<REGISTA_DEV_BOTS_DIR>/<package_name>/main.py`) apenas com `REGISTA_DEV_UNSIGNED=1` **e** `REGISTA_ENVIRONMENT=dev`. O agente assume `prod` quando a variável falta, e em `prod` com a flag ligada se recusa a iniciar (e o servidor recusa criar execução em `prod` enquanto não houver versão assinada). O robô roda como `python -u main.py` sem shell, com ambiente por lista de permissão (nenhum token nem variável `REGISTA_*` do agente), pasta temporária por execução, prioridade abaixo do normal e prazo máximo; em dev usa o Python do workspace (`uv sync --group bots` e `uv run playwright install chromium`). Em produção a flag é recusada e o servidor não cria execução de bot sem versão em uso.

### Runtime da máquina (M4)

`regista-agent setup` (console elevado) instala as versões **exatas** de Python e Chromium que os pacotes pedem, em `%ProgramData%\Regista\python` e `%ProgramData%\Regista\browsers` (várias revisões lado a lado), com escrita só para Administradores e SYSTEM e leitura e execução para a conta do agente. Pode receber as versões por argumento ou ler as dos bots do pool (`--from-server`). O hash do Python é conferido; o Chromium vem do CDN do Playwright só por HTTPS (risco aceito, ADR 0021). O robô recebe `PLAYWRIGHT_BROWSERS_PATH` apontando para essa pasta, definido pelo agente. A execução nunca baixa runtime.

## Operação

- `regista-agent diagnose`: conexão, resolução de DNS, proxy, certificado TLS, horário do sistema, permissões, Python/uv disponíveis.
- Suporte a proxy corporativo (configuração e variáveis padrão `HTTPS_PROXY`).
- Log local rotativo do agente e registro local do que foi executado (robô, versão, horário, resultado) para auditoria do TI do cliente.
- Lista local e kill switch (M4): `allowed_bots` em `agent.toml` e o arquivo `PAUSED` na pasta do agente. `regista-agent allow|disallow <pacote>` e `pause|resume` exigem console elevado. O controle é **só local**: o painel não pausa nem retoma; o heartbeat leva `paused` apenas como indicação ("Pausada nesta máquina"). O `diagnose` mostra chaves confiáveis, `tenant_id`, lista, kill switch e runtimes.
- Autoatualização assinada (M8): o agente baixa nova versão de si mesmo, valida assinatura e troca.
- Armazenamento da chave privada (ADR 0018): no Windows, DPAPI com escopo da máquina e entropia fixa, em `%ProgramData%\Regista\keys\` com herança desligada e acesso só para a conta que roda o agente (`--agent-account`; padrão `NT SERVICE\RegistaAgent` no modo `service`, obrigatória no modo `session`), `SYSTEM` e `Administradores`, nunca para quem rodou o `enroll`. Limite aceito: um administrador da máquina extrai a chave; a evolução é o TPM. O `diagnose` confere a ACL. Detalhes em `agent/README.md`.
- Renovação do token e backoff usam relógio monotônico, não o horário do sistema.

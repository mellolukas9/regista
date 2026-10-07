# ADR 0022: Separação de privilégios entre agente e robô (hospedeiro do robô)

- **Status:** proposta (vira aceita ao fim do M4b, depois da conferência manual)
- **Data:** 2026-10

## Contexto

O robô roda como processo filho do agente, **com a mesma conta** (`NT SERVICE\RegistaAgent` no modo `service`, o usuário dedicado no modo `session`). Isso foi aceito no M3 e mantido no M4, mas o M4 deixou claro o que significa. Verificado no código e nas ACLs herdadas do Windows (`%ProgramData%` dá leitura a todos os usuários e controle total ao criador):

| Recurso | O que o robô consegue hoje |
|---|---|
| `packages\` (zips em cache) | Alterar ou apagar o zip de **qualquer** versão. A adulteração é detectada (o hash é conferido a cada execução e o arquivo é baixado de novo), mas vira indisponibilidade. |
| `envs\` (ambiente por versão) | Alterar o ambiente de **qualquer** versão (por exemplo, plantar código em `site-packages`). O ambiente é reaproveitado **sem reverificação**: a alteração persiste e roda nas próximas execuções de qualquer robô. |
| `uv-cache\` | Alterar o cache do `uv`, de onde os próximos ambientes são montados. |
| `keys\` | **Ler** `machine.key` e `identity.json`. A chave é protegida por DPAPI com escopo de máquina e entropia fixa no código: qualquer processo da máquina que consiga ler o arquivo a decifra. Com ela, o robô se passa pela máquina junto ao servidor (obtém tokens, pede execuções, envia logs). |
| `python\` e `browsers\` | Só ler e executar (o `setup` fixa a ACL; há teste no CI do Windows). Não consegue alterar o runtime. |
| `agent.toml`, `PAUSED`, `logs\` | Editar a lista de robôs permitidos, ligar ou desligar o kill switch, ler e apagar logs. |
| Leitura por outros usuários locais | `packages\`, `envs\`, `logs\`, `agent.toml` são legíveis por qualquer usuário da máquina (herança de `ProgramData`). |

O modelo de ameaças do `security.md` (robô explorado: "usuário Windows dedicado sem admin") assume que um robô comprometido fica contido. Hoje ele só fica contido em relação ao Windows em geral, não em relação ao próprio agente, ao runtime e a outras versões. Isso importa: o robô navega em sites de terceiros e processa dados de clientes.

## Decisão

**Hospedeiro do robô com identidade própria (opção E) e ambiente novo por execução (opção D).** Nenhuma credencial é guardada.

1. **O agente roda sempre como serviço** (`NT SERVICE\RegistaAgent`, sessão 0) e é o **único** que acessa `keys\`, `agent.toml` e `PAUSED` (os dois últimos só leitura: só um administrador os altera).
2. **Um hospedeiro** (`regista-agent host`) roda como a identidade do robô e é quem inicia o robô: no modo `service`, o serviço `RegistaRobot` (conta virtual `NT SERVICE\RegistaRobot`, sem perfil interativo); no modo `session`, uma tarefa de logon do **usuário dedicado** (sem senha: o gatilho é o logon do próprio usuário), na sessão interativa, para robôs com tela e com o perfil desse usuário. Os modos `service` e `session` passam a dizer **onde o hospedeiro roda** (emenda à ADR 0010).
3. **Canal por named pipe**, com comando de mão única: o agente manda "execute esta versão, já verificada, com estes parâmetros, nestas pastas"; o hospedeiro só inicia o robô e devolve saída, código de saída e eventos. O hospedeiro **não pede nada** ao agente (nem token, nem chave, nem dado do servidor).
   - O pipe é criado pelo agente (`FILE_FLAG_FIRST_PIPE_INSTANCE`, **uma única instância**, clientes remotos recusados), com DACL só para o SID esperado do hospedeiro. O hospedeiro confere que o servidor do pipe é o agente.
   - Ao aceitar, o agente confere a identidade de quem conectou: PID do cliente do pipe, SID do token do processo e caminho do executável. Tudo que vem do hospedeiro é **dado não confiável** (formato estrito, tamanhos, taxa, só o `run_id` da execução corrente).
   - Como robô e hospedeiro têm a mesma identidade, um robô não pode se passar pelo hospedeiro porque a instância única já está ocupada, o hospedeiro segura o robô num Job Object com `KILL_ON_JOB_CLOSE` (cai o hospedeiro, morrem os robôs e o pipe libera) e o executável do cliente é conferido.
4. **Sem fallback.** Hospedeiro ausente, sem resposta, com identidade ou protocolo divergentes: a execução falha com o código `robot_host_unavailable`, com texto fixo no painel. No Windows, produção nunca roda o robô como a conta do agente.
5. **Ambiente novo por execução.** O agente (não o robô) extrai o código da versão **do zip já verificado** e cria o venv numa **pasta da execução** (`runs\<id>\`), com a ACL gravada **antes** de popular: o robô lê e executa o código e o venv da própria execução, sem escrita; escreve só em `tmp\` e `artifacts\`. O `uv-cache` fica fora do alcance do robô (sem leitura nem escrita); a instalação é sempre `--offline --no-index --require-hashes` contra o `requirements.lock` do pacote verificado, com `--link-mode=copy` (hardlink do cache herdaria a ACL do cache), e **sem cache** se o `uv` não reconferir hashes de itens vindos dele. A pasta da execução é apagada pelo agente ao fim, inclusive em falha, cancelamento e queda. `envs\` deixa de existir.
6. **Matriz de permissões** de `%ProgramData%\Regista` (herança desligada na raiz, usuários locais sem acesso): ver `docs/specs/security.md`. O robô só lê o pacote e o ambiente da própria execução e não alcança `keys\`, `agent.toml` nem `PAUSED`.
7. **Arquivos do robô são dados não confiáveis.** O agente só abre capturas que sejam arquivos regulares **sem ponto de reanálise** (junction e link simbólico recusados; o robô pode criar uma junction em `artifacts\` apontando para `keys\`, e um agente que seguisse o link enviaria a chave como "captura"), e a limpeza da pasta da execução não segue links.
8. **Persistência entre execuções.** O robô recebe um ambiente de processo montado só pelo agente: `PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`, `TEMP`, `TMP`, `HOME`, `USERPROFILE`, `APPDATA` e `LOCALAPPDATA` dentro de `tmp\`. Teste de registro (HKCU): ver "Resultado do spike".

## Opções consideradas

| Opção | O que fecha | Custo real no Windows | Veredito |
|---|---|---|---|
| **A. Conta local separada + `CreateProcessWithLogonW`** | leitura de `keys\`, escrita em caches, `agent.toml` | Senha da conta guardada pelo agente (um segredo a rotacionar e perder); o MSI cria e gerencia a conta; matar o robô depende de handles de um processo criado pelo `seclogon`; no modo session o robô passa a ter o perfil de **outra** conta, e os aplicativos desktop ficam configurados no usuário dedicado | Descartada |
| **B. Token restrito / AppContainer** | leitura de `keys\` e escrita | Sem senha nem conta, mas o Chromium do Playwright tem o próprio sandbox (token restrito dentro de token restrito costuma quebrar), o modo session com aplicativos desktop sofre com integridade baixa, e todo acesso passa por uma segunda checagem (SIDs restritos) em toda pasta que o robô toca | Descartada (a mais frágil) |
| **C. Reverificar arquivos extraídos contra lista de hashes assinada** | só a persistência em `envs\` | Não impede a leitura de `keys\`; varrer milhares de arquivos de `site-packages` custa segundos por execução; só detecta o que já mudou | Dispensável: nada que o robô escreve é reaproveitado |
| **D. Ambiente novo por execução** | persistência em `envs\` e `uv-cache\` | +2 a 5 s por execução (cópia das wheels) e disco temporário; não impede a leitura de `keys\` | Adotada, junto com E |
| **E. Hospedeiro com identidade própria** | tudo: `keys\`, caches, `agent.toml`, `PAUSED`, sem credencial | Dois serviços (mais uma tarefa no modo session), um protocolo local mínimo, identidade do cliente do pipe e um instalador mínimo | **Adotada** |

## Risco residual (aceito)

Robô e hospedeiro têm a **mesma identidade**. Um robô comprometido consegue, portanto, abrir o processo do hospedeiro ou injetar código nele. Isso **não** dá acesso a `keys\` nem ao agente: o hospedeiro não tem privilégio além do robô, e o agente trata tudo que vem dele como não confiável e recusa o que sair do protocolo. O pior caso é **falsificar a saída e o resultado da própria execução** (um log mentiroso, um código de saída falso). A defesa em profundidade: o hospedeiro não guarda segredo, não pede nada ao agente, e um teste exercita um hospedeiro adulterado (mensagens fora do protocolo, de outro `run_id`, de tipo desconhecido, grandes demais ou fora de ordem são recusadas).

Outros resíduos: o perfil e o `HKCU` da identidade do robô persistem entre execuções (no modo session, o perfil do usuário dedicado é, por decisão de produto, o do robô, para preservar os aplicativos desktop configurados). Um administrador da máquina continua podendo tudo (limite aceito desde a ADR 0018).

## Resultado do spike

_(preenchido no passo 1 do M4b: custo do venv por execução com Playwright, comportamento do cache do `uv`, Chromium sob `NT SERVICE\RegistaRobot`, Job Object, pipe entre serviços e persistência pelo registro.)_

## Consequências

O M4b entrega o hospedeiro, o instalador mínimo (`service install`) e a matriz de permissões. O M8 troca o invólucro de serviço em ctypes por WinSW/MSI, instala em `Program Files` e cria os dois serviços. Cada execução ganha o custo do ambiente novo. O M5 passará o token de job do agente ao hospedeiro por execução (o robô **vê** esse token, porque é para ele). Linux (fora do MVP) continua sem isolamento (`DirectLauncher`).

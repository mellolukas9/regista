# ADR 0022: Conta separada e de menor privilégio para o robô

- **Status:** proposta (a decisão fica para quando o trabalho for planejado)
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

**Ainda não decidida.** Fica registrado que é **pré-requisito obrigatório antes do primeiro cliente em produção**: o robô deve rodar com uma conta separada, de menor privilégio, que só lê o cache e o ambiente da própria versão e **não enxerga `keys\`**. Esta ADR compara as opções para a decisão.

## Opções

| Opção | Como funciona | Fecha | Custo e riscos |
|---|---|---|---|
| **A. Conta local separada para o robô, com ACLs** | O instalador cria `RegistaRobot` (sem login interativo, sem admin). O agente lança o robô com essa conta (`CreateProcessWithLogonW`/`CreateProcessAsUser`, senha ou token gerenciado). ACLs: `keys\`, `agent.toml`, `PAUSED`, `logs\` só para o agente; `packages\`, `envs\`, `uv-cache\` gravam só o agente e leem o robô; `python\` e `browsers\` como hoje; pasta de trabalho da execução só do robô. | (a), (b), (c) e `agent.toml` | O maior: o MSI cria e gerencia a conta e sua credencial (ou usa `LogonUser` com senha aleatória guardada com DPAPI para o agente). No modo `session`, a conta interativa já existe e a separação exige uma segunda conta para o robô, com impacto nos robôs que dependem da tela. O Playwright precisa de acesso de leitura ao perfil/temporário do robô. Exige testes de ACL no CI do Windows e conferência de antivírus. Não precisa mudar o formato do pacote. |
| **B. Token restrito do Windows (restricted token / AppContainer / job object)** | O agente cria, com a mesma conta, um token com SIDs desabilitados e SIDs restritos (`CreateRestrictedToken`), ou roda o robô num AppContainer/low integrity, com ACLs que concedem acesso só à pasta de trabalho e à versão. | (c) e a escrita em `packages\`/`envs\` (por integridade baixa e SIDs restritos) | Médio: sem conta nova nem senha. Mas o Chromium do Playwright já usa o próprio sandbox, que pode falhar sob token restrito ou AppContainer; automação de janelas (modo `session`) tem limitações; é a opção mais frágil para robôs que acionam aplicativos desktop. Exige experimentação com o Playwright antes de decidir. |
| **C. Reverificar os arquivos extraídos a cada execução contra uma lista de hashes assinada** | O pacote assinado leva um arquivo com o hash de cada arquivo do ambiente (ou o agente calcula na criação e guarda assinado com a chave da máquina). Antes de cada execução, o agente confere o `envs\<versão>` e o código extraído. | Só a persistência por alteração do ambiente (a) | Baixo a médio, mas **não** fecha (c): o robô continua lendo a chave da máquina. A conferência de um ambiente com milhares de arquivos leva segundos (cada execução). Não impede o robô de alterar o ambiente e agir durante a própria execução. Serve como defesa em profundidade, nunca sozinha. |
| **D. Ambiente novo a cada execução** | `uv venv` mais `uv pip install` offline a partir das wheels do pacote, num diretório temporário do robô, apagado ao fim. | (a) e (b) (nada persiste entre execuções) | Baixo na implementação; paga 1 a 3 s por execução (mais com muitas wheels grandes) e disco temporário. Também **não** fecha (c). Combina bem com a opção A. |

## Recomendação provisória (sujeita à decisão)

**A, mais D.** A é a única que fecha a leitura da chave da máquina (c), que é o risco mais grave; D tira a persistência dos ambientes sem depender de ACLs perfeitas. B só se experimentos mostrarem que o Playwright e os robôs com tela convivem com ela; C como reforço opcional.

## Consequências

Enquanto isso não for decidido e feito: um robô comprometido (por exemplo, por um site malicioso que explore o navegador) pode ler a identidade da máquina e persistir código no ambiente. Por isso o `STATUS.md` registra como **pré-requisito do primeiro cliente em produção**. Mudar para a conta separada altera o instalador (M8), o `setup`, o `diagnose` (que passa a conferir as ACLs de cada pasta), o modo `session` e o runbook.

## Alternativas descartadas

Manter como está e documentar (não serve a um produto multi-cliente que roda código em máquinas de terceiros); mover a chave da máquina para o TPM sem separar a conta (o robô continuaria podendo usar a chave enquanto o agente roda, sem extraí-la, o que ajuda mas não basta).

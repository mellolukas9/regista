# Runbook: implantação em cliente

_Rascunho. Validar e completar no M8 com a primeira implantação real._

## 1. Levantamento

- O robô precisa de janela visível ou de aplicativo desktop, ou é só web?
- Volume diário estimado de itens e duração média por item.
- Horário em que a máquina fica ligada; quem usa a máquina durante o dia.
- Existe TI no cliente? Antivírus corporativo? Proxy?
- Onde estão os dados de entrada (planilha, ERP, site) e que dado é sensível.

## 2. Escolha do caminho

| Situação | Caminho |
|---|---|
| Robô web headless | **A:** modo `service` na própria máquina, sem VM (hospedeiro do robô como serviço, conta virtual própria) |
| Robô precisa de tela e a máquina comporta | **B:** VM Windows na máquina (Hyper-V no Windows Pro/Enterprise/Education, ou VMware) com modo `session` (hospedeiro do robô no logon do usuário dedicado) |
| Robô precisa de tela e a máquina não comporta | Máquina dedicada simples ou EC2 na conta do cliente |

O Windows de estação (10/11) não permite duas sessões interativas simultâneas; por isso robôs com tela numa estação em uso exigem VM.

### Requisitos mínimos sugeridos

| | Caminho A | Caminho B |
|---|---|---|
| Memória | 8 GB (concorrência 1) | 16 GB (4–6 GB para a VM) |
| Processador | 4 núcleos | 4+ núcleos, virtualização ativa na BIOS |
| Disco | SSD, 10 GB livres | SSD, 60 GB livres |
| Sistema | Windows 10/11 | Windows 10/11 Pro no host + licença para a VM |
| Rede | Saída HTTPS para o domínio do Regista (proxy suportado) | Idem |

No caminho A, configurar prioridade de processo abaixo do normal e concorrência 1–2 para não afetar o usuário da máquina.

## 3. Configuração no Regista

1. Criar o tenant e os usuários do cliente (MFA obrigatório para todos os usuários, ADR 0017).
2. Criar o pool e a máquina; gerar a chave de registro.
3. Criar a fila (modo de dados `reference` por padrão; definir campos visíveis e retenção).
4. Publicar a versão assinada do robô e associar ao pool.

## 4. Instalação

1. Caminho B: preparar a VM (Windows, atualizações, usuário dedicado **sem admin**, login automático, bloqueio de tela desativado). **Cadastrar o agente só depois da VM pronta; nunca clonar uma VM já cadastrada.**
2. Instalar o programa do agente **em uma pasta só alterável por Administradores e SYSTEM** (por exemplo, em `C:\Program Files\Regista`; o MSI do M8 faz isso; antes dele, `service install` recusa uma pasta que outras contas alterem).
3. Em um PowerShell **elevado**: `regista-agent enroll --url <url> --key <chave>`. No caminho B, acrescente `--robot-account <usuário dedicado>`. O robô nunca roda com a conta do agente (ADR 0022); no caminho A a conta do robô é a virtual `NT SERVICE\RegistaRobot`.
4. `regista-agent setup --from-server` (grava as permissões das pastas, instala o Python e o Chromium exatos).
5. `regista-agent service install --start` (caminho A: serviços `RegistaAgent` e `RegistaRobot`; caminho B: serviço `RegistaAgent` e a tarefa de logon `Regista\RobotHost`, que inicia o hospedeiro no logon do usuário dedicado, **sem guardar senha**). Confira com `regista-agent service status`.
6. `regista-agent diagnose` sem erros (o agente e o hospedeiro rodando com contas diferentes, as permissões das pastas, o usuário dedicado sem privilégio de administrador) e máquina **online** no painel.
7. Adicionar o robô à lista local de permitidos: `regista-agent allow <pacote>`.
8. Se houver TI, solicitar exceção de antivírus para a pasta do agente.

### Máquina que já tinha o agente (migração para o M4b)

1. Atualizar o programa (na pasta só de administradores) e rodar `regista-agent setup` elevado: ele grava a matriz de permissões, **apaga `envs\` e esvazia `uv-cache\`** (o conteúdo antigo não é confiável) e passa a negar o acesso de usuários comuns.
2. `regista-agent service install` (cria os serviços; a tarefa antiga que iniciava o *agente* no logon do usuário, no caminho B, deve ser removida na mão: o que inicia no logon agora é o hospedeiro).
3. **Chave da máquina:** uma máquina que já rodou robô não confiável teve a chave legível pelo robô. Gere uma nova chave de registro no painel e rode `regista-agent enroll --force` (a chave antiga fica inútil). Hoje só há máquinas de desenvolvimento.
4. Conferir com `regista-agent diagnose`.

### Quando a execução falha com "o serviço que roda os robôs não respondeu"

É o código `robot_host_unavailable`: o hospedeiro não está conectado ao agente, não respondeu ou não é quem devia ser. No console da máquina: `regista-agent diagnose` como administrador (diz se o serviço `RegistaRobot` ou a tarefa de logon está parado, com a conta errada ou ausente), `regista-agent service status`, e o log do agente em `%ProgramData%\Regista\logs\agent.log` (a verificação que falhou). No caminho B o hospedeiro só existe com o usuário dedicado logado. Reinicie com `Start-Service RegistaRobot` (caminho A) ou faça o logon do usuário (caminho B).

## 5. Homologação

1. Lote pequeno com linhas válidas, inválidas e casos de erro conhecidos.
2. Conferir reconciliação do lote, relatório e planilha devolvida.
3. Validar com o usuário de negócio.

## 6. Produção

1. Criar agendamento dentro do horário em que a máquina fica ligada.
2. Ativar alertas por e-mail: falha de job, máquina sem sinal, lote concluído.
3. Acompanhar a primeira semana; ajustar concorrência e horários.

## Acordos com o cliente

- Disponibilidade da máquina é responsabilidade do cliente; o Regista monitora e alerta.
- Itens interrompidos (máquina desligada, queda de energia) ficam como Abandonado e não voltam sozinhos: a pessoa usa "Reprocessar" no item ou nas falhas da fila (ADR 0011).
- Crescimento de volume: o robô migra para máquina dedicada ou nuvem do cliente trocando o pool.

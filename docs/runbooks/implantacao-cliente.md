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
| Robô web headless | **A:** agente em modo `service` na própria máquina, sem VM |
| Robô precisa de tela e a máquina comporta | **B:** VM Windows na máquina (Hyper-V no Windows Pro/Enterprise/Education, ou VMware) com agente em modo `session` |
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

1. Criar o tenant e os usuários do cliente (MFA para administradores).
2. Criar o pool e a máquina; gerar a chave de registro.
3. Criar a fila (modo de dados `reference` por padrão; definir campos visíveis e retenção).
4. Publicar a versão assinada do robô e associar ao pool.

## 4. Instalação

1. Caminho B: preparar a VM (Windows, atualizações, usuário dedicado sem admin, login automático, bloqueio de tela desativado). **Cadastrar o agente só depois da VM pronta; nunca clonar uma VM já cadastrada.**
2. Instalar o MSI do agente.
3. `regista-agent enroll --url <url> --key <chave>`.
4. `regista-agent diagnose` sem erros e máquina **online** no painel.
5. Adicionar o robô à lista local de permitidos.
6. Se houver TI, solicitar exceção de antivírus para a pasta do agente.

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
- Itens interrompidos (máquina desligada, queda de energia) voltam para a fila automaticamente.
- Crescimento de volume: o robô migra para máquina dedicada ou nuvem do cliente trocando o pool.

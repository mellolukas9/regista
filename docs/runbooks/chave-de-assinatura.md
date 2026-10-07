# Runbook: chave de assinatura dos pacotes

Vale para a chave que assina os pacotes de robô (ADR 0021). Quem tem a chave privada consegue mandar código para rodar em todas as máquinas de clientes: trate-a como a chave mais sensível da Artemisys.

## Regras

- A privada de produção **nunca** vai para o repositório, o servidor do Regista, a CI nem os segredos do GitHub. Na CI só existem chaves de teste geradas na hora.
- Fica num arquivo **cifrado com senha**, na máquina de build da Artemisys (padrão `%USERPROFILE%\.regista\signing\`). A senha é pedida por prompt; `REGISTA_SIGNING_PASSPHRASE` serve só para uso local nessa máquina.
- O `key_id` (16 caracteres hex) identifica a chave. A pública vai em `agent/src/regista_agent/trusted_keys.py` e na API, por PR revisado.

## Gerar

```powershell
uv run regista-pack keygen --out $env:USERPROFILE\.regista\signing --name ativa
uv run regista-pack keygen --out $env:USERPROFILE\.regista\signing-reserva --name reserva
```

Gere **duas** chaves antes do primeiro cliente: a **ativa** e a **reserva**. As duas públicas entram na lista confiável do agente desde o primeiro MSI, para que passar à reserva nunca exija reinstalar agentes.

## Backup

- **Chave ativa:** duas cópias do arquivo cifrado, em mídias diferentes e em locais diferentes (por exemplo, um pendrive no cofre e outro na casa de uma segunda pessoa de confiança). A senha fica no gerenciador de senhas da Artemisys, nunca junto do arquivo.
- **Chave reserva:** **as mesmas regras**, mas guardada **separadamente** da ativa (outras mídias, outro local). Quem alcança uma não alcança a outra.
- Confira o backup a cada 6 meses: abra o arquivo com a senha e assine um pacote de teste com `regista-pack verify`.
- Perdeu a ativa e tem a reserva? Passe a assinar com a reserva e gere uma nova reserva (o agente precisa de atualização para confiar nela).

## Se a chave vazar

1. **Pare de assinar com ela.** Mova o arquivo para fora de uso e registre quando e como vazou.
2. **Passe para a reserva.** Assine as próximas versões com a chave reserva (os agentes já confiam nela).
3. **Republique as versões em uso** de todos os bots, assinadas com a reserva, e coloque as novas em uso. Versões antigas assinadas pela chave vazada deixam de ser usadas.
4. **Prepare a atualização do agente** que remove a chave comprometida da lista confiável e acrescenta uma nova reserva. Enquanto os agentes não forem atualizados, eles ainda aceitariam o que fosse assinado com a chave vazada: quem tiver acesso à chave e ao servidor (ou um pacote entregue por outro meio) poderia rodar código. Priorize atualizar as máquinas, começando pelas de clientes com acesso a dados sensíveis, e use o kill switch local onde o risco justificar.
5. **Revise o que aconteceu:** quais versões foram publicadas desde o vazamento (`audit_log`: `bot.version_published`), e gere uma nova reserva.

## Limites conhecidos

- Remover uma chave comprometida exige atualizar o agente. Até a autoatualização assinada (M8), isso é a reinstalação do MSI.
- O servidor do Regista não guarda a privada, então um servidor invadido não consegue assinar; mas ele pode oferecer a um agente um pacote assinado **legítimo** de outra versão. O agente só aceita se o cliente, o nome e a versão baterem com o esperado.

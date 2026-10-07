# ADR 0021: Pacotes assinados: formato, chaves, amarração ao cliente e runtime

- **Status:** aceita
- **Data:** 2026-10

## Contexto

A ADR 0008 decidiu que o agente só executa pacotes assinados pela Artemisys, com ambiente por versão criado com `uv`. O M4 precisa dos detalhes: o que vai dentro do pacote, como as dependências chegam à máquina do cliente, o que exatamente é assinado, onde fica a chave, como ela é trocada, e de onde vêm o Python e o Chromium. Redes de clientes costumam ter proxy com inspeção de TLS e bloqueio do PyPI, e a conta do agente (`NT SERVICE\RegistaAgent` ou o usuário dedicado do modo `session`) não enxerga o `%LOCALAPPDATA%` de outro usuário.

## Decisão

1. **Formato.** Um zip `.rgpkg` com `manifest.json`, `bot/` (código), `wheels/` (todas as dependências já baixadas, inclusive o SDK) e `requirements.lock` (gerado com hashes). Uma assinatura `.rgsig` (JSON) acompanha: manifesto, `sha256` do zip, `key_id` e a assinatura Ed25519. O agente instala **sem rede**: `uv pip install --offline --no-index --find-links wheels --require-hashes`. A máquina do cliente só precisa falar com a API e com o S3 do Regista, nunca com o PyPI.
2. **O que se assina.** `b"regista-package-v1\n"` mais o JSON canônico de `{tenant_id, package_name, version, sha256, size, python, playwright, chromium_revision, key_id}`. Como o `sha256` cobre o zip inteiro, código, wheels e lockfile ficam cobertos.
3. **Amarração ao cliente.** O manifesto leva `tenant_id`. O agente compara com o `tenant_id` gravado no cadastro (`keys\identity.json`, na pasta com ACL restrita da ADR 0018), **nunca** com algo que venha na resposta da execução, e recusa pacote de outro cliente mesmo que o servidor o envie. Também recusa se `package_name` ou a versão diferirem do bot e da execução. `regista-pack build --client` pode ser repetido e gera um pacote por cliente numa execução (mesmas wheels, manifestos diferentes).
4. **Só wheels, só Windows 64 bits no MVP.** O `regista-pack` baixa wheels para `win_amd64` e o Python alvo. Dependência que só tem sdist faz o comando falhar com mensagem apontando o pacote; nunca se inclui sdist (compilar na máquina do cliente executaria código sem assinatura). Outras plataformas ficam como evolução.
5. **Chaves.** Ed25519; a privada fica em PKCS8 **cifrado com senha**, na máquina de build da Artemisys, fora do repositório e do servidor. **A chave de produção nunca vai para a CI nem para os segredos do GitHub.** Na CI só existem chaves de teste geradas na hora, aceitas pelo override de dev. `REGISTA_SIGNING_PASSPHRASE` existe apenas para uso local na máquina de build. O `key_id` são os 16 primeiros caracteres hex do `sha256` da chave pública.
6. **Rotação.** O agente embute **uma lista** de chaves públicas confiáveis (`trusted_keys.py`, versionado). No início há a chave ativa e uma **reserva** guardada offline; os agentes confiam nas duas. Trocar de uma para outra não exige reinstalar agentes. Remover uma chave comprometida exige uma nova versão do agente (MSI; autoatualização no M8): limite conhecido, com roteiro no runbook `chave-de-assinatura.md`. A API tem a mesma lista e um teste garante que as duas são iguais.
7. **Sem sobrescrita em produção.** `REGISTA_DEV_TRUSTED_KEYS` (chaves extras) só vale com `REGISTA_ENVIRONMENT=dev`. Em `prod`, o agente se recusa a iniciar se ela estiver definida, e o `agent.toml` não tem esse campo. A API aceita o equivalente só fora de `prod`.
8. **Runtime preparado por um passo elevado, nunca baixado pela execução.** O manifesto declara `python` (versão exata), `playwright` e `chromium_revision`. O `regista-agent setup` (console elevado) instala essas versões exatas em `%ProgramData%\Regista\python` e `%ProgramData%\Regista\browsers` (várias revisões do Chromium lado a lado), podendo ler da API o que os bots do pool pedem. As duas pastas têm escrita só para Administradores e SYSTEM; a conta do agente só lê e executa, então um robô comprometido não altera o runtime dos próximos. Se faltar, a execução falha com `runtime_missing` e o motivo "Falta preparar esta máquina para a versão X: rode regista-agent setup como administrador". O `diagnose` lista os runtimes e avisa quando a versão em uso de algum bot do pool exige um que falta.
9. **Integridade do que o `setup` baixa.** O Python (python-build-standalone) tem hash publicado e é conferido. **Risco aceito:** os navegadores do Playwright vêm do CDN dele só por HTTPS, sem hash verificável. Evolução: o MSI do M8 traz os dois offline.
10. **Pacote por URL pré-assinada.** O painel envia o `.rgpkg` direto ao S3 (PUT pré-assinado de 300 s, chave montada pelo servidor, tipo e tamanho assinados, limite de 200 MB); o servidor confere assinatura antes de abrir o upload e, ao concluir, **recalcula o `sha256` e o tamanho** do objeto e o manifesto dentro do zip. O agente baixa por GET pré-assinado de 120 s, só de uma versão que esteja numa execução `assigned`/`running` **dessa** máquina.
11. **A versão da execução é fixada quando o agente assume**, não na criação: trocar a versão em uso vale para a próxima execução que uma máquina pegar.
12. **Banco.** `bot_versions` com RLS forçado; FKs compostas com `tenant_id` e `bot_id` fazem o próprio banco recusar colocar em uso a versão de outro bot ou de outro cliente. Versões publicadas são imutáveis (sem `UPDATE` de conteúdo, sem `DELETE`).
13. **Ordem no agente.** Kill switch local, lista local de robôs permitidos (negar por padrão), pacote (hash e assinatura **antes** de abrir o zip; depois cliente, nome e versão), extração com proteção contra caminhos maliciosos, runtime, ambiente por versão, execução. Códigos de erro novos: `package_invalid`, `robot_not_allowed`, `runtime_missing`, `environment_failed`.
14. **`REGISTA_DEV_UNSIGNED` continua só em dev** (a ADR 0008 e o M3 não mudam nisso). As travas: o agente se recusa a iniciar com ela em `prod`; o servidor recusa criar execução em `prod` de bot sem versão em uso; em `prod` o agente nunca usa o runner de pasta.

## Consequências

Um servidor do Regista comprometido continua sem conseguir executar código: sem a chave privada ele não produz assinatura aceita, e mesmo um pacote legítimo de outro cliente é recusado pelo agente. A máquina do cliente não depende do PyPI. O custo são pacotes maiores (dezenas de MB com o Playwright) e um passo de preparação por máquina (`setup`). Máquinas cadastradas antes do M4 não têm o `tenant_id` local e precisam de "Gerar nova chave" e novo `enroll`. Remover uma chave de assinatura comprometida depende de atualizar os agentes.

## Alternativas descartadas

| Opção | Motivo |
|---|---|
| Instalar do PyPI na hora | Proxy e bloqueios no cliente; um pacote comprometido ou removido muda o robô sem a Artemisys saber; não reprodutível |
| Agente baixa Python e Chromium sozinho | A conta do agente passaria a baixar binários em toda versão nova, atrás do proxy do cliente |
| Python e Chromium dentro de cada pacote | ~250 MB repetidos a cada versão |
| Assinatura só do nome e da versão | A proteção entre clientes ficaria só no servidor (RLS) para o mesmo nome de pacote |
| Chave pública configurável em produção | Quem controla o arquivo de configuração passaria a controlar em quem o agente confia |
| Chave de assinatura na CI | Um repositório ou CI comprometido assinaria código para todas as máquinas |

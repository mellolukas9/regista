# ADR 0018: Identidade de máquina: buscas antes do tenant, token assinado e tarefas entre clientes

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O M2 entrega a identidade das máquinas e a presença delas. Três problemas apareceram ao implementar as ADRs 0004, 0007 e 0016 juntas:

1. O agente se apresenta com uma chave de registro, um `machine_id` ou um nonce **antes** de existir tenant na sessão, e o RLS bloqueia essa busca (mesmo problema da ADR 0017 com o login).
2. Depois do token, toda requisição do agente precisa saber o tenant sem confiar em parâmetro, e a revogação precisa valer na hora.
3. A tarefa que marca máquinas sem sinal olha todos os clientes, e a aplicação não pode ter `BYPASSRLS`.

Além disso, a chave privada da máquina precisa de proteção em repouso que funcione em Windows, onde quem faz o cadastro (administrador num console) não é a conta que roda o agente.

## Decisão

1. **Três funções `SECURITY DEFINER` novas**, no mesmo padrão da ADR 0017 (dono `regista_owner`, `search_path` fixo, flag de plataforma ligado e restaurado dentro do corpo, `REVOKE ALL FROM PUBLIC`, `EXECUTE` só para `regista_app`, só as colunas necessárias). Elas **estendem a lista da ADR 0017**:
   - `app.lookup_enrollment_key(key_hash)`: acha a chave de registro e o tenant dela;
   - `app.lookup_machine_credential(machine_id)`: devolve tenant, chave pública, `credential_version` e status;
   - `app.rate_limit_purge(segundos)`: limpa janelas velhas de `auth_rate_limits`, que o app não lê.
2. **Autenticação por desafio assinado.** O nonce é de uso único (consumido por `UPDATE … RETURNING`), vale 60 s, fica ligado à máquina e é assinado junto com o `audience` (URL pública da API). Pedir outro desafio invalida o anterior. Rate limit por IP e por máquina.
3. **Token de acesso sem estado**, `rga1.<payload>.<mac>`, de 15 min, com `{typ, mid, tid, cv, exp}`. O MAC é HMAC-SHA256 com chave derivada da master key pelo **`KeyProvider.mac` / `verify_mac`** (dev: HKDF a partir da chave local; produção, M8: KMS `GenerateMac`). O `tid` vem do token assinado; a máquina é lida sob RLS normal.
4. **Revogação imediata.** O marcador `MachineRoute` consulta a máquina a cada requisição e responde 401 se ela foi revogada ou se `cv` difere de `credential_version`. Cadastrar de novo incrementa `credential_version`, o que derruba o agente antigo na hora. Um tipo de credencial nunca serve no lugar do outro: o usuário usa só cookie e o agente só `Bearer`.
5. **Tarefas que passam por todos os clientes** (padrão): a leitura usa `tenant_session(platform_admin=True)` (só `SELECT`, permitido pela política); cada escrita roda em `tenant_session(tenant_id da linha)`. A tarefa é idempotente e refaz a condição no `UPDATE`, então não disputa com um heartbeat que acabou de chegar.
6. **Worker em processo separado** (`regista-worker`), conectado como `regista_app`. Periódicas sem duplicação entre workers (o defer periódico do Procrastinate é único por tarefa e instante, mais `queueing_lock`). O schema do Procrastinate é aplicado pela migration `0003`, como owner, no schema `public`, com grants explícitos a `regista_app`. As tabelas dele não têm `tenant_id` (dado de plataforma) e os argumentos das tarefas levam só ids.
7. **Versão do Procrastinate fixa** (`==`). Atualizar exige uma migration Alembic nova que aplica, como `regista_owner`, os arquivos de `procrastinate/sql/migrations/` entre a versão antiga e a nova (em ordem) e refaz os grants. Nunca usar `procrastinate schema --apply` nem trocar só a versão. O procedimento está no `CLAUDE.md`.
8. **Chave privada no Windows: DPAPI com escopo da máquina (`LocalMachine`) mais ACL.** A chave fica em `%ProgramData%\Regista\keys\`, com entropia adicional fixa, herança desligada e acesso só para a **conta que roda o agente**, `SYSTEM` e `Administradores`; nunca para quem rodou o `enroll`. A conta vem de `--agent-account` (padrão `NT SERVICE\RegistaAgent` no modo serviço; obrigatória no modo sessão). Gravar na pasta exige console elevado. O `diagnose` reprova se a conta do agente não lê a chave, se houver outra conta com acesso ou se a herança estiver ligada. Linux: arquivo `0600` em pasta `0700`, com dono igual à conta do agente.
9. **Relógio do agente.** Renovação do token e backoff medem tempo decorrido com `time.monotonic()`; o agente não lê o `exp` do token, só o servidor o valida.

## Consequências

As buscas antes do tenant continuam a única exceção ao "nada contorna RLS", agora seis funções com teste próprio (colunas devolvidas, `PUBLIC` sem `EXECUTE`, flag restaurado em falha). Revogar e recadastrar valem na próxima requisição, sem lista de tokens revogados. O token não pode ser invalidado individualmente antes dos 15 min fora da revogação da máquina ou do recadastro (aceito: o escopo é uma máquina e a verificação roda a cada chamada). O M3 passa a ter três terminais em desenvolvimento (API, worker, web).

**Risco aceito:** um administrador da máquina consegue extrair a chave privada (DPAPI `LocalMachine` não protege contra quem administra o computador) e passar por ela. Evolução: provedor de chaves da plataforma, com a chave no TPM; o resto do agente não muda, porque só chama `load`. Mitigação até lá: revogar a máquina no painel invalida a identidade na hora.

## Alternativas descartadas

DPAPI do usuário (`CurrentUser`): quebra quando o serviço roda com outra conta que a do `enroll`. JWT assinado com chave assimétrica: exigiria publicar e rodar chaves para algo que só a API valida. Token opaco com tabela de sessões de máquina: uma consulta e uma escrita a mais por requisição, sem ganho (a máquina já é consultada a cada chamada). Role com `BYPASSRLS` para o worker: quebra a regra inegociável 1. Lista de máquinas sem sinal calculada no heartbeat: dependeria de a máquina que parou continuar mandando algo.

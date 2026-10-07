# Modelo de dados

Convenções:

- Toda tabela de cliente tem `tenant_id uuid not null` referenciando `tenants(id)`, índice que começa por `tenant_id` e política RLS (ver `security.md`).
- Chave primária `id uuid` (v7: padrão `uuidv7()` do PostgreSQL 18; gere na aplicação só quando o id for necessário antes do INSERT). `created_at` e `updated_at` em `timestamptz` UTC.
- Enumerações como `text` com `CHECK` (mais simples de migrar que tipos `enum` do Postgres).
- Nomes de tabelas e colunas em inglês, `snake_case`.

O marco em que cada tabela nasce está entre parênteses. Comportamentos de produto que dependem destas tabelas estão em `design-system.md`, seção 11.

## Identidade e acesso

### `tenants` (M0)
| Coluna | Tipo | Notas |
|---|---|---|
| id | uuid | |
| name | text | |
| slug | text unique | |
| data_region | text | ex.: `sa-east-1` |
| is_active | bool | |
| is_internal | bool | tenant da equipe Artemisys (M1): só um, nunca desativado, oculto de listas, seletor e visões consolidadas |

Na interface, tenant se chama **Cliente**. Só a equipe Artemisys cria clientes (nome + e-mail do primeiro Admin do cliente, que recebe convite).

A política de `tenants` usa `id` no lugar de `tenant_id`: a sessão enxerga apenas o próprio tenant, exceto administradores da plataforma.

### `users` (M1)
| Coluna | Tipo | Notas |
|---|---|---|
| tenant_id | uuid | |
| email | citext | **único global** (ADR 0017) |
| password_hash | text | argon2id; nulo até definir a senha |
| display_name | text | nome exibido; só para a equipe Artemisys |
| role | text | `tenant_admin`, `operator`, `viewer` |
| is_platform_admin | bool | equipe Artemisys; só no tenant interno, e todo usuário dele é platform admin (trigger) |
| mfa_secret_enc | bytea | criptografado via KeyProvider (AAD = tenant_id\|user_id) |
| mfa_key_id | text | chave usada na criptografia |
| mfa_enabled_at | timestamptz | |
| mfa_last_step | bigint | último passo TOTP aceito (impede reuso do código) |
| mfa_enabled | bool | MFA é obrigatório para todos; `false` só até concluir o primeiro acesso |
| status | text | `invited` (convite não aceito), `active` (senha definida), `disabled` ("Remover acesso" desativa na hora) |
| failed_logins | int | |
| locked_until | timestamptz | |
| last_login_at | timestamptz | |

### `invitations` (M1)
id, tenant_id, user_id, token_hash, expires_at, used_at, created_by. O convite leva a definir senha e configurar MFA (validade padrão de 7 dias; `revoked_at` quando substituído). "Reenviar convite" invalida o anterior, a senha, o MFA, os códigos de recuperação e as sessões do usuário.

### `recovery_codes` (M1)
id, tenant_id, user_id, code_hash, used_at. 10 por usuário, uso único; gerar novos invalida os anteriores.

### `sessions` (M1)
id, tenant_id, user_id, token_hash (sha256 do token do cookie), csrf_token_hash, created_at, last_seen_at, expires_at, revoked_at, ip, user_agent, stage (`mfa_required`, `mfa_setup`, `recovery_codes`, `active`). O contexto de cliente do admin da plataforma vem só do cookie `rg_client`.

### `audit_log` (M1)
id, tenant_id, actor_type (`user`, `machine`, `system`), actor_id, action (ex.: `job.triggered`, `machine.revoked`), target_type, target_id, metadata jsonb, ip, created_at. Somente inserção: o role da aplicação tem apenas `INSERT` (sem `SELECT`, `UPDATE` nem `DELETE`).

### `auth_rate_limits` (M1)
key_hash bytea, window_start, count; PK `(key_hash, window_start)`. Sem `tenant_id` (dado de plataforma). Nenhum grant ao `regista_app`: só a função `app.rate_limit_hit` acessa.

## Execução

### `pools` (M2)
id, tenant_id, name (único por cliente, sem diferenciar maiúsculas), kind (`on_prem`, `client_cloud`, `internal`; a interface não tem campo e usa `on_prem`), description, created_by, created_at, updated_at.

### `machines` (M2)
id, tenant_id, pool_id, name, public_key (bytea, Ed25519), mode (`service`, `session`, `oneshot`), status (`pending`, `online`, `offline`, `revoked`), last_seen_at, agent_version, os_info jsonb, max_concurrency int (paralelismo de itens dentro de uma execução), enrolled_at, revoked_at, revoked_by, created_by, created_at, updated_at.

Identidade e autenticação do agente: `credential_version` int (sobe a cada cadastro; um token com versão antiga é recusado, o que derruba o agente anterior no recadastro) e o desafio em andamento, `challenge_hash` (sha256 do nonce) e `challenge_expires_at` (60 s), limpos no primeiro uso. `public_key` é nula até o cadastro. O nome segue `[a-z0-9][a-z0-9-]{0,62}` e é único por cliente entre as máquinas não revogadas. A API só aceita os modos `service` e `session`.

`paused_locally` bool (M4): o agente avisa pelo heartbeat que o kill switch local está ligado; é só indicação, o painel não controla.

Regras: `offline` após **2 minutos** sem sinal; volta a `online` no próximo sinal. **Uma execução por máquina por vez.**

### `machine_events` (M2)
id, tenant_id, machine_id, kind (`enrolled`, `re_enrolled`, `first_signal`, `went_offline`, `came_back`, `agent_updated`, `revoked`), metadata jsonb, created_at. Somente inserção. Alimenta o histórico da máquina. `re_enrolled` é o uso de uma nova chave numa máquina já cadastrada; o conteúdo vindo do agente (versão, sistema) é dado não confiável.

### `enrollment_keys` (M2)
id, tenant_id, machine_id, key_hash (sha256, único), expires_at, used_at, revoked_at, created_by, created_at. Uma chave viva (nem usada nem revogada) por máquina: gerar nova chave revoga a anterior na mesma transação. A chave em si (`rgk_…`) só aparece na resposta que a cria.

### `bots` (M3)
id, tenant_id, pool_id, name (único por cliente, sem diferenciar maiúsculas), `package_name` (`^[a-z][a-z0-9_]{0,62}$`, único por cliente, não muda: é a pasta do robô em dev e o pacote no M4), description, concurrency int default 1, is_active, created_by, current_version_id (M4).

### `bot_versions` (M4)
| Coluna | Notas |
|---|---|
| id, tenant_id, bot_id | FK composta `(tenant_id, bot_id)` → `bots`; `UNIQUE (tenant_id, bot_id, id)` |
| version | `^\d+\.\d+\.\d+$`, única por bot |
| package_sha256, size_bytes | do `.rgpkg`; o servidor os recalcula ao concluir o upload |
| signature (bytea, 64), key_id | assinatura Ed25519 e a chave que a fez |
| manifest jsonb | o manifesto assinado (cliente, pacote, versão, runtime) |
| storage_key | montada só pelo servidor: `tenants/<tenant>/bots/<bot>/versions/<id>.rgpkg` |
| status | `uploading`, `published`, `expired` (upload que venceu sem concluir) |
| upload_expires_at, published_at | |
| release_note | até 2000 caracteres |
| created_by, created_at | |

Só a equipe Artemisys publica versões. Publicada, a versão é imutável (sem `DELETE`; só `status` e `published_at` mudam). `bots.current_version_id` tem FK composta `(tenant_id, id, current_version_id)` → `bot_versions(tenant_id, bot_id, id)`: o banco recusa colocar em uso a versão de outro bot ou de outro cliente. A mesma FK vale para `jobs.bot_version_id`.

### `jobs` (M3)
| Coluna | Tipo | Notas |
|---|---|---|
| tenant_id, bot_id, bot_version_id | uuid | versão nula só em dev (M3) |
| pool_id | uuid | destino |
| machine_id | uuid | preenchido quando um agente assume |
| status | text | `pending`, `assigned`, `running`, `completed`, `failed`, `cancelled` |
| trigger | text | `manual`, `schedule`, `api` |
| triggered_by | uuid | usuário ou agendamento |
| params | jsonb | parâmetros do job |
| started_at, finished_at | timestamptz | |
| error_code, error_message | text | mensagem mascarada |
| cancel_requested_at | timestamptz | |
| short_code | text | código exibido (ex.: `exec-7f3a24`), único por tenant |
| assigned_at | timestamptz | quando um agente assumiu (alimenta a Timeline) |
| items_successful, items_failed, items_abandoned, items_total | int | contagem para a coluna Itens |

`error_code` aceita: `machine_lost`, `machine_revoked`, `timeout`, `robot_failed`, `robot_not_found`, `cancelled`, `internal` e, a partir do M4, `package_invalid`, `robot_not_allowed`, `runtime_missing`, `environment_failed`. `jobs.error_reason` (M4, nulo fora do `package_invalid`) guarda o motivo de uma lista fechada: `hash_mismatch`, `signature_invalid`, `unknown_key`, `wrong_client`, `wrong_package`, `wrong_version`, `unsafe_archive`, `too_large`, `malformed_package` (`CHECK`; o servidor descarta valor fora da lista). Para os códigos novos o servidor não guarda a mensagem livre do agente. `bot_version_id` é preenchido **quando o agente assume** a execução (a versão em uso naquele momento), não na criação.

Regras: `failed` quando qualquer item termina com falha, mesmo que o robô encerre normalmente. "Executar agora" com execução ativa do mesmo bot cria outro job `pending` (não bloqueia). Cancelar só em `pending`, `assigned` ou `running`.

### `job_logs` (M3), particionada por mês em `ts`
tenant_id, job_id, `seq` (numeração do agente por execução; `UNIQUE (job_id, seq, ts)` evita duplicar reenvios), item_id (nulo), item_ref (nulo), attempt (nulo), ts, level (`INFO`, `WARN`, `ERROR`), message (até 8 KB no banco; o servidor corta em 4 KB), extra jsonb. Sem partição `DEFAULT`; RLS forçado também nas partições, que não recebem grant (o app acessa pela tabela-mãe).

### `artifacts` (M3)
id, tenant_id, job_id, item_id, attempt_id, kind (`screenshot`, `file`), storage_key (montada só pelo servidor: `tenants/<tenant>/jobs/<job>/<id>.png`), `content_type` (`image/png`, `image/jpeg`), size_bytes, `uploaded_at` (nulo até o servidor ver o objeto no S3), created_at.

## Filas

### `queues` (M5)
id, tenant_id, name (único por tenant, **imutável**, formato `[a-z0-9-]+`, igual ao usado no código do robô), data_mode (`reference`, `central`; `e2e` reservado), max_attempts int default 3, lease_seconds int default 300, retention_days int default 30 (7 a 365), visible_fields text[].

Só a equipe Artemisys cria filas e altera `data_mode` e `max_attempts`; o Admin do cliente altera apenas `visible_fields` e `retention_days`.

### `queue_items` (M5)
| Coluna | Tipo | Notas |
|---|---|---|
| tenant_id, queue_id | uuid | |
| batch_id | uuid | nulo se criado fora de lote |
| reference | text | identificador neutro, único por fila |
| status | text | `new`, `in_progress`, `successful`, `failed`, `abandoned` |
| priority | int | maior primeiro |
| payload_enc | bytea | modo `central` |
| payload_ref | jsonb | modo `reference` (ex.: arquivo + id da linha) |
| visible_data | jsonb | apenas campos de `visible_fields` |
| key_id | text | chave usada na criptografia |
| attempt_count | int | continua contando após reprocessar |
| failure_type | text | `business`, `application` |
| row_number | int | linha da planilha de origem, quando veio de lote |
| action_hint | jsonb | `{title, text}` montado pelo backend a partir do último motivo |
| error_message | text | mascarada |
| output | jsonb | resultado não sensível |
| locked_by_job | uuid | |
| worker_slot | int | |
| locked_until | timestamptz | trava com prazo |
| defer_until | timestamptz | |
| started_at, finished_at | timestamptz | |

Índice parcial para a retirada: `(queue_id, priority desc, created_at) where status = 'new'`.

### `item_attempts` (M5)
id, tenant_id, item_id, number, job_id, machine_id, started_at, finished_at, status (`successful`, `failed`, `abandoned`), failure_type, reason (texto curto para pessoas). Reprocessar mantém as tentativas anteriores.

### `batches` (M6)
id, tenant_id, queue_id, short_code (ex.: `LOTE-0012`), source_name, source_sha256, rows_read, rows_enqueued, rows_rejected, status (`open`, `closed`, `completed`, `cancelled`), created_by, closed_at.

Entrada: `.xlsx` ou `.csv` até 10 MB, cabeçalho na primeira linha. "Cancelar lote" cancela só os itens ainda `new`.

Restrição ao fechar: `rows_read = rows_enqueued + rows_rejected`.

### `batch_rejections` (M6)
tenant_id, batch_id, row_number, reference, reason_code (`duplicate_in_batch`, `empty_reference`, `already_successful`), related_row (para duplicadas).

## Agenda, alertas e segredos

### `schedules` (M7)
id, tenant_id, bot_id, cron, timezone (padrão `America/Sao_Paulo`), params jsonb, is_active, next_fire_at, last_fired_at.

### `alerts` (M7)
id, tenant_id, target_type (`bot`, `pool`, `queue`), target_id, event (`job_failed`, `job_pending_30m`, `machine_offline_15m`, `items_failed_or_abandoned`, `item_abandoned`), channel (`email`), destination, is_active, last_sent_at.

### `notifications` (M7)
id, tenant_id, user_id, kind, title, body, link, read_at, created_at. Mesmo conteúdo dos alertas, por usuário; guardadas por 30 dias.

### `secrets` (M5)
id, tenant_id, name, ciphertext bytea, key_id, created_by, rotated_at. Entregues ao agente somente junto com o job que precisa deles.

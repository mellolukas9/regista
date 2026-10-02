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
id, tenant_id, name, kind (`on_prem`, `client_cloud`, `internal`), description.

### `machines` (M2)
id, tenant_id, pool_id, name, public_key (bytea, Ed25519), mode (`service`, `session`, `oneshot`), status (`pending`, `online`, `offline`, `revoked`), last_seen_at, agent_version, os_info jsonb, max_concurrency int (paralelismo de itens dentro de uma execução), revoked_at.

Regras: `offline` após **2 minutos** sem sinal; volta a `online` no próximo sinal. **Uma execução por máquina por vez.**

### `machine_events` (M2)
id, tenant_id, machine_id, kind (`enrolled`, `first_signal`, `went_offline`, `came_back`, `agent_updated`, `revoked`), metadata jsonb, created_at. Alimenta o histórico da máquina.

### `enrollment_keys` (M2)
id, tenant_id, machine_id, key_hash, expires_at, used_at, created_by.

### `bots` (M3)
id, tenant_id, pool_id, name, description, concurrency int default 1, is_active, current_version_id (M4).

### `bot_versions` (M4)
id, tenant_id, bot_id, version text, package_sha256, signature bytea, storage_key, size_bytes, release_note text, created_by. Só a equipe Artemisys publica versões.

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
| items_successful, items_failed, items_abandoned, items_total | int | contagem para a coluna Itens |

Regras: `failed` quando qualquer item termina com falha, mesmo que o robô encerre normalmente. "Executar agora" com execução ativa do mesmo bot cria outro job `pending` (não bloqueia). Cancelar só em `pending`, `assigned` ou `running`.

### `job_logs` (M3), particionada por mês em `ts`
tenant_id, job_id, item_id (nulo), item_ref (nulo), attempt (nulo), ts, level, message (limite de tamanho), extra jsonb.

### `artifacts` (M3)
id, tenant_id, job_id, item_id, attempt_id, kind (`screenshot`, `file`), storage_key, size_bytes, created_at.

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

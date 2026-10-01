# Modelo de dados

Convenções:

- Toda tabela de cliente tem `tenant_id uuid not null` referenciando `tenants(id)`, índice que começa por `tenant_id` e política RLS (ver `security.md`).
- Chave primária `id uuid` (v7 gerado na aplicação). `created_at` e `updated_at` em `timestamptz` UTC.
- Enumerações como `text` com `CHECK` (mais simples de migrar que tipos `enum` do Postgres).
- Nomes de tabelas e colunas em inglês, `snake_case`.

O marco em que cada tabela nasce está entre parênteses.

## Identidade e acesso

### `tenants` (M0)
| Coluna | Tipo | Notas |
|---|---|---|
| id | uuid | |
| name | text | |
| slug | text unique | |
| data_region | text | ex.: `sa-east-1` |
| is_active | bool | |

A política de `tenants` usa `id` no lugar de `tenant_id`: a sessão enxerga apenas o próprio tenant, exceto administradores da plataforma.

### `users` (M1)
| Coluna | Tipo | Notas |
|---|---|---|
| tenant_id | uuid | |
| email | citext | único por tenant |
| password_hash | text | argon2id |
| role | text | `tenant_admin`, `operator`, `viewer` |
| is_platform_admin | bool | equipe Artemisys |
| mfa_secret_enc | bytea | criptografado via KeyProvider |
| mfa_enabled | bool | |
| failed_logins | int | |
| locked_until | timestamptz | |
| last_login_at | timestamptz | |

### `sessions` (M1)
id, tenant_id, user_id, token_hash (sha256 do token do cookie), csrf_token_hash, created_at, last_seen_at, expires_at, revoked_at, ip, user_agent, active_tenant_id (contexto escolhido por administradores da plataforma).

### `audit_log` (M1)
id, tenant_id, actor_type (`user`, `machine`, `system`), actor_id, action (ex.: `job.triggered`, `machine.revoked`), target_type, target_id, metadata jsonb, ip, created_at. Somente inserção (o role da aplicação não tem `UPDATE`/`DELETE`).

## Execução

### `pools` (M2)
id, tenant_id, name, kind (`on_prem`, `client_cloud`, `internal`), description.

### `machines` (M2)
id, tenant_id, pool_id, name, public_key (bytea, Ed25519), mode (`service`, `session`, `oneshot`), status (`pending`, `online`, `offline`, `revoked`), last_seen_at, agent_version, os_info jsonb, max_concurrency int, revoked_at.

### `enrollment_keys` (M2)
id, tenant_id, machine_id, key_hash, expires_at, used_at, created_by.

### `bots` (M3)
id, tenant_id, pool_id, name, description, concurrency int default 1, is_active, current_version_id (M4).

### `bot_versions` (M4)
id, tenant_id, bot_id, version text, package_sha256, signature bytea, storage_key, size_bytes, created_by.

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

### `job_logs` (M3), particionada por mês em `ts`
tenant_id, job_id, item_id (nulo), ts, level, message (limite de tamanho), extra jsonb.

### `artifacts` (M3)
id, tenant_id, job_id, item_id, kind (`screenshot`, `file`), storage_key, size_bytes, created_at.

## Filas

### `queues` (M5)
id, tenant_id, name (único por tenant), data_mode (`reference`, `central`; `e2e` reservado), max_retries int default 3, lease_seconds int default 300, retention_days int default 30, visible_fields text[].

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
| retry_count | int | |
| exception_type | text | `business`, `application` |
| error_message | text | mascarada |
| output | jsonb | resultado não sensível |
| locked_by_job | uuid | |
| worker_slot | int | |
| locked_until | timestamptz | trava com prazo |
| defer_until | timestamptz | |
| started_at, finished_at | timestamptz | |

Índice parcial para a retirada: `(queue_id, priority desc, created_at) where status = 'new'`.

### `batches` (M6)
id, tenant_id, queue_id, source_name, source_sha256, rows_read, rows_enqueued, rows_rejected, status (`open`, `closed`, `completed`, `cancelled`), created_by, closed_at.

Restrição ao fechar: `rows_read = rows_enqueued + rows_rejected`.

### `batch_rejections` (M6)
tenant_id, batch_id, row_ref, reason.

## Agenda, alertas e segredos

### `schedules` (M7)
id, tenant_id, bot_id, cron, timezone (padrão `America/Sao_Paulo`), params jsonb, is_active, next_fire_at, last_fired_at.

### `alerts` (M7)
id, tenant_id, target_type (`bot`, `queue`, `machine`, `tenant`), target_id, channel (`email`), destination, events text[], is_active.

### `secrets` (M5)
id, tenant_id, name, ciphertext bytea, key_id, created_by, rotated_at. Entregues ao agente somente junto com o job que precisa deles.

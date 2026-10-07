# ADR 0020: Distribuição de jobs: canal único, escuta dedicada e partições de logs

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O M3 entrega jobs aos agentes por long-polling (ADR 0006), acordado por `LISTEN/NOTIFY` (ADR 0002). A spec dizia `LISTEN jobs_<tenant>`: um canal por cliente. Isso exigiria `LISTEN`/`UNLISTEN` dinâmico, nomes de canal montados com uuid e uma conexão por cliente. Além disso, muitos agentes esperando ao mesmo tempo não podem esgotar o pool de conexões, e `job_logs` é particionada por mês e precisa de uma partição para cada mês em que se grava.

## Decisão

1. **Canal único `regista_jobs`.** Um gatilho `AFTER INSERT` em `jobs` faz `pg_notify('regista_jobs', '<tenant_id>:<pool_id>')` (só ids). A rota `GET /agent/jobs/next` filtra por pool e cliente no banco; o canal serve só para acordar.
2. **Uma conexão asyncpg dedicada por processo da API**, fora do pool do SQLAlchemy, com o role `regista_app`, que faz `LISTEN regista_jobs`. Cada requisição que não achou job se registra num mapa em memória `(tenant, pool) → eventos` e espera sem segurar conexão do pool. Se a conexão de escuta cair, ela reconecta com backoff e acorda todos os esperando (eles conferem no banco, então um aviso perdido nunca trava um job). Há um teto de esperas por processo (503 com `Retry-After` acima dele). Incompatível com PgBouncer em modo transação, como já registrado.
3. **Retirada atômica** com `FOR UPDATE SKIP LOCKED`, filtrando por `tenant_id` e `pool_id` da máquina, e o banco garante uma execução por máquina com um índice único parcial em `jobs(machine_id)` para `assigned`/`running`.
4. **Partições de `job_logs`** por mês, sem partição `DEFAULT`. A função `app.ensure_job_log_partitions(meses)` (`SECURITY DEFINER`, dono `regista_owner`, `search_path` fixo, `EXECUTE` só para `regista_app`, nome calculado dentro da função, sem entrada de texto) cria as partições do mês atual em diante; a migration cria o mês atual e os 3 seguintes, e uma tarefa diária do worker repete. Ela é DDL fixa, não leitura entre clientes, e entra na lista de funções `SECURITY DEFINER` das ADRs 0017 e 0018, ao lado de `app.job_logs_partition_status()` (leitura do catálogo). Faltando a partição, a gravação falha de forma explícita (503 e log ERROR; o agente reenvia) e `/health` indica degradação quando falta a do mês seguinte.

## Consequências

Mil agentes esperando custam mil corrotinas e uma conexão. Uma queda da conexão de escuta só atrasa a entrega até o agente repetir a requisição. Se o worker ficar parado por mais de 3 meses, as gravações de log falham até a partição ser criada (o `/health` avisa um mês antes).

## Alternativas descartadas

Um canal por cliente (conexões e `LISTEN` dinâmicos); uma conexão por requisição esperando (esgota o pool); polling curto sem `NOTIFY` (latência e carga); partição `DEFAULT` (impede criar a partição do mês depois, se já houver linhas nela); criar a partição dentro da própria gravação (daria ao role da aplicação DDL).

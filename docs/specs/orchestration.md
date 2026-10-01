# Orquestração

Existem duas filas com papéis diferentes:

- **Fila de jobs:** qual robô deve rodar, em qual pool, com quais parâmetros. Consumida pelos agentes.
- **Fila de itens:** o trabalho de negócio (cada linha de planilha, cada processo). Consumida pelos robôs via SDK.

Um job pode processar um item ou centenas, em paralelo.

## Ciclo de vida de um job

```
pending ──(agente assume)──► assigned ──(robô inicia)──► running ──► completed
   │                             │                          ├──────► failed
   └──────── cancelled ◄─────────┴────── (cancelamento) ────┘
```

- Agente assume o job de forma atômica (`FOR UPDATE SKIP LOCKED`) filtrando por `pool_id` da máquina e `tenant_id`.
- Máquina que para de enviar heartbeat com job em `assigned`/`running`: o job vira `failed` com `error_code = 'machine_lost'` e os itens travados seguem a regra de abandono.
- Cancelamento: o painel grava `cancel_requested_at`; o agente consulta no heartbeat, encerra o robô e o SDK libera os itens em andamento.

## Distribuição de jobs para o agente

`GET /agent/jobs/next?wait=30` é um long-polling:

1. Tenta assumir um job pendente do pool da máquina.
2. Se não houver, a requisição aguarda até `wait` segundos ouvindo `LISTEN jobs_<tenant>` (um `NOTIFY` é emitido ao criar job).
3. Responde `204` se nada chegou; o agente repete.

`LISTEN` exige uma conexão asyncpg dedicada e persistente, fora do pool comum (e incompatível com PgBouncer em modo transação).

## Ciclo de vida de um item

```
new ──► in_progress ──► successful
          │  ├────────► failed (business)        ── fim
          │  ├────────► failed (application) ──► new (se ainda há tentativas)
          │  ├─ trava venceu ──► abandoned ─────► new (se ainda há tentativas)
          │  └─ release() ────────────────────► new (não conta tentativa)
```

### Retirada atômica

```sql
UPDATE queue_items
SET status = 'in_progress', locked_by_job = :job, worker_slot = :slot,
    locked_until = now() + make_interval(secs => :lease), started_at = now()
WHERE id = (
  SELECT id FROM queue_items
  WHERE queue_id = :queue AND status = 'new'
    AND (defer_until IS NULL OR defer_until <= now())
  ORDER BY priority DESC, created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;
```

### Trava com prazo (lease)

- O SDK renova `locked_until` em segundo plano (a cada `lease_seconds / 3`).
- Tarefa interna periódica: itens `in_progress` com `locked_until < now()` viram `abandoned` e, se `retry_count < max_retries`, voltam para `new`.
- Renovação ou conclusão de um item cuja trava já pertence a outro job é rejeitada (`409`).

### Regras de nova tentativa

| Situação | Comportamento |
|---|---|
| Falha de aplicação (timeout, site fora) | `retry_count += 1`; volta para `new` com `defer_until` crescente (ex.: 1, 5, 15 min) até `max_retries` |
| Falha de negócio (dado inválido) | Final. Não tenta de novo |
| Abandonado (máquina caiu) | Igual à falha de aplicação |
| Liberado (`release`, cancelamento) | Volta para `new` sem contar tentativa |

## Concorrência no robô (Playwright async)

Um browser por job, um `BrowserContext` por item, N tarefas asyncio (N = `bots.concurrency`, limitado por `machines.max_concurrency`).

```python
async def worker(browser, queue):
    while item := await regista.next_item(queue):
        ctx = await browser.new_context()
        async with regista.item_scope(item):      # trava, renovação, logs com item_id
            page = await ctx.new_page()
            try:
                await process(page, item)
                await item.success(output={...})
            except BusinessError as e:
                await item.fail(e, business=True)
            except Exception as e:
                await item.fail(e, screenshot=await page.screenshot())
        await ctx.close()

await asyncio.gather(*(worker(browser, "acordos") for _ in range(regista.concurrency())))
```

- `item_scope` define um `contextvars.ContextVar` com o `item_id`; o logger do SDK anexa automaticamente.
- Cancelamento (`asyncio.CancelledError`) libera o item com `release()`.
- O SDK usa `httpx.AsyncClient` com conexão reaproveitada; nenhuma chamada síncrona.
- Idempotência: quando reprocessar for perigoso no sistema de destino, o robô verifica se a `reference` já foi efetivada antes de agir.

## Lotes e reconciliação

- Uma planilha (ou fonte) vira um `batch`. O robô dispatcher lê a fonte e chama `add_item` ou `reject_row` para cada linha.
- **Invariante:** `rows_read = rows_enqueued + rows_rejected`. O lote não fecha se não bater.
- A `reference` vem de uma coluna identificadora estável da fonte. Nunca usar número de linha.
- Relatório do lote: referência, status, tentativas, data, máquina, motivo; resumo no topo; reprocessar só falhas; exportar CSV/XLSX.
- Consolidação: ao concluir o lote, um passo no ambiente do cliente grava Status, Data e Mensagem numa cópia da planilha original. Nunca escrever na planilha durante o processamento paralelo.

## Agendamento

- Laço no backend a cada ~15 s: adquire `pg_try_advisory_lock(<constante>)`; somente quem obtém o lock dispara.
- Para cada `schedule` ativo com `next_fire_at <= now()`: cria o job (`trigger = 'schedule'`) e recalcula `next_fire_at` com `croniter` no fuso do agendamento, na mesma transação.
- Disparos perdidos durante indisponibilidade: dispara uma vez ao voltar (sem acumular).

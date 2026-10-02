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
- **Uma execução por máquina por vez.** Máquina com job `assigned`/`running` não recebe outro. Sem máquina `online` livre no pool, o job fica `pending`. Execuções do mesmo bot podem rodar em paralelo em máquinas diferentes.
- O job termina `failed` se qualquer item terminar com falha, mesmo que o robô encerre normalmente.
- Máquina que fica sem sinal com job `assigned`/`running`: o job vira `failed` com `error_code = 'machine_lost'`.
- Cancelamento (`pending`, `assigned`, `running`): o painel grava `cancel_requested_at`; o agente consulta no heartbeat e o robô para **antes do próximo item**. Itens já concluídos ficam como estão.
- **Ao terminar um job por qualquer motivo**, o backend move todo item ainda `in_progress` daquele job para `abandoned`.

## Distribuição de jobs para o agente

`GET /agent/jobs/next?wait=30` é um long-polling:

1. Tenta assumir um job pendente do pool da máquina.
2. Se não houver, a requisição aguarda até `wait` segundos ouvindo `LISTEN jobs_<tenant>` (um `NOTIFY` é emitido ao criar job).
3. Responde `204` se nada chegou; o agente repete.

`LISTEN` exige uma conexão asyncpg dedicada e persistente, fora do pool comum (e incompatível com PgBouncer em modo transação).

## Ciclo de vida de um item

```
new ──► in_progress ──► successful
          ├──► falha de aplicação ──► nova tentativa na mesma execução (até max_attempts)
          │                           └─ esgotou ──► failed (application)
          ├──► failed (business)          (sem nova tentativa)
          └──► abandoned                  (execução terminou com o item em andamento,
                                           ou a trava venceu)

failed / abandoned ──(Reprocessar)──► new   (mantém tentativas; o contador continua)
```

Cada tentativa é registrada em `item_attempts` (número, início, fim, máquina, execução, status, `failure_type`, motivo curto, capturas). O backend monta `action_hint` a partir do último motivo.

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
- Tarefa interna periódica: itens `in_progress` com `locked_until < now()` viram `abandoned`. **Não voltam sozinhos para a fila**: só por "Reprocessar" (decisão de produto, `design-system.md` §11.2).
- Renovação ou conclusão de um item cuja trava já pertence a outro job é rejeitada (`409`).

### Regras de nova tentativa

| Situação | Comportamento |
|---|---|
| Falha de aplicação (timeout, site fora) | Nova tentativa automática **na mesma execução**, com pequena espera crescente, até `max_attempts`; esgotou → `failed` (application) |
| Falha de negócio (dado inválido) | `failed` (business). Não tenta de novo |
| Abandonado (execução terminou ou trava venceu) | `abandoned`. Volta só por "Reprocessar" |
| Liberado (`release`) | Volta para `new` sem contar tentativa (uso interno do SDK, ex.: robô encerrando antes de começar o item) |
| Reprocessar (item, falhas da fila, falhas do lote) | Aceita `failed` e `abandoned`; volta para `new`; mantém tentativas anteriores |

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
- Cancelamento: o robô termina o item em andamento e não pega o próximo. Interrupção forçada deixa o item para o backend marcar como `abandoned`.
- O SDK usa `httpx.AsyncClient` com conexão reaproveitada; nenhuma chamada síncrona.
- Idempotência: quando reprocessar for perigoso no sistema de destino, o robô verifica se a `reference` já foi efetivada antes de agir.

## Lotes e reconciliação

- Uma planilha (ou fonte) vira um `batch`. Duas formas de criar:
  - **"Enviar planilha" no painel** (`.xlsx`/`.csv` até 10 MB, cabeçalho na primeira linha);
  - **robô dispatcher** no ambiente do cliente, via SDK (`add_item` / `reject_row`).
- Rejeições padronizadas: referência repetida no lote (indicando a outra linha), referência vazia, item já existente na fila com `successful`.
- Se a reconciliação não fechar, a API devolve a diferença ("N linhas sem destino").
- No relatório, "Não processadas" = itens `abandoned` ou cancelados.
- **Invariante:** `rows_read = rows_enqueued + rows_rejected`. O lote não fecha se não bater.
- A `reference` vem de uma coluna identificadora estável da fonte. Nunca usar número de linha.
- Relatório do lote: referência, status, tentativas, data, máquina, motivo; resumo no topo; reprocessar só falhas; exportar CSV/XLSX.
- Consolidação: ao concluir o lote, um passo no ambiente do cliente grava Status, Data e Mensagem numa cópia da planilha original. Nunca escrever na planilha durante o processamento paralelo.

## Agendamento

- Laço no backend a cada ~15 s: adquire `pg_try_advisory_lock(<constante>)`; somente quem obtém o lock dispara.
- Para cada `schedule` ativo com `next_fire_at <= now()`: cria o job (`trigger = 'schedule'`) e recalcula `next_fire_at` com `croniter` no fuso do agendamento, na mesma transação.
- Disparos perdidos durante indisponibilidade: dispara uma vez ao voltar (sem acumular).

## Atualização do painel

O painel consulta a API a cada 15 s (TanStack Query). As respostas trazem a hora da última atualização, que alimenta o indicador "Sincronizado · há Ns"; sem resposta por 2 min, "Atualização atrasada"; servidor fora, "Sem conexão". SSE/WebSocket não fazem parte do MVP.

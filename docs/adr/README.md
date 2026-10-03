# Registro de decisões de arquitetura (ADR)

Cada arquivo registra uma decisão: contexto, decisão, consequências e alternativas. Para mudar uma decisão, crie uma nova ADR que a substitua e marque a antiga como `substituída por NNNN`.

| ADR | Decisão |
|---|---|
| [0001](0001-monorepo-e-convencoes.md) | Monorepo e convenções |
| [0002](0002-postgres-como-fila-e-fonte-de-verdade.md) | Remover o Prefect; PostgreSQL como fila e fonte de verdade |
| [0003](0003-sem-redis.md) | Sem Redis |
| [0004](0004-multi-tenancy-com-rls.md) | Isolamento entre clientes com Row-Level Security |
| [0005](0005-autenticacao-propria-no-fastapi.md) | Autenticação própria no FastAPI |
| [0006](0006-agente-pull-https.md) | Agente pull-only por HTTPS com long-polling |
| [0007](0007-identidade-da-maquina-ed25519.md) | Identidade da máquina por par de chaves Ed25519 |
| [0008](0008-pacotes-assinados-e-uv.md) | Pacotes de robô assinados e ambientes isolados com uv |
| [0009](0009-pools-de-execucao.md) | Pools de execução em vez de máquina fixa |
| [0010](0010-modos-do-agente.md) | Modos do agente: serviço, sessão de usuário e único |
| [0011](0011-filas-de-itens-com-lease.md) | Filas de itens com trava com prazo |
| [0012](0012-lotes-com-reconciliacao.md) | Lotes com reconciliação |
| [0013](0013-modos-de-dados.md) | Modos de dados por fila |
| [0014](0014-execucao-no-ambiente-do-cliente.md) | Execução no ambiente do cliente primeiro |
| [0015](0015-agendador-proprio.md) | Agendador próprio com croniter e advisory lock |
| [0016](0016-tarefas-internas-procrastinate.md) | Tarefas internas com Procrastinate |
| [0017](0017-ajustes-na-autenticacao.md) | Ajustes na autenticação (MFA para todos, convite como recuperação, tenant interno) |

## Modelo

```markdown
# ADR NNNN: Título

- **Status:** proposta | aceita | substituída por NNNN
- **Data:** AAAA-MM

## Contexto
## Decisão
## Consequências
## Alternativas descartadas
```

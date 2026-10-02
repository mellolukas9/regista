# Frontend

A referência completa da interface é **`docs/specs/design-system.md`** (handoff de design): tokens prontos para o `@theme` do Tailwind v4, status e rótulos, matriz de permissões, inventário de componentes com base shadcn/ui, shell, todas as telas com rotas e textos exatos, versão mobile, glossário de ações, dados de exemplo e decisões de produto (seção 11).

Este arquivo só registra como usar o handoff.

## Regras

- Visual e textos: siga o `design-system.md` **exatamente** (tokens, rótulos, nomes de ações, estados vazios e de erro). Não invente cores, rótulos ou textos.
- A cor de destaque é o **azul Artemisys** (`--color-accent: #2577B8`). Qualquer menção a verde-limão em material antigo (como `docs/apresentacao/`) está superada.
- Stack: Next.js (App Router) + Tailwind v4 (CSS-first, `@theme`) + shadcn/ui + TanStack Table + Recharts + lucide-react + fontes Geist via `next/font`.
- Implemente as telas **junto com o marco** que entrega o backend delas (ver `ROADMAP.md`), não em bloco separado.
- O M0 entrega só os tokens, as fontes e o shell vazio (sidebar + topbar sem dados). Componentes shadcn entram conforme as telas pedirem.
- Permissões aparecem na interface (botão oculto sem permissão), mas **são aplicadas no servidor**. Esconder botão não substitui a checagem na API.
- Detalhes abrem em página própria (sem drawer). Filtros, ordenação e paginação ficam na URL e são processados no servidor.
- Atualização por polling a cada 15 s (TanStack Query), com o indicador "Sincronizado · há Ns".
- Nenhum token ou segredo em `localStorage`; autenticação só por cookie httpOnly. Todo texto vindo de logs, erros ou payloads é exibido escapado.

## Termos da interface x entidades do código

| Interface | Código / API |
|---|---|
| Cliente | `tenant` |
| Execução | `job` (rotas da interface em `/runs`) |
| Máquina | `machine` |
| Pool | `pool` |
| Fila | `queue` (fila de itens) |
| Item | `queue_item` |
| Tentativa | `item_attempt` |
| Lote | `batch` |
| Agendamento | `schedule` |
| Admin do cliente / Operador / Leitor | `tenant_admin` / `operator` / `viewer` |
| Equipe Artemisys | usuário com `is_platform_admin` |

## Telas por marco

| Marco | Telas (seção 7 do design-system) |
|---|---|
| M0 | Shell vazio (sidebar, topbar), tokens, fontes |
| M1 | Login e MFA (7.1), Clientes (7.16), Usuários (7.17), Minhas sessões (7.18) |
| M2 | Máquinas e pools (7.13), Detalhe da máquina (7.14) |
| M3 | Bots (7.5), Detalhe do bot (7.6, sem versões), Execuções (7.3), Detalhe da execução (7.4), Dashboard inicial (7.2) |
| M4 | Aba de versões do Detalhe do bot (7.6) |
| M5 | Filas (7.8), Detalhe da fila (7.9), Detalhe do item (7.10) |
| M6 | Lotes (7.11), Relatório do lote (7.12) |
| M7 | Agendamentos (7.7 e aba do bot), Alertas (7.15), notificações do sino, Dashboard completo |

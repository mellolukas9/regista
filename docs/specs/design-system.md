# Regista v2 — Handoff de interface

Documento para implementar o frontend do Regista v2 sem acesso ao canvas de design. Siga na ordem: tokens → componentes → shell → telas. A seção 11 lista as decisões de produto que o backend e o agente precisam cumprir para as telas funcionarem.

- Stack: Next.js (App Router) + Tailwind v4 + shadcn/ui + TanStack Table + Recharts + lucide-react.
- Fontes: Geist (interface) e Geist Mono (IDs, filas, horários, logs, cron), via `next/font` (`geist/font/sans` e `geist/font/mono`).
- Tema: só escuro por enquanto. Toda cor sai de um token com nome de função, para que um tema claro troque apenas os valores.
- Idioma: português do Brasil em toda a interface. Use exatamente os textos deste documento.

---

## 1. Regras gerais

1. Status nunca só por cor: sempre ponto + texto (StatusPill). A forma do ponto também informa (cheio, vazado, pulsante).
2. Contraste mínimo 4.5:1 em todo texto. Os tokens abaixo já passam; não invente cores novas para texto.
3. Alvos clicáveis com no mínimo 44 × 44px, inclusive ícones e ações dentro de tabelas. Não existe tamanho "sm" de botão.
4. Foco de teclado sempre visível (`--ring`).
5. A mesma ação tem o mesmo nome em todo lugar (ver Glossário de ações, seção 9).
6. Ações destrutivas (revogar máquina, cancelar lote, excluir, remover acesso) sempre com AlertDialog. As irreversíveis pedem para digitar o nome do alvo.
7. Estados vazios dizem o que fazer a seguir. Erros dizem o que aconteceu e como resolver.
8. Detalhes abrem em página própria (não usar drawer). O link de volta preserva os filtros da lista.
9. Filtros, ordenação e paginação de tabelas ficam na URL (`?status=failed&sort=-updated_at&page=2`).
10. Fuso de exibição: `America/Sao_Paulo`. Datas `dd/MM/yyyy HH:mm`; nas listas, `dd/MM HH:mm`; relativos ("há 12 min") com Tooltip mostrando a data completa.
11. Nunca exibir CPF nem nome de pessoa em telas de cliente. Usuários de cliente aparecem pelo e-mail.
12. `prefers-reduced-motion`: pontos pulsantes e skeleton ficam parados.

---

## 2. Tokens (Tailwind v4)

Cole em `app/globals.css`.

```css
@import "tailwindcss";

@theme {
  /* Fontes */
  --font-sans: var(--font-geist-sans), ui-sans-serif, system-ui, sans-serif;
  --font-mono: var(--font-geist-mono), ui-monospace, monospace;

  /* Superfícies */
  --color-bg: #0A0C0F;              /* fundo da aplicação */
  --color-sidebar: #0D1014;         /* sidebar, fundo de campo, cabeçalho de tabela */
  --color-panel: #111419;           /* cards, tabelas, dialogs */
  --color-panel-active: #161A21;    /* item ativo, botão secundário, menus */
  --color-row-hover: #13171D;       /* hover de linha */
  --color-control-hover: #1C212A;   /* hover de botão secundário */
  --color-log: #07090C;             /* fundo do LogViewer */

  /* Bordas */
  --color-border: #1C2129;
  --color-border-control: #232A34;
  --color-border-hover: #2E3642;
  --color-border-strong: #3A4351;   /* checkbox vazio, ponto de etapa futura */

  /* Texto (contraste sobre bg / panel) */
  --color-text: #E7EAEE;            /* 16.2 / 15.3 */
  --color-text-secondary: #C9CFD8;  /* 12.5 / 11.8 */
  --color-text-label: #98A2B3;      /*  7.6 /  7.2 */
  --color-text-muted: #7D8796;      /*  5.4 /  5.1 — mínimo 11px */

  /* Destaque (azul Artemisys) */
  --color-accent: #2577B8;          /* só preenchimento, texto branco por cima (4.8:1) */
  --color-accent-hover: #1F6FA8;    /* azul exato do site (5.4:1 com branco) */
  --color-accent-fg: #FFFFFF;
  --color-accent-text: #5AA9E6;     /* links, ícone/aba ativa, foco (7.7:1) */
  --color-accent-text-hover: #8CC4F0;

  /* Status */
  --color-success: #22C55E;
  --color-warning: #F59E0B;
  --color-running: #9B7BFF;
  --color-danger: #F2564B;
  --color-danger-hover: #F46B61;
  --color-danger-fg: #0A0C0F;       /* texto sobre botão de perigo (5.8:1) */
  --color-danger-chart: #EF4444;    /* só barras de gráfico */
  --color-neutral: #98A2B3;

  /* Textos claros sobre fundos tingidos (banners e logs) */
  --color-warning-text: #FBBF24;
  --color-warning-body: #D2C29A;
  --color-danger-text: #FF8F86;
  --color-log-warn: #FCD34D;
  --color-log-error: #FFB3AB;

  /* Raio */
  --radius-sm: 6px;       /* badge, kbd, segmento interno */
  --radius-control: 10px; /* botões, campos, menus, toasts */
  --radius-card: 14px;    /* cards, tabelas, dialogs */
  --radius-pill: 999px;   /* StatusPill */

  /* Elevação */
  --shadow-popover: 0 8px 24px rgba(0, 0, 0, 0.45);  /* menu, select, tooltip, toast */
  --shadow-overlay: 0 24px 64px rgba(0, 0, 0, 0.60); /* dialog */
  --color-scrim: rgba(5, 6, 8, 0.72);

  /* Escala tipográfica: tamanho / altura de linha / peso / tracking */
  --text-kpi: 36px;      --text-kpi--line-height: 40px;      --text-kpi--font-weight: 600;  --text-kpi--letter-spacing: -0.03em;
  --text-display: 28px;  --text-display--line-height: 34px;  --text-display--font-weight: 600; --text-display--letter-spacing: -0.02em;
  --text-title-lg: 20px; --text-title-lg--line-height: 28px; --text-title-lg--font-weight: 600;
  --text-title: 15px;    --text-title--line-height: 22px;    --text-title--font-weight: 600;
  --text-body: 14px;     --text-body--line-height: 20px;
  --text-body-sm: 13px;  --text-body-sm--line-height: 18px;
  --text-caption: 12px;  --text-caption--line-height: 16px;
  --text-overline: 11px; --text-overline--line-height: 16px; --text-overline--letter-spacing: 0.08em; /* + uppercase, 500 */
}

/* Ponte para os nomes que o shadcn/ui espera */
:root {
  --background: var(--color-bg);
  --foreground: var(--color-text);
  --card: var(--color-panel);
  --card-foreground: var(--color-text);
  --popover: var(--color-panel-active);
  --popover-foreground: var(--color-text);
  --primary: var(--color-accent);
  --primary-foreground: var(--color-accent-fg);
  --secondary: var(--color-panel-active);
  --secondary-foreground: var(--color-text);
  --muted: var(--color-panel-active);
  --muted-foreground: var(--color-text-label);
  --accent: var(--color-panel-active);
  --accent-foreground: var(--color-text);
  --destructive: var(--color-danger);
  --border: var(--color-border);
  --input: var(--color-border-control);
  --ring: var(--color-accent-text);
  --radius: 10px;
}

/* Tema claro futuro: redefinir apenas as variáveis do @theme aqui. */
/* [data-theme="light"] { --color-bg: …; } */

@layer base {
  html { color-scheme: dark; }
  body { @apply bg-bg text-text font-sans antialiased; }
  a { color: var(--color-accent-text); }
  a:hover { color: var(--color-accent-text-hover); }
  :focus-visible {
    outline: none;
    box-shadow: 0 0 0 2px var(--color-bg), 0 0 0 4px var(--color-accent-text);
  }
  .tabular { font-variant-numeric: tabular-nums; }
}

@media (prefers-reduced-motion: reduce) {
  .animate-pulse-dot, .animate-skeleton { animation: none !important; }
}
```

Espaçamento: use a escala padrão do Tailwind (base 4px).
- Gap entre controles: 12px.
- Gap entre cards: 16px.
- Padding de card: 20px.
- Gap entre blocos da página: 24px.
- Margem lateral da página: 40px no desktop e 16px abaixo de 900px.

Status no StatusPill:
- texto = cor do status;
- fundo = mesma cor a 14% (`color-mix(in srgb, var(--color-success) 14%, transparent)`);
- borda = mesma cor a 40%.

---

## 3. Status, rótulos e tons

Centralize em `lib/status.ts`. Use estes valores e rótulos exatamente.

Forma do ponto:
- `solid`: estado final ou estável.
- `hollow`: aguardando.
- `pulse`: acontecendo agora.

| Entidade | Valor | Rótulo | Tom | Ponto |
|---|---|---|---|---|
| job | `pending` | Pendente | warning | hollow |
| job | `assigned` | Atribuído | running | hollow |
| job | `running` | Executando | running | pulse |
| job | `completed` | Concluído | success | solid |
| job | `failed` | Falhou | danger | solid |
| job | `cancelled` | Cancelado | neutral | solid |
| item | `new` | Novo | neutral | hollow |
| item | `in_progress` | Em andamento | running | pulse |
| item | `successful` | Sucesso | success | solid |
| item | `failed` | Falhou (+ "Negócio" ou "Aplicação") | danger | solid |
| item | `abandoned` | Abandonado | warning | solid |
| machine | `pending` | Aguardando cadastro | warning | hollow |
| machine | `online` | Online | success | solid |
| machine | `offline` | Sem sinal | danger | solid |
| machine | `revoked` | Revogada | neutral | solid |
| batch | `open` | Aberto | running | hollow |
| batch | `closed` | Fechado | running | solid |
| batch | `completed` | Concluído | success | solid |
| batch | `cancelled` | Cancelado | neutral | solid |

`failure_type` do item: `business` → "Negócio" e `application` → "Aplicação". O tipo aparece dentro da pill, depois de um divisor: `Falhou | Negócio`.

Regras de negócio que a interface assume:
- **Execução "Falhou":** quando o robô termina com algum item com falha.
- **Falha de aplicação:** gera nova tentativa automática até o máximo da fila.
- **Falha de negócio:** não tenta de novo.
- **Item abandonado:** item que estava em andamento quando a execução terminou. No lote ele conta como "não processado".
- **"Reprocessar falhas":** inclui itens Falhou e Abandonado.

---

## 4. Papéis e permissões

| Ação / tela | Artemisys (admin da plataforma) | Admin do cliente (`tenant_admin`) | Operador (`operator`) | Leitor (`viewer`) |
|---|---|---|---|---|
| Ver todos os clientes, seletor de cliente, tela Clientes | ✔ | — | — | — |
| Novo cliente | ✔ | — | — | — |
| Cadastrar bot, Publicar versão | ✔ | — | — | — |
| Nova fila, mudar modo de dados, máximo de tentativas | ✔ | — | — | — |
| Campos visíveis e retenção da fila | ✔ | ✔ | — | — |
| Executar agora, Reexecutar, Cancelar execução | ✔ | ✔ | ✔ | — |
| Reprocessar (item, falhas da fila, falhas do lote) | ✔ | ✔ | ✔ | — |
| Enviar planilha | ✔ | ✔ | ✔ | — |
| Cancelar lote | ✔ | ✔ | — | — |
| Agendamentos (criar, editar, pausar, excluir) | ✔ | ✔ | — | — |
| Alertas (criar, editar) | ✔ | ✔ | — | — |
| Máquinas: Cadastrar máquina, Novo pool, Gerar nova chave, Revogar máquina | ✔ | ✔ | — | — |
| Usuários: Convidar usuário, Mudar papel, Encerrar sessões, Remover acesso | ✔ | ✔ | — | — |
| Exportar CSV / XLSX | ✔ | ✔ | ✔ | ✔ |
| Consultar tudo do próprio cliente | ✔ | ✔ | ✔ | ✔ |

Como a permissão aparece:
- **Botão:** sem a permissão, ele não aparece. Não deixe desabilitado.
- **Tela:** sem acesso (por exemplo, Usuários para Operador), a navegação não mostra o item. A rota mostra o EmptyState `forbidden`.

Rótulos de papel: `tenant_admin` → "Admin do cliente", `operator` → "Operador", `viewer` → "Leitor". Para a equipe Artemisys, o rodapé da sidebar mostra "Equipe Artemisys".

**Contexto de cliente.** O admin da plataforma escolhe o cliente no seletor da sidebar, e a escolha é guardada em cookie `rg_client` (vazio = "Todos os clientes"). Rotas são as mesmas para todos.
- **"Todos os clientes":** listas ganham a coluna Cliente e o Dashboard usa a versão consolidada.
- **Usuário de cliente:** fica sempre preso ao próprio cliente.

---

## 5. Inventário de componentes

Base = componente do shadcn/ui que serve de ponto de partida.

### Button — base `button`
- Variantes:
  - `primary`: accent com texto branco;
  - `secondary`: panel-active com borda de controle;
  - `ghost`: transparente, texto secondary;
  - `destructive`: danger com texto escuro, **só** dentro de AlertDialog;
  - `destructive-outline`: panel-active, borda danger 40%, texto danger. Usar para abrir uma confirmação destrutiva, ex.: "Revogar máquina".
- Tamanhos: altura 44px; `icon` 44 × 44 com `aria-label` obrigatório e Tooltip com o mesmo texto.
- Estados: padrão, hover, foco (`--ring`), desabilitado (opacidade 45%, `cursor-not-allowed`), carregando (spinner 14px + verbo no gerúndio, ex.: "Reprocessando…", `aria-busy`).
- Props: `variant`, `size`, `loading`, `loadingText`, `icon` (lucide), `asChild`.
- Erro após clique: o botão volta ao normal. A validação aparece no campo, e falha de servidor aparece num Toast de erro.

### Input / Textarea — base `input`, `textarea`, `label`, `form`
- Altura 44px, fundo `--color-sidebar`, borda `--color-border-control`.
- Hover: borda `--color-border-hover`.
- Foco: borda accent-text + halo `0 0 0 3px` accent-text a 22%.
- Erro: borda danger + mensagem 12px com ícone `circle-alert`, `aria-invalid` e `aria-describedby`.
- Desabilitado: opacidade 45%.
- Carregando: spinner à direita (ex.: validar nome livre).
- Rótulo sempre visível acima (13px, 500). Ajuda abaixo (12px label). A mensagem de erro substitui a ajuda.
- Textarea: contador opcional `0 / 280`.

### Select — base `select`
- Mesmo visual do Input, ícone `chevron-down`.
- Lista com `--shadow-popover` e item selecionado com `check` em accent-text.
- Item desabilitado mostra o motivo à direita (ex.: "sem máquinas").

### Checkbox — base `checkbox`
- 20px dentro de alvo de 44px. Marcado: fundo accent e check branco.
- Estado parcial (`indeterminate`) com traço, usado no cabeçalho da tabela.
- Erro: borda danger + mensagem.

### Switch — base `switch`
- 40 × 24 dentro de alvo de 44px. Ligado: fundo accent e bolinha branca.
- Carregando: spinner dentro da bolinha. Erro: mensagem abaixo, e o switch volta ao valor anterior.

### SegmentedControl — base `toggle-group` (type="single")
- Contêiner com fundo `--color-sidebar` e borda de controle. Segmentos de 36px de altura, alvo total de 44px.
- Ativo: fundo `--color-border-control` + contorno interno `--color-border-hover`.
- Pode exibir uma contagem mono depois do rótulo.

### StatusPill — base `badge` (variante própria)
- Props: `kind: "job" | "item" | "machine" | "batch"`, `status`, `failureType?`.
- Lê rótulo, tom e ponto de `lib/status.ts` e tem 24px de altura.

### Badge — base `badge`
- Variantes: `neutral`, `accent`, `mono`, `success-text`, `warning-text`. Raio 6px e 22px de altura.
- Uso: papel, versão, hash, modo de dados, "MFA ativo", "Você", "Esta sessão", "Em uso".

### Card — base `card`
- Raio 14px, padding 20px. Variante `link` (o card inteiro é clicável), variante `attention` (banner).

### Banner — composição (`alert` do shadcn como base)
- Tons: `warning`, `danger`, `info`. Ícone 20px, título (14px, 500), texto (13px) e ação opcional à direita.

### KPI — base `card`
- Props: `label`, `icon?`, `value`, `context`, `progress?` (0–1), `tone?` (`danger` muda a borda e o rótulo), `href?`, `loading`, `error`.
- Valor 36px tabular. Erro: valor "—" + "Não foi possível carregar. Tentar de novo".

### DataTable — base `table` + `@tanstack/react-table`
- `manualSorting`, `manualPagination`, `manualFiltering`. Estado sincronizado com a URL.
- Linha de 56px, cabeçalho de 44px (overline 11px, uppercase).
- Ordenação: clique alterna crescente → decrescente → sem ordem, com `aria-sort`. A seta aparece no cabeçalho.
- Seleção: só da página atual. O checkbox do cabeçalho tem três estados.
- Ações em lote: barra acima da tabela, com fundo accent-text a 8%, o texto "N itens selecionados" e as ações.
- Linha clicável: a referência é um link e a linha inteira leva ao detalhe.
- Rodapé: "Linhas por página" (10, 25, 50), texto `1–25 de 1.257` e botões "Anterior" / "Próxima" com números de página.
- Estados:
  - carregando: skeleton com o mesmo número de linhas;
  - troca de página: linhas atuais a 50% de opacidade até chegar a resposta;
  - vazio: EmptyState;
  - vazio com filtro: "Nenhum item com esses filtros" + "Limpar filtros";
  - erro sem dados: EmptyState de erro;
  - erro com dados na tela: Toast.
- Abaixo de 900px: vira lista de cards (título, status, metadado) e a seleção some.
- Props: `columns`, `data`, `rowCount`, `state`, `onStateChange`, `isLoading`, `error`, `emptyState`, `getRowHref`, `bulkActions?`.

### Dialog e AlertDialog — base `dialog` e `alert-dialog`
- Largura 480–720px, raio 14px, véu `--color-scrim`. Prendem o foco, fecham com Esc e devolvem o foco ao gatilho.
- Botão de sair: **sempre "Voltar"** (ghost). O botão de confirmar repete o verbo do título.
- Destrutivo:
  - não fecha clicando fora;
  - foco inicial em "Voltar";
  - ícone `triangle-alert` em quadro danger;
  - quando irreversível, campo "Digite <nome> para confirmar" que libera o botão.

### KeyReveal — base `dialog` + `input` + `checkbox`
- Sem X, sem Esc e sem fechar por clique fora.
- Conteúdo:
  - pill "Aguardando cadastro" com o nome da máquina;
  - título "Copie a chave de registro";
  - aviso âmbar;
  - "Endereço do Regista" em mono, com "Copiar endereço" (vira "Copiado");
  - a chave em mono com `user-select: all`;
  - botão "Copiar chave", que vira "Copiada" por 3s com `aria-live`;
  - checkbox "Guardei a chave em um lugar seguro";
  - "Concluir", liberado só após o checkbox.
- A chave nunca volta da API depois deste passo.

### Toast — base `sonner`
- Canto inferior direito, no máximo 3.
- Sucesso: some em 5s. Erro: fica até ser fechado e usa `role="alert"`.
- Pode ter uma ação em link (ex.: "Ver execução").

### Tabs — base `tabs`
- Sublinhado de 2px accent-text na aba ativa e contagem opcional. A aba ativa fica na URL (`?tab=`).

### Tooltip — base `tooltip`
- Fundo `--color-text` com texto `--color-bg`. Abre em hover e foco após 400ms.
- Nunca guarda informação que não exista em outro lugar.

### Skeleton — base `skeleton`
- Shimmer entre panel-active e `#1E242D`. Aparece só após 300ms de espera.

### EmptyState — composição
- Props: `icon`, `title`, `description`, `action?`, `tone: "empty" | "filtered" | "error" | "forbidden"`.
- Ícone 20px em quadro de 44px; título 15px/600; descrição 13px label com até 340px.

### Sidebar — base `sidebar`
- 248px fixa, fundo `--color-sidebar`, rolagem própria se faltar altura.
- Topo: marca (quadro accent 32px com "R" branco + "Regista" + "by Artemisys").
- Abaixo da marca:
  - admin da plataforma: seletor de cliente;
  - usuário de cliente: caixa fixa com o nome do cliente.
- Grupos e itens:
  - **Artemisys:** Clientes (só admin da plataforma);
  - **Operação:** Dashboard, Execuções, Filas, Lotes;
  - **Automação:** Bots, Agendamentos, Alertas;
  - **Infraestrutura:** Máquinas, Usuários (Usuários só admin da plataforma e Admin do cliente).
- Item de 44px. Ativo: fundo panel-active + contorno interno + ícone accent-text + `aria-current="page"`.
- Contadores só quando pedem ação: Execuções = pendentes (warning), Máquinas = sem sinal (danger), ambos com `aria-label`.
- Rodapé: avatar com iniciais, usuário (e-mail para cliente, nome para Artemisys), papel e botão de menu (Minhas sessões, Configurar MFA, Sair).
- Abaixo de 900px vira `sheet` lateral esquerda, com topbar mobile de 60px: menu, cliente + página, ponto de sincronização com rótulo acessível e notificações.

### Seletor de cliente — base `popover` + `command`
- Busca "Buscar cliente", lista com "Todos os clientes" primeiro e atalho Ctrl+J.
- Cada cliente mostra um metadado à direita ("1 sem sinal" em danger).
- Trocar recarrega a página atual no novo contexto.

### Topbar — composição
- Caminho em mono `Cliente / Pai / Página` (o pai é link), seguido de:
  - indicador de sincronização;
  - busca "Buscar referência, bot ou execução" com Ctrl K (`command`);
  - notificações (`popover`).
- Indicador de sincronização (atualiza a cada 15s; clicar força atualização):

| Estado | Texto |
|---|---|
| ok | `Sincronizado · há 12s` |
| atualizando | `Sincronizando…` |
| atrasado (2 min) | `Atualização atrasada · há 3 min` |
| erro | `Sem conexão · tentando de novo` |

- Notificações: título "Notificações", ação "Marcar todas como lidas" e itens com texto, cliente e tempo, cada um levando ao alvo.

### DetailHeader — composição
- Link de volta com o nome da lista, contexto mono (ex.: `Execução · exec-7f3a21`), título display, StatusPill + metadado mono, ações à direita.
- Logo abaixo: faixa `meta` de pares rótulo/valor e depois Tabs. Abaixo de 900px as ações descem em largura total.

### LogViewer — base `scroll-area` + `toggle-group` + `select`
- Fundo `--color-log`, mono 12.5/20. Colunas: hora `HH:mm:ss.SSS`, nível, referência do item (link), mensagem.
- Nível: INFO accent-text, WARN warning, ERROR danger. Linhas ERROR com fundo danger a 7%.
- Filtros: nível (Todos/INFO/WARN/ERROR com contagem) e item ("Todos os itens" + referências). No detalhe do item, o filtro vem travado.
- "Acompanhar ao vivo" (switch) só em execução ativa. Ação "Copiar logs".
- Estados:

| Estado | Texto |
|---|---|
| carregando | `Carregando logs_` |
| sem linhas | `Aguardando a primeira linha do robô_` |
| filtro vazio | `Nenhuma linha com esse filtro.` + "Limpar filtros" |
| erro | `Não foi possível carregar os logs.` + "Tentar de novo" |
| fim | `Fim dos logs desta execução.` |

- `role="log"`.

### Timeline de execução — composição (`<ol>`)
- 4 etapas fixas: Criada, Na fila, Executando, Finalizada.
- Marcas por estado:

| Estado | Marca |
|---|---|
| feita | check verde |
| atual | ponto pulsante violeta |
| atenção (parada na fila) | âmbar, com o texto do motivo ("há 2h · nenhuma máquina livre") |
| falha | X vermelho, rótulo "Finalizada · Falhou" |
| cancelada | traço neutro |
| futura | vazado `--color-border-strong` |

- Cada etapa mostra hora mono. A linha entre etapas é verde até a etapa atual.

### CronBuilder — composição (`toggle-group` + `input`)
- Atalhos: "De hora em hora", "Diário", "Dias úteis", "Personalizado".
- Campos por atalho:
  - De hora em hora: "No minuto" (00–59);
  - Diário e Dias úteis: "Horário" (HH:mm);
  - Personalizado: "Expressão cron" editável.
- Expressão cron sempre visível, em mono e só leitura fora de Personalizado. Ajuda: "Gerada pelo atalho. Escolha Personalizado para editar." (no Personalizado: "minuto · hora · dia do mês · mês · dia da semana").
- Resumo à direita: descrição em português + "Próximas 3 execuções" + `Fuso: America/Sao_Paulo`. Use `cronstrue` (locale pt_BR) para a descrição e `cron-parser` para as próximas datas.
- Erro de expressão: mensagem que diz o campo e um exemplo, ex.: `A hora vai de 0 a 23. Ex.: 0 8 * * 1-5`.
- Props: `value`, `onChange`, `timezone`.

---

## 6. Shell e layout

- `app/(app)/layout.tsx`: `flex min-h-screen` → Sidebar (248px, sticky, `h-screen`, rolagem própria) + `main` (padding `24px 40px 56px`). O conteúdo fica em contêiner `max-w-[1240px] mx-auto flex flex-col gap-6`.
- A topbar é a primeira linha do conteúdo e não fica fixa.
- Cabeçalho de página: `h1` display + parágrafo label com até 680px à esquerda; ações à direita, alinhadas pela base.
- Ordem das ações: secundárias antes, primária por último (mais à direita). Uma primária por área.
- Abaixo de 1100px, grades de duas colunas viram uma. Abaixo de 900px: sidebar em sheet, padding 16px e `h1` 24px.
- `app/(auth)/layout.tsx`: duas colunas (marca 1.1fr / formulário 1fr). Abaixo de 960px, só o formulário com a marca pequena no topo.

---

## 7. Telas

Convenções desta seção:
- Rotas em `app/(app)` salvo indicação.
- "Estados" lista os que a tela precisa implementar.
- Textos entre aspas são exatos.

### 7.1 Login e MFA — `app/(auth)`

**`/login`**
- Marca (coluna esquerda):
  - título "Suas automações, acompanhadas em tempo real." ("acompanhadas em tempo real." em accent-text);
  - três pontos com ícone:
    - "Disparo sem acessar a máquina" — "Execute qualquer robô direto do painel, sem abrir acesso remoto.";
    - "Cada item com status e motivo" — "Veja o que deu certo, o que falhou e por quê, linha por linha.";
    - "Aviso quando algo para" — "Receba e-mail quando um robô falha ou uma máquina fica sem sinal.";
  - rodapé "Regista é um serviço da Artemisys · artemisys.com.br".
- Formulário:
  - título "Entrar", subtítulo "Use o e-mail do convite que você recebeu.";
  - campos "E-mail" e "Senha";
  - botão "Entrar" (largura total, 48px);
  - nota "Esqueceu a senha? Fale com o administrador do seu escritório."
- Erro: banner danger "E-mail ou senha incorretos" / "Confira os dois e tente de novo. Se esqueceu a senha, peça ao administrador do seu escritório." O campo senha fica com borda de erro.

**`/login/mfa/setup`** (primeiro acesso)
- Cabeçalho: "Passo 1 de 2", título "Proteja sua conta", subtítulo "Todo acesso ao Regista pede um código do celular, além da senha."
- Passos numerados:
  1. "Instale um app autenticador, como Google Authenticator ou Microsoft Authenticator."
  2. "No app, escolha adicionar conta e aponte a câmera para o código abaixo."
- QR TOTP em fundo branco. Ao lado: "Não consegue escanear? Digite esta chave no app:" + chave em mono agrupada em 4 + botão "Copiar chave".
- Campo "Código de 6 dígitos que aparece no app" (`inputmode="numeric"`, `autocomplete="one-time-code"`) e botão "Ativar verificação".

**`/login/mfa`**
- Título "Digite o código", subtítulo "Abra o app autenticador e digite o código de 6 dígitos do Regista."
- Campo "Código" e botão "Confirmar". Links "Voltar" e "Usar código de recuperação".
- Erro: "Código incorreto ou expirado. Os códigos mudam a cada 30 segundos; use o que está na tela agora."

**`/login/recovery-codes`**
- Cabeçalho: "Passo 2 de 2", título "Guarde seus códigos de recuperação", subtítulo "Se perder o celular, entre com um destes códigos. Cada um funciona uma vez."
- Banner warning "Eles não serão mostrados de novo" / "Copie agora e guarde num gerenciador de senhas."
- 10 códigos `XXXX-XXXX` em mono, duas colunas.
- Botão "Copiar códigos", checkbox "Guardei os códigos em um lugar seguro" e botão "Concluir e entrar", liberado após o checkbox.

### 7.2 Dashboard — `/dashboard`

**Versão admin da plataforma em "Todos os clientes"**
- Cabeçalho: título "Visão geral", texto "Todos os clientes. Escolha um cliente no topo da barra lateral para ver só os dados dele." e período (SegmentedControl "Hoje" / "7 dias" / "30 dias").
- Banner warning: "N pontos precisam de atenção" + resumo ("1 máquina sem sinal, 1 execução esperando máquina e 1 item abandonado.").
- KPIs:
  - "Clientes ativos";
  - "Execuções · 7 dias" (contexto "1 executando · 1 pendente");
  - "Taxa de sucesso · 7 dias" (com barra);
  - "Máquinas sem sinal" (tom danger quando > 0).
- Gráfico "Execuções por dia · 14 dias": barras empilhadas Concluído (success) + Falhou (danger-chart), Recharts `BarChart` `stackId`.
- Card "Precisa de atenção": até 5 linhas (StatusPill + alvo mono + cliente e tempo), cada uma é link:
  - máquinas `offline`;
  - jobs `pending` há mais de 10 min;
  - itens `abandoned` ou `in_progress` há mais de 30 min.
- Tabela "Por cliente": Cliente, Execuções · 7d, Sucesso, Máquinas ("1 de 3 online" + pill "1 sem sinal"), Última execução. Ação "Ver clientes →".
- Tabela "Últimas execuções" (5): Execução (bot + id), Cliente, Status, Início, Duração. Ação "Ver todas →".

**Versão de cliente**
- Cabeçalho: título "Dashboard", texto "Como estão as automações do Escritório Exemplo agora." e ação primária "Executar agora", que leva a Bots.
- Banner danger quando há máquina sem sinal: "estacao-atendimento-02 está sem sinal desde 08:20" / o texto da §13 (o mesmo do 7.14, com a máquina que assume o pool e a frase do modo Sessão) + "Ver máquina".
- KPIs: "Execuções · 7 dias", "Itens processados · 7 dias", "Taxa de sucesso dos itens", "Máquinas online" ("1 de 2 · 1 sem sinal").
- Gráfico "Itens processados por dia · 14 dias" com Sucesso / "Falhou ou abandonado".
- Card "Máquinas": nome, situação ("Executando exec-7f3a24" / "Último sinal hoje 08:20") e pill.
- Card "Parados": jobs pendentes e itens abandonados.
- "Últimas execuções" com coluna Itens (`37 · 3`) e, no cabeçalho do card, "Próxima: ter 06/10 08:00".

**Estados das duas versões**

| Estado | Título | Texto | Ação |
|---|---|---|---|
| Vazio (admin) | "Nenhum cliente cadastrado ainda" | "Cadastre o primeiro cliente e convide o administrador dele. Os números aparecem aqui assim que houver execuções." | "Cadastrar cliente" |
| Vazio (cliente) | "Ainda não há execuções" | "Quando a Artemisys publicar seu primeiro robô, ele aparece em Bots. Antes disso, cadastre a máquina onde ele vai rodar." | "Cadastrar máquina" |
| Carregando | — | skeleton dos 4 KPIs e do gráfico | — |
| Erro | "Não foi possível carregar o dashboard" (admin: "…a visão geral") | "O servidor não respondeu. Seus robôs continuam rodando; só o painel está sem dados agora." | "Tentar de novo" |

No erro, a topbar mostra "Sem conexão · tentando de novo".

### 7.3 Execuções — `/runs`

- Cabeçalho: título "Execuções", texto "Cada vez que um robô roda. Clique numa execução para ver a linha do tempo, os logs e os itens." e ação "Executar agora".
- Filtros:
  - busca "Buscar pelo código, ex.: exec-7f3a21";
  - Select "Bot: Todos";
  - Select "Status: Todos";
  - período "Hoje" / "7 dias" / "30 dias" (padrão 30 dias);
  - contagem à direita ("8 execuções").
- Colunas: Execução (bot + id mono), Status, Gatilho ("Manual" + e-mail / "Agendamento" + descrição da regra), Máquina, Início (ordem padrão decrescente), Duração, Itens (sucesso verde · falha vermelha).
- Coluna Cliente quando o admin está em "Todos os clientes".
- Estados:
  - Vazio: "Nenhuma execução nos últimos 30 dias" / "Dispare um robô agora ou crie um agendamento dentro do bot." / "Ver bots".
  - Erro: "Não foi possível carregar as execuções" / "O servidor demorou para responder. Seus filtros foram mantidos." / "Tentar de novo".
- Mobile: lista de cards com chips de status (Todas, Executando, Pendente, Falhou, Concluído, Cancelado) roláveis na horizontal.

### 7.4 Detalhe da execução — `/runs/[runId]?tab=logs|items|screenshot`

- DetailHeader:
  - voltar "Execuções", contexto `Execução · exec-7f3a21`, título = nome do bot;
  - StatusPill + `01/10/2026 08:00:12 · 4min 12s · estacao-atendimento-01`;
  - ações "Cancelar execução" (destructive-outline) e "Reexecutar" (primary).
- Faixa meta: Gatilho, Pool, Fila (link), Lote (link), Versão do bot, Itens.
- Card com a Timeline.
- Banner danger, quando há itens não concluídos:
  - título "N itens não foram concluídos";
  - texto com uma frase por item (referência: motivo);
  - ação "Ver itens com falha" → fila filtrada.
- Abas:
  - **Logs:** LogViewer em altura cheia (mínimo 420px).
  - **Itens:** contagem; colunas Referência, Status, Tentativas, Motivo; rodapé "Mostrando 6 de 40 · ver todos na fila".
  - **Captura de erro:** só se houver. Título "Tela no momento do erro", referência e hora; legenda "Captura feita pelo robô quando o erro aconteceu. Ela fica guardada pelo prazo de retenção da fila."
- Regras dos botões:
  - "Cancelar execução" só existe em `pending | assigned | running`. Fora disso fica oculta. Confirmação: AlertDialog "Cancelar exec-7f3a24?" / "O robô para no próximo item. Itens já concluídos continuam concluídos." / "Voltar" + "Cancelar execução".
  - "Reexecutar" fica oculto enquanto a execução está ativa.
- Executando: a Timeline fica na etapa atual ("há 18 min · 12 de 40 itens"), o LogViewer em "Acompanhando ao vivo…" e não há aba Captura.
- Estados:
  - Carregando: skeleton.
  - Não encontrada: "Execução não encontrada" / "O link pode estar errado ou a execução já passou do prazo de retenção. Procure pela lista de execuções." / "Voltar para Execuções".

### 7.5 Bots — `/bots`

- Cabeçalho: título "Bots", texto "Robôs publicados pela Artemisys. Para ver versões, agendamentos e histórico, abra o bot." e ação "Cadastrar bot" (só Artemisys, secondary).
- Busca "Buscar bot".
- Colunas:
  - Bot (ícone + nome + `fila acordos · v1.4.2`);
  - Cliente (só em "Todos os clientes");
  - Pool;
  - Última execução (pill + data);
  - Últimas 10 (faixa de 10 barrinhas 10 × 22px coloridas pelo status, com `aria-label` resumindo);
  - Próxima;
  - ações "Agendar" (secondary, abre o bot na aba Agendamentos) e "Executar agora" (primary).
- Se já houver execução ativa, "Executar agora" continua habilitado com Tooltip "Entra na fila depois da execução atual".
- Estados:
  - Vazio: "Nenhum bot cadastrado" / "Cadastre o bot e publique a primeira versão. Antes, confira se o cliente já tem uma máquina online no pool." / "Cadastrar bot".
  - Erro: "Não foi possível carregar os bots" / "O servidor não respondeu. Tente de novo em alguns segundos." / "Tentar de novo".

### 7.6 Detalhe do bot — `/bots/[botId]?tab=overview|runs|schedules|versions`

- DetailHeader:
  - contexto `Bot · Escritório Exemplo`;
  - título "Controle de Acordos";
  - pill da execução atual ou última + badge accent `v1.4.2 em uso` + "Próxima: ter 06/10 08:00";
  - ações "Agendar" e "Executar agora".
- Faixa meta: Fila, Pool, Máquinas no pool ("1 de 2 online"), "Sucesso · 30 dias" ("75% das execuções · 97% dos itens").
- Abas:
  - **Visão geral:** "Últimas 10 execuções" (barras clicáveis de 36 × 44, legenda "Mais antiga à esquerda. Verde concluída, vermelho falhou, cinza cancelada, roxo executando.") + "Próximas execuções" (3 datas).
  - **Execuções:** tabela curta (id, status, gatilho, início, duração) e link para `/runs?bot=`.
  - **Agendamentos:**
    - nota "Horários de Brasília. Agendamentos pausados não disparam nada." e ação "Novo agendamento";
    - cada regra é um card com descrição, `cron · criado por …`, "Próxima" (ou "Pausado"), Switch "Ativo"/"Pausado" e ações "Editar" e "Excluir" (destructive-outline);
    - confirmação: "Excluir este agendamento?" / "Toda terça e quinta às 08:00 deixa de disparar. Execuções já feitas continuam no histórico." / "Voltar" + "Excluir agendamento".
  - **Versões:**
    - nota "Cada versão é assinada. O agente só roda o código cujo hash bate com o publicado.";
    - ação "Publicar versão" (só Artemisys);
    - colunas Versão (+ badge "Em uso"), Publicada em, Por, Hash (curto `3f9a…c21e`), O que mudou.
- Dialog "Novo agendamento" / "Editar agendamento":
  - texto "Controle de Acordos vai rodar sozinho nos horários abaixo.";
  - CronBuilder;
  - botões "Voltar" e "Criar agendamento" / "Salvar agendamento".

### 7.7 Agendamentos — `/schedules` (só consulta)

- Cabeçalho: título "Agendamentos", texto "Tudo o que vai rodar sozinho, em um lugar. Para criar ou mudar um agendamento, abra o bot."
- Card "Próximas 48 horas": linhas com horário mono, quadradinho na cor do pool, bot, pool e cliente. Legenda "Mesma cor = mesmo pool. Horários sobrepostos no mesmo pool esperam um pelo outro." Agrupe disparos repetidos ("· mais 6 até 18:00").
- Tabela "Todas as regras" ("4 regras · 3 ativas"): Bot, Cliente, Regra (descrição + cron), Próxima, Situação (badge "Ativo"/"Pausado") e ação "Abrir bot".
- Estados:
  - Vazio: "Nenhum agendamento" / "Abra um bot e use \"Agendar\" para que ele rode sozinho nos horários escolhidos." / "Ver bots".
  - Erro: "Não foi possível carregar os agendamentos" / "Os agendamentos continuam valendo; só a lista não carregou. Tente de novo." / "Tentar de novo".

### 7.8 Filas — `/queues`

- Cabeçalho: título "Filas", texto "Cada linha de planilha ou processo vira um item, com status próprio. A fila é o caminho entre a planilha e o robô." e ação "Nova fila" (só Artemisys).
- Colunas: Fila (nome mono + bot), Cliente (admin), Modo (badge "Referência"/"Central"), Novo, Em andamento, Sucesso, Falhou, Abandonado (cada contagem com ponto na cor do status) e Retenção.
- Dialog "Nova fila":
  - texto "Use o mesmo nome que o código do robô espera. O nome não muda depois.";
  - "Nome da fila" (mono, ajuda "Letras minúsculas, números e hífen.");
  - "Cliente";
  - "Bot que consome a fila";
  - "Onde ficam os dados dos itens" (SegmentedControl Referência/Central) com ajuda dinâmica:
    - Referência: "Referência: só o código do item (ex.: ACORDO-0042) sai da máquina do cliente. O conteúdo fica no ambiente dele e aparece mascarado no painel.";
    - Central: "Central: o conteúdo do item fica guardado no Regista pelo prazo de retenção e aparece no detalhe do item.";
  - "Retenção (dias)" (ajuda "Depois disso, itens, logs e evidências são apagados.");
  - "Máximo de tentativas" (ajuda "Vale para falhas de aplicação. Falha de negócio não tenta de novo.");
  - botões "Voltar" e "Criar fila".
- Estados:
  - Vazio: "Nenhuma fila criada" / "Crie a fila com o mesmo nome que o código do robô usa. Depois, envie planilhas em Lotes." / "Nova fila".
  - Erro: "Não foi possível carregar as filas" / "O servidor não respondeu. Tente de novo em alguns segundos." / "Tentar de novo".

### 7.9 Detalhe da fila — `/queues/[queueId]?tab=items|settings&status=`

- DetailHeader:
  - contexto `Fila · Escritório Exemplo`, título = nome mono;
  - linha "Referência" (badge) + "Consumida por Controle de Acordos · retenção de 90 dias · até 3 tentativas";
  - ações "Enviar planilha" (vai para Lotes com a fila pré-selecionada) e "Reprocessar falhas (N)".
- Aba Itens:
  - SegmentedControl de status com contagem (Todos, Novo, Em andamento, Sucesso, Falhou, Abandonado), busca "Buscar referência";
  - colunas Referência, Status, Tentativas (`2 de 3`), Lote, Último motivo (uma linha, reticências) e Atualizado (decrescente);
  - paginação no servidor;
  - filtro sem resultado: "Nenhum item com esse status" / "Escolha outro status ou veja todos." / "Ver todos".
- Aba Configurações:
  - "Onde ficam os dados dos itens": desabilitado para cliente, com ajuda "Só a equipe Artemisys muda o modo, porque o robô precisa ser ajustado junto.";
  - "Campos visíveis no detalhe do item": checkboxes com os nomes de campo em mono e ajuda "No modo Referência, os campos aparecem mascarados. Escolha quais nomes de campo sua equipe vê.";
  - "Retenção (dias)" (ajuda "Itens, logs e evidências mais antigos são apagados.");
  - "Máximo de tentativas": desabilitado para cliente, com ajuda "Definido pela Artemisys com o robô.";
  - rodapé "Descartar alterações" + "Salvar configurações". Sucesso: Toast "Configurações salvas".
- Confirmação "Reprocessar falhas":
  - título "Reprocessar 24 itens?";
  - texto "Os itens com Falhou ou Abandonado voltam para a fila como Novo. As tentativas anteriores continuam no histórico.";
  - botões "Voltar" + "Reprocessar".

### 7.10 Detalhe do item — `/queues/[queueId]/items/[itemId]`

- DetailHeader:
  - voltar "Fila acordos", contexto `Item · fila acordos`, título `ACORDO-0042` mono;
  - pill "Falhou | Negócio" + "2 de 3 tentativas · atualizado 01/10/2026 08:02";
  - ação "Reprocessar".
- Banner com o que fazer, montado pelo backend a partir do último motivo. Exemplo warning: "Falta o valor do acordo na planilha" / "Corrija a linha 18 do LOTE-0012 no sistema de origem e depois clique em Reprocessar. Reprocessar sem corrigir vai falhar de novo."
- Faixa meta: Lote (`LOTE-0012 · linha 18`, link), Criado, Última execução (link) e Máquina.
- Duas colunas (1.5fr / 1fr):
  - **Tentativas:** lista numerada, da mais recente para a mais antiga. Cada uma mostra pill com o tipo, intervalo e duração mono, o motivo (14px) e uma nota:
    - negócio: "Falha de negócio não gera nova tentativa automática.";
    - aplicação: "Falha de aplicação: o robô tentou de novo na mesma execução.".
  - **Logs deste item:** LogViewer com filtro travado e coluna "tentativa N" no lugar da referência.
  - **Dados do item:** badge do modo.
    - No modo Referência:
      - nota info "Os dados deste item ficam no ambiente do Escritório Exemplo. O Regista guarda só a referência e o status.";
      - lista nome-do-campo → valor mascarado (`••••••`, com `aria-label="Oculto"`);
      - só o campo `referencia` aparece em claro.
    - No modo Central, os valores aparecem normalmente.
  - **Evidências:** miniaturas que abrem a imagem em Dialog e legenda "Capturas feitas pelo robô. Ficam guardadas por 90 dias."
- Estados:
  - Carregando: skeleton.
  - Erro: "Não foi possível carregar o item" / "O servidor não respondeu. Tente de novo; se continuar, avise a Artemisys." / "Tentar de novo".
- Mobile: header com voltar, pill, "Reprocessar" em largura total, banner, meta em lista, tentativas em cards, logs em duas linhas por entrada, dados e evidências.

### 7.11 Lotes — `/batches`

- Cabeçalho: título "Lotes", texto "Cada planilha enviada vira um lote. O relatório confere se toda linha lida virou item ou foi rejeitada com motivo." e ação "Enviar planilha".
- Colunas:
  - Lote (id + nome do arquivo);
  - Fila;
  - Status;
  - Linhas (`43 lidas = 40 + 3 rejeitadas`, rejeitadas em warning quando > 0);
  - Resultado (barra empilhada + `37 sucesso · 2 falha · 1 não processada` ou `28 a processar`);
  - Enviado (data + e-mail).
- Dialog "Enviar planilha":
  - texto "Cada linha vira um item. Linhas com problema são rejeitadas e aparecem no relatório com o motivo.";
  - Select "Fila";
  - área de arquivo "Arraste a planilha aqui ou escolha um arquivo" com ajuda ".xlsx ou .csv, até 10 MB. A primeira linha precisa ter os nomes das colunas.";
  - depois de escolher, linha com nome, tamanho e número de linhas;
  - botões "Voltar" e "Enviar planilha".
- Estados:
  - Vazio: "Nenhuma planilha enviada ainda" / "Envie uma planilha .xlsx ou .csv. Cada linha vira um item na fila escolhida." / "Enviar planilha".
  - Erro: "Não foi possível carregar os lotes" / "O servidor não respondeu. Tente de novo em alguns segundos." / "Tentar de novo".

### 7.12 Relatório do lote — `/batches/[batchId]`

- DetailHeader:
  - contexto `Lote · fila acordos`, título `LOTE-0012`;
  - pill + "acordos-semana-40.xlsx · enviado 01/10/2026 07:52 por operacao@…";
  - ações "Exportar CSV", "Exportar XLSX" e "Reprocessar falhas (N)".
  - Em lote `open` ou `closed` há também "Cancelar lote", com confirmação destrutiva digitando o id:
    - título "Cancelar LOTE-0013?";
    - texto "Os itens ainda Novos deste lote são cancelados e não serão processados. Itens já concluídos continuam concluídos.";
    - botão "Cancelar lote".
- Card "Resumo reconciliado":
  - selo à direita "Todas as 43 linhas têm destino" (verde). Se a soma não fechar: selo danger "N linhas sem destino. Avise a Artemisys.";
  - equação de três caixas "Lidas 43 = Enfileiradas 40 + Rejeitadas 3", com a caixa rejeitadas em warning;
  - "Das 40 enfileiradas": barra empilhada + legenda "37 Sucesso · 2 Falha · 1 Não processada (abandonada)".
- Tabela "Por referência": filtro "Todas · 40" / "Com problema · 3"; colunas Linha, Referência, Status, Tentativas, Motivo.
- Tabela "Linhas rejeitadas", com ajuda "Não viraram item. Corrija na planilha e envie de novo só estas linhas." Colunas Linha, Referência ("—" se vazia), Motivo. Motivos padronizados:
  - "Referência repetida neste lote (também na linha 6).";
  - "Referência vazia. Preencha a coluna referencia.";
  - "Já existe na fila acordos com status Sucesso.".

### 7.13 Máquinas e pools — `/machines`

- Cabeçalho: título "Máquinas e pools", texto "Computadores onde os robôs rodam, com o agente instalado. Um pool reúne máquinas que podem rodar os mesmos robôs." e ações "Novo pool" (secondary) e "Cadastrar máquina" (primary).
- Banner danger por máquina sem sinal: "estacao-atendimento-02 está sem sinal há 2 h" / o texto da §13 (o mesmo do 7.14) + "Ver máquina".
- Um card por pool:
  - título, linha "Roda: Controle de Acordos" (ou "Nenhum bot usa este pool ainda"), contagem "1 de 2 online";
  - tabela Máquina, Status, Último sinal ("há 8s" / "hoje 08:20 · há 2 h" / "Chave gerada há 10 min, ainda não usada"), Agente, Modo ("Serviço"/"Sessão"), Agora ("Executando exec-7f3a24") e "Abrir →".
- Abaixo dos pools, "Mostrar revogadas (N)".
- Dialog "Cadastrar máquina":
  - texto "Depois de cadastrar, você recebe uma chave para instalar o agente nesse computador.";
  - "Nome da máquina" (mono);
  - "Pool";
  - "Como o agente roda" (SegmentedControl Serviço/Sessão) com ajuda:
    - Serviço: "Serviço: roda em segundo plano, mesmo sem ninguém logado. Use quando o robô não precisa de tela.";
    - Sessão: "Sessão: roda na sessão do Windows de um usuário logado. Use para sites ou sistemas que exigem tela aberta.";
  - botões "Voltar" e "Cadastrar e gerar chave" → KeyReveal.
- Dialog "Novo pool": "Nome do pool" + "Voltar" / "Criar pool".
- Estados:
  - Vazio: "Nenhuma máquina cadastrada" / "Cadastre o computador onde os robôs vão rodar. Você recebe uma chave para instalar o agente nele." / "Cadastrar máquina".
  - Erro: "Não foi possível carregar as máquinas" / "O servidor não respondeu. As máquinas continuam funcionando; só o painel está sem dados." / "Tentar de novo".

### 7.14 Detalhe da máquina — `/machines/[machineId]?tab=history|runs`

- DetailHeader:
  - contexto `Máquina · Escritório – Atendimento`, título mono;
  - pill + "Último sinal hoje às 08:20, há 2 h";
  - ações "Gerar nova chave" (secondary; abre o KeyReveal e invalida a chave anterior) e "Revogar máquina" (destructive-outline).
- Banner danger quando estiver sem sinal: "O agente parou de responder" e o texto da §13 (muda se há outra máquina online no pool e se a máquina está no modo Sessão).
- Faixa meta: Versão do agente, Modo, Sistema, Pool, Cadastrada (data + quem).
- Abas:
  - **Histórico:** linha do tempo de eventos (data mono, ponto colorido, título e detalhe). Eventos: ficou sem sinal, concluiu exec-…, agente atualizado, ficou online pela primeira vez, máquina cadastrada.
  - **Execuções:** tabela.
- AlertDialog "Revogar estacao-atendimento-02?":
  - texto "A máquina deixa de receber execuções na hora e a chave dela para de funcionar. Para usar de novo, será preciso cadastrar a máquina outra vez.";
  - campo "Digite estacao-atendimento-02 para confirmar";
  - botões "Voltar" + "Revogar máquina" (destructive, liberado só com o nome exato). Se houver execução em andamento, acrescente ao texto: "A execução em andamento será cancelada."

### 7.15 Alertas — `/alerts`

- Cabeçalho: título "Alertas", texto "Quem recebe e-mail quando algo dá errado. Cada regra junta um alvo, um evento e os destinatários." e ação "Nova regra".
- Colunas: Alvo (tipo + nome), Quando, Enviar para (um por linha, mono), Último envio, Situação ("Ativa"/"Pausada") e "Editar".
- Eventos por tipo de alvo:
  - Bot: "Execução falhou", "Execução pendente por mais de 30 min".
  - Pool: "Máquina sem sinal por mais de 15 min".
  - Fila: "Item falhou ou foi abandonado", "Item abandonado".
- Dialog "Nova regra de alerta" / "Editar regra":
  - texto "O e-mail sai uma vez por ocorrência, com o link para o que aconteceu.";
  - "Tipo de alvo" (Bot/Pool/Fila);
  - alvo (Select, rótulo muda: "Bot" / "Pool de máquinas" / "Fila");
  - "Quando" (Select + ajuda);
  - "Enviar para" (Textarea, "Um e-mail por linha."; erro "Linha 2 não é um e-mail válido.");
  - botões "Voltar" e "Criar regra" / "Salvar regra". Em editar, também "Excluir regra" com confirmação.
- Estados:
  - Vazio: "Nenhuma regra de alerta" / "Sem regras, ninguém recebe e-mail quando um robô falha ou uma máquina fica sem sinal. Comece por \"Execução falhou\"." / "Nova regra".
  - Erro: "Não foi possível carregar os alertas" / "As regras continuam funcionando; só a lista não carregou. Tente de novo." / "Tentar de novo".

### 7.16 Clientes — `/clients` (só admin da plataforma)

- Cabeçalho: título "Clientes", texto "Escritórios e empresas atendidos pela Artemisys no Regista. Só a equipe Artemisys vê esta tela." e ação "Novo cliente".
- Colunas: Cliente (+ "desde ago/2026"), Usuários, Máquinas ("1 de 3 online"), Bots, Última execução, Atenção (pill ou "—") e "Entrar no cliente" (troca o seletor de cliente e vai para o Dashboard).
- Dialog "Novo cliente":
  - texto "O primeiro administrador recebe um convite por e-mail. Depois, ele convida o resto da equipe.";
  - "Nome do cliente" (ajuda "Aparece no seletor de clientes e no topo das telas dele.");
  - "E-mail do primeiro administrador" (erro exemplo "Falta o domínio. Ex.: admin@escritorio-exemplo.com.br");
  - botões "Voltar" e "Criar e enviar convite".
- Estados:
  - Vazio: "Nenhum cliente ainda" / "Cadastre o primeiro cliente. O administrador dele recebe um convite e configura o resto." / "Novo cliente".
  - Erro: padrão de lista.

### 7.17 Usuários — `/users` (admin da plataforma e Admin do cliente)

- Cabeçalho: título "Usuários", texto "Quem acessa o Escritório Exemplo no Regista e o que cada um pode fazer." e ação "Convidar usuário".
- Colunas:
  - E-mail (+ badge "Você");
  - Papel;
  - Verificação (badge "MFA ativo" verde / "Sem MFA" âmbar / "—");
  - Último acesso;
  - Situação ("Ativo" / "Convite enviado");
  - ações "Mudar papel" (desabilitado na própria linha) e "Encerrar sessões" (desabilitado para convite pendente).
- Dialog "Convidar usuário":
  - texto "A pessoa recebe um e-mail para criar a senha e ativar a verificação em duas etapas.";
  - "E-mail";
  - "Papel" (radio em cards com descrição):
    - Admin do cliente — "Gerencia usuários, máquinas, agendamentos, alertas e retenção das filas.";
    - Operador — "Dispara robôs, reprocessa itens e envia planilhas.";
    - Leitor — "Só consulta. Não dispara nem altera nada.";
  - botões "Voltar" e "Enviar convite".
- Dialog "Mudar papel": mesmo radio + "Salvar papel". No rodapé à esquerda, "Remover acesso" (destructive-outline):
  - título "Remover acesso de financeiro@…?";
  - texto "A pessoa sai na hora e não consegue mais entrar. Para voltar, será preciso um novo convite.";
  - botão "Remover acesso".
- AlertDialog "Encerrar sessões de operacao@escritorio-exemplo.com.br?":
  - texto "A pessoa sai do Regista em todos os aparelhos e precisa entrar de novo. Robôs em execução não param.";
  - botões "Voltar" + "Encerrar sessões".
- Estados: Carregando e Erro (padrão de lista).

### 7.18 Minhas sessões — `/account/sessions`

- Título "Minhas sessões", texto "Aparelhos conectados à sua conta <e-mail>. Se não reconhecer algum, encerre e troque a senha." e ação "Encerrar todas as outras".
- Lista: ícone do aparelho, "Chrome no Windows", "Rio de Janeiro, BR · ativa agora", badge "Esta sessão" e botão "Encerrar" nas outras.
- Card "Verificação em duas etapas" (badge "Ativa"):
  - texto "Ativada em 12/08/2026 com app autenticador. Restam 8 de 10 códigos de recuperação.";
  - ação "Gerar novos códigos de recuperação", com a nota "Gerar novos códigos invalida os antigos." Leva a `/login/recovery-codes` após confirmar a senha.

---

## 8. Mobile (390px)

Telas com versão mobile desenhada: Login, Dashboard (cliente), Execuções e Detalhe do item. As demais seguem as regras responsivas da seção 6.

- **Topbar mobile (60px, fixa no topo):**
  - botão menu (`aria-label="Abrir menu"`);
  - cliente (mono 11px) + página (15px/600);
  - ponto de sincronização com `aria-label`;
  - notificações.
  - Em páginas de detalhe, o menu vira "Voltar" com o nome da lista no `aria-label`.
- **Dashboard:**
  - banner compacto ("1 máquina sem sinal" / "estacao-atendimento-02 desde 08:20. Toque para ver.");
  - KPIs em grade 2 × 2, "Parados", "Últimas execuções" em cards;
  - "Executar agora" em largura total no fim.
- **Ações principais:** botões em largura total com 48px de altura.

---

## 9. Glossário de ações (use sempre estes nomes)

| Ação | Texto do botão | Onde |
|---|---|---|
| Disparar robô | Executar agora | Bots, Detalhe do bot, Dashboard, Execuções |
| Rodar de novo uma execução | Reexecutar | Detalhe da execução |
| Parar execução | Cancelar execução | Detalhe da execução |
| Pôr item(ns) de volta na fila | Reprocessar / Reprocessar falhas (N) | Item, Fila, Lote, ações em lote |
| Subir planilha | Enviar planilha | Lotes, Fila |
| Encerrar lote | Cancelar lote | Relatório do lote |
| Criar agendamento | Agendar (atalho) / Novo agendamento | Bots, Detalhe do bot |
| Nova máquina | Cadastrar máquina | Máquinas |
| Desativar máquina | Revogar máquina | Detalhe da máquina |
| Nova chave | Gerar nova chave | Detalhe da máquina |
| Exportar | Exportar CSV / Exportar XLSX | Relatório do lote |
| Sair de dialog | Voltar | Todos os dialogs |
| Repetir carga | Tentar de novo | Todos os erros |
| Tirar filtros | Limpar filtros | Listas filtradas |

Vocabulário: "Sem sinal" (não "offline"/"heartbeat"), "máquina" (não "worker"), "execução" (não "job"/"run"), "item" (não "work item"), "fila" é sempre a fila de itens. Não citar ferramentas internas na interface.

---

## 10. Dados de exemplo (seed coerente)

Use estes dados para desenvolvimento e testes visuais. "Hoje" é sex 02/10/2026, 10:20 (BRT).

- **Clientes:**
  - Escritório Exemplo (desde ago/2026);
  - Artemisys (demonstração) (desde jul/2026).
- **Usuários do Escritório Exemplo:**
  - admin@escritorio-exemplo.com.br (Admin do cliente, MFA);
  - operacao@… (Operador, MFA);
  - financeiro@… (Leitor, sem MFA);
  - estagio@… (convite enviado).
- **Pools e máquinas:**
  - Escritório – Atendimento: estacao-atendimento-01 (online, Serviço, v0.9.3) e estacao-atendimento-02 (sem sinal desde 08:20, Sessão, v0.9.3);
  - Escritório – Financeiro: estacao-financeiro-01 (Aguardando cadastro);
  - Artemisys – Demonstração: vm-demo-01 (online).
- **Bots:**
  - Controle de Acordos (fila `acordos`, modo Referência, retenção 90, 3 tentativas, v1.4.2 em uso; agendamentos `0 8 * * 2,4` ativo e `0 18 * * 1-5` pausado);
  - Busca na Wikipédia (fila `buscas-demo`, modo Central, retenção 30, v0.3.0; `0 8-18 * * 1-5`).
- **Lotes:**

| Lote | Arquivo | Linhas | Resultado | Status |
|---|---|---|---|---|
| LOTE-0012 | acordos-semana-40.xlsx | 43 lidas = 40 + 3 rejeitadas (linhas 7, 29, 41) | 37 sucesso, 2 falha, 1 abandonado | Concluído |
| LOTE-0013 | — | 40 | 12 concluídos | Fechado, em processamento |
| LOTE-0011 | — | 41 | 41 sucesso | Concluído |
| LOTE-0010 | — | — | — | Cancelado |

- **Execuções:**

| Execução | Status | Quando | Itens / situação |
|---|---|---|---|
| exec-7f3a24 | Executando | desde 10:02 | LOTE-0013 |
| exec-7f3a23 | Pendente | desde 10:08 | esperando máquina |
| exec-7f3a21 | Falhou | 01/10 08:00 | 37/2/1 |
| exec-7f3a20 | Cancelado | — | — |
| exec-7f3a19 | Concluído | 29/09 | 41/0 |
| exec-7f3a17 | Falhou | 24/09 | site fora do ar, 0/40 |
| exec-7f3a16 | Concluído | 22/09 | 38/2 |

- **Itens de destaque:**
  - ACORDO-0042 (Falhou · Negócio, 2 tentativas: aplicação → negócio, linha 18);
  - ACORDO-0046 (Falhou · Aplicação, 3 de 3);
  - ACORDO-0039 (Abandonado).

Nunca use CPF, nome de pessoa ou dado real de processo nos dados de exemplo.

---

## 11. Decisões de produto e backend

As telas assumem os comportamentos abaixo. Eles precisam existir na API e no agente; esconder botões no frontend não basta.

### 11.1 Clientes e acesso

- **Dois níveis de usuário.** Equipe Artemisys (admin da plataforma) vê todos os clientes. Usuários de cliente pertencem a um único cliente, com papel `tenant_admin`, `operator` ou `viewer`.
- **Isolamento.** Toda consulta filtra pelo cliente do usuário. Para o admin da plataforma, o cliente vem do cookie `rg_client`; vazio = "Todos os clientes".
- **Visões consolidadas** para "Todos os clientes":
  - dashboard agregado;
  - listas com campo `client` (execuções, bots, filas, agendamentos);
  - lista de máquinas sem sinal de todos.
- **Entidade Cliente (nova).** Criada só pela Artemisys, com nome + e-mail do primeiro Admin do cliente. O Admin recebe convite por e-mail e convida o resto da equipe.
- **Permissões aplicadas no servidor**, conforme a matriz da seção 4. Exclusivas da Artemisys:
  - cadastrar cliente;
  - cadastrar bot e publicar versão;
  - criar fila;
  - mudar modo de dados e máximo de tentativas.
- **O Admin do cliente pode alterar na fila** só os campos visíveis e a retenção.
- **Convite.** O e-mail leva a criar senha e configurar MFA. O status do usuário fica "Convite enviado" até o primeiro acesso.
- **MFA (TOTP) obrigatório** para todos os usuários, sem exceção.
  - 10 códigos de recuperação, cada um de uso único.
  - Gerar novos invalida os anteriores e exige confirmar a senha.
- **Sessões.**
  - Listar com aparelho, navegador, cidade aproximada e último uso.
  - O próprio usuário encerra uma sessão ou "todas as outras".
  - O Admin do cliente encerra todas as sessões de outro usuário.
  - Encerrar sessões nunca interrompe robôs.
- **Remover acesso** desativa o usuário na hora. Para voltar, é preciso novo convite.

### 11.2 Execuções e itens

- **Execução `failed`** quando qualquer item termina com falha, mesmo que o robô tenha encerrado normalmente.
- **Falha de aplicação** (`failure_type = application`): nova tentativa automática na mesma execução, até `max_attempts` da fila.
- **Falha de negócio** (`failure_type = business`): sem nova tentativa.
- **Item `abandoned`.** Ao terminar uma execução, todo item ainda `in_progress` passa para `abandoned`. Quem faz essa transição é o backend, não o robô.
- **Reprocessar** (item, "falhas da fila", "falhas do lote"):
  - aceita itens `failed` e `abandoned`;
  - volta o item para `new`;
  - mantém todas as tentativas anteriores;
  - o contador de tentativas continua.
- **"Executar agora" com execução ativa do mesmo bot** cria um job `pending`, que espera na fila. Não é bloqueado.
- **Cancelar execução** só em `pending`, `assigned` ou `running`. Com `running`, o robô para no próximo item; itens concluídos ficam como estão.
- **Por tentativa, guardar:** número, início, fim, máquina, execução, status, `failure_type`, motivo (texto curto para pessoas) e capturas de tela.
- **Logs** têm `item_ref` e `attempt` para filtrar por item e tentativa.
- **Dica de ação.** O item expõe um campo `action_hint` (título + texto curto) montado pelo backend a partir do último motivo. Exemplo: "Falta o valor do acordo na planilha" / "Corrija a linha 18 do LOTE-0012 no sistema de origem e depois clique em Reprocessar."
- **Contagem por execução:** sucesso, falha, abandonado e total, para a coluna Itens e o resumo da execução.

### 11.3 Lotes

- **Uma planilha enviada = um lote**, ligado a uma fila. Aceita `.xlsx` e `.csv` até 10 MB, com cabeçalho na primeira linha.
- **Reconciliação obrigatória:** `lidas = enfileiradas + rejeitadas`. Se não fechar, a API devolve a diferença e a tela mostra "N linhas sem destino".
- **Motivos de rejeição padronizados:**
  - referência repetida no mesmo lote (indicando a outra linha);
  - referência vazia;
  - item já existe na fila com status `successful`.
- **Status:**

| Status | Quando |
|---|---|
| `open` | ainda recebendo linhas |
| `closed` | não recebe mais, itens em processamento |
| `completed` | todos os itens com estado final |
| `cancelled` | itens `new` do lote foram cancelados |

- **"Não processadas"** no relatório = itens `abandoned` ou cancelados.
- **Exportar** o relatório por referência em CSV e XLSX, com linha, referência, status, tentativas e motivo, mais as linhas rejeitadas.
- **Cancelar lote** (Admin do cliente) cancela só os itens ainda `new`.

### 11.4 Filas e dados

- **Modo Referência.**
  - Só a referência e o status saem do ambiente do cliente.
  - A API devolve os nomes dos campos permitidos com valor mascarado; só `referencia` vem em claro.
  - Capturas de tela continuam sendo enviadas, porque mostram a tela do site, não a planilha.
- **Modo Central.** O conteúdo do item é guardado no Regista pelo prazo de retenção.
- **Campos visíveis.** Lista de nomes de campo configurável por fila.
- **Retenção.** Apaga itens, tentativas, logs e evidências mais antigos que o prazo, por fila.
- **Nome da fila.** Único por cliente, imutável e no formato `[a-z0-9-]+`. Precisa bater com o que o código do robô usa.

### 11.5 Máquinas, agente e versões

- **Chave de registro.**
  - Gerada ao cadastrar a máquina, devolvida uma única vez e guardada só como hash.
  - "Gerar nova chave" invalida a anterior.
  - Enquanto a chave não é usada, a máquina fica `pending`.
- **Modo do agente:** `service` (sem usuário logado) ou `session` (na sessão do Windows). É escolhido no cadastro e mostrado no detalhe.
- **Sem sinal.** A máquina vira `offline` depois de **2 minutos** sem sinal do agente. Volta a `online` no próximo sinal.
- **Revogar.**
  - A máquina passa a `revoked` e a chave para de funcionar.
  - A execução em andamento nela é cancelada.
  - Revogadas ficam ocultas por padrão ("Mostrar revogadas").
- **Distribuição.** Execuções vão para qualquer máquina `online` livre do pool do bot. **Uma execução por máquina por vez.** Sem máquina livre, o job fica `pending`.
- **Histórico da máquina:** cadastro, primeiro sinal, ficou sem sinal / voltou, atualização do agente, execuções concluídas.
- **Versões de bot.**
  - Cada publicação guarda versão, data, quem publicou, hash do pacote e nota do que mudou.
  - O agente só executa se o hash bater com o da versão em uso.

### 11.6 Agendamentos, alertas e notificações

- **Agendamento** pertence ao bot. Cron de 5 campos, fuso `America/Sao_Paulo`, ativo/pausado. A API devolve as próximas 3 datas, para não depender só do cálculo no navegador.
- **Página Agendamentos** consome uma lista de disparos das próximas 48 h, com bot, pool e cliente.
- **Eventos de alerta:**

| Alvo | Evento |
|---|---|
| Bot | Execução falhou |
| Bot | Execução pendente por mais de 30 min |
| Pool | Máquina sem sinal por mais de 15 min |
| Fila | Item falhou ou foi abandonado (um e-mail por execução, com a lista) |
| Fila | Item abandonado |

- **Envio de alerta:** só por e-mail, uma vez por ocorrência, com link direto para o alvo. Guardar a data do último envio por regra.
- **Notificações do sino:**
  - mesmo conteúdo dos alertas, por usuário;
  - estado lida/não lida e "Marcar todas como lidas";
  - contagem de não lidas na topbar.
- **Contadores da navegação:** execuções `pending` e máquinas `offline` do contexto atual.

### 11.7 Infraestrutura da interface

- **Paginação, ordenação e filtros no servidor**, em todas as listas.
- **Busca global** por referência de item, nome de bot e código de execução.
- **Atualização.**
  - O painel pede dados a cada 15 s.
  - A API devolve a hora da última atualização, que alimenta o indicador "Sincronizado · há Ns".
  - Sem resposta por 2 min, o indicador mostra "Atualização atrasada".
  - Se o servidor não responde, mostra "Sem conexão".
- **Dados pessoais.** Nenhuma tela de cliente exibe CPF ou nome de pessoa. Usuários de cliente são identificados por e-mail.

### 11.8 Decidido na interface, falta confirmar

Estas escolhas estão nos itens acima como padrão, mas ainda não foram validadas:

1. **Limite de "Sem sinal":** 2 min para o status. O alerta só sai depois de 15 min.
2. **Senha esquecida (decidido no M1):** não há fluxo de autoatendimento. O Admin do cliente (ou a Artemisys) usa "Reenviar convite", que invalida a senha e o MFA atuais, encerra as sessões e obriga a definir senha e MFA de novo. O texto do login já orienta a falar com o administrador.
3. **Concorrência:** uma execução por máquina. Execuções do mesmo bot podem rodar em paralelo se houver mais de uma máquina livre no pool.
4. **Limites de retenção:** proposta de 7 a 365 dias.
5. **Notificações:** guardadas por 30 dias.

---

## 12. Textos complementares do M1

Telas e estados que o M1 precisa e que as seções 7 e 9 não trazem. Mesma regra: usar exatamente estes textos.

**Convite — `/invite` (token no fragmento da URL)**
- Título "Crie sua senha", subtítulo "Você vai entrar no Regista com o e-mail <e-mail> e esta senha."
- Campos "Nova senha" (ajuda "Pelo menos 12 caracteres. Evite senhas comuns.") e "Repita a senha"; botão "Criar senha".
- Convite inválido: "Este convite não vale mais" / "O link expirou ou já foi usado. Peça um novo convite ao administrador do seu escritório."

**Erros de senha**
- "Use pelo menos 12 caracteres." · "Essa senha é muito comum. Escolha outra." · "As senhas não são iguais." · "A nova senha precisa ser diferente da atual." · "Senha atual incorreta."

**Login bloqueado ou com limite de tentativas** (banner danger)
- "Muitas tentativas" / "Espere alguns minutos e tente de novo. Se esqueceu a senha, peça ao administrador do seu escritório."

**Código de recuperação — `/login/mfa`**
- Título "Use um código de recuperação", subtítulo "Digite um dos códigos que você guardou ao ativar a verificação. Cada um funciona uma vez."
- Campo "Código de recuperação", botão "Confirmar", link "Usar o app autenticador".
- Erro "Código de recuperação inválido ou já usado."

**Usuários (7.17)**
- Ação "Reenviar convite" na linha (oculta na própria linha).
- AlertDialog "Reenviar convite para <e-mail>?" / "A senha e a verificação em duas etapas atuais deixam de valer e a pessoa sai de todos os aparelhos. Ela recebe um e-mail para criar uma nova senha. Robôs em execução não param." / "Voltar" + "Reenviar convite". Para convite pendente: "O link anterior deixa de valer e a pessoa recebe um novo e-mail."
- Toast "Convite reenviado".
- Erros: "Esta pessoa já tem acesso a este cliente." · "Este e-mail já é usado em outra conta do Regista. Use outro e-mail." · "O cliente precisa de pelo menos um Admin do cliente ativo."

**Minhas sessões (7.18)**
- A lista mostra aparelho, navegador e "ativa agora" / "último uso há X". A cidade aproximada fica para depois (exige GeoIP).
- "Configurar MFA" no menu do rodapé leva ao card "Verificação em duas etapas".
- Card "Senha": "Ao trocar, você continua conectado aqui e sai dos outros aparelhos." + ação "Trocar senha".
- Dialog "Trocar senha": campos "Senha atual", "Nova senha" (mesma ajuda do convite), "Repita a nova senha", "Código de 6 dígitos que aparece no app"; botões "Voltar" + "Trocar senha". Toast "Senha alterada. Os outros aparelhos foram desconectados."
- Dialog "Gerar novos códigos de recuperação?" / "Os códigos atuais deixam de valer. Confirme sua senha para continuar." / campo "Senha" / "Voltar" + "Gerar novos códigos".
- AlertDialog "Encerrar todas as outras sessões?" / "Os outros aparelhos saem do Regista e precisam entrar de novo. Robôs em execução não param." / "Voltar" + "Encerrar todas as outras".

**Clientes (7.16) no M1:** só as colunas Cliente (+ "desde"), Usuários e "Entrar no cliente"; Máquinas, Bots, Última execução e Atenção entram nos marcos que criam esses dados. (No M2 a coluna Máquinas passou a existir: "1 de 3 online", ou "—" sem máquinas.)

**Sem acesso (EmptyState `forbidden`)**
- "Você não tem acesso a esta tela" / "Se precisar dela, peça ao administrador do seu escritório."

**Erros de lista**
- "Não foi possível carregar os clientes" / "…os usuários" / "…as sessões", com "O servidor não respondeu. Tente de novo em alguns segundos." e a ação "Tentar de novo".

**E-mails**
- "Seu acesso ao Regista": convite com o link e a validade de 7 dias.
- "Novo convite para o Regista": o link anterior deixa de valer.
- "Sua senha do Regista foi alterada": "Se não foi você, fale com o administrador do seu escritório."

---

## 13. Textos complementares do M2

Telas e estados de Máquinas e pools (7.13 e 7.14) que as seções 7 e 9 não trazem. Mesma regra: usar exatamente estes textos.

**Banner de máquina sem sinal (7.14)**
- Título "O agente parou de responder".
- Sem outra máquina online no pool: "Até o agente voltar, as execuções do pool ficam pendentes. Confira se o computador está ligado e com internet."
- Com outra máquina online no pool: "Até o agente voltar, as execuções do pool vão para a máquina <nome>. Confira se o computador está ligado e com internet."
- Só em máquina no modo Sessão, acrescenta ao final: "Confira também se o usuário do Windows continua logado."

**Gerar nova chave (7.14)**
- AlertDialog "Gerar nova chave para <nome>?" / "Quando a nova chave for usada, o agente instalado hoje nessa máquina para de funcionar. Se ela não for usada em 24 h, nada muda." / "Voltar" + "Gerar nova chave". Em máquina `pending`, vai direto para o KeyReveal.
- Toast "Nova chave gerada".

**Último sinal (7.13)**
- Online: "há 8s". Sem sinal: "hoje 08:20 · há 2 h" (ou "ontem 08:20" / "dd/MM HH:mm").
- Aguardando cadastro: "Chave gerada há 10 min, ainda não usada"; com a chave vencida, "Chave expirada, gere uma nova".

**KeyReveal**
- Texto abaixo do título: "Use o endereço e a chave no computador onde o agente será instalado. A chave vale uma vez, até <data e hora>."
- Aviso âmbar: "Esta é a única vez que a chave aparece. Se você fechar sem copiar, será preciso gerar uma nova."
- Dois blocos em mono, cada um com seu botão: "Endereço do Regista" (o mesmo que o agente usa no `enroll --url`; "Copiar endereço" → "Copiado") e "Chave de registro" ("Copiar chave" → "Copiada").

**Máquinas e pools**
- Pool sem máquinas: "Nenhuma máquina neste pool ainda."
- Em "Todos os clientes": "Você está vendo todos os clientes. Para cadastrar máquinas ou criar pools, escolha um cliente no topo da barra lateral."
- Revogadas: "Mostrar revogadas (N)" / "Ocultar revogadas".
- Cadastrar máquina sem pools: ajuda do campo Pool "Crie um pool antes de cadastrar a máquina."
- Banner por máquina na lista: "<nome> está sem sinal há <tempo>" (do 7.13).

**Erros de formulário**
- "Já existe uma máquina com esse nome neste cliente."
- "Já existe um pool com esse nome."
- "Use letras minúsculas, números e hífen. Ex.: estacao-atendimento-03" (nome da máquina: `[a-z0-9][a-z0-9-]{0,62}`).
- "O nome não confere." (confirmação de revogar)
- "Escreva um nome para o pool."

**Toasts**
- "Pool criado", "Máquina revogada", "Nova chave gerada".

**Erro de carregamento**
- "Não foi possível carregar a máquina" (7.14), com "O servidor não respondeu. Tente de novo em alguns segundos." e "Tentar de novo".

**Detalhe da máquina (7.14)**
- Máquina inexistente ou de outro cliente: "Máquina não encontrada" / "Confira o endereço ou volte para a lista de máquinas." (máquinas não são removidas, só revogadas).
- Cabeçalho: contexto "Máquina · <pool>" (e "· <cliente>" em "Todos os clientes"); sem sinal: "Último sinal hoje às 08:20, há 2 h".
- Faixa meta: "Cadastrada" mostra data e quem ("11/10/2026 10:15 · pessoa@escritorio.com.br", ou "Equipe Artemisys"); numa máquina revogada, o campo vira "Revogada" com a data.
- Histórico, títulos e detalhes: "Máquina cadastrada" (Agente <versão>), "Agente cadastrado de novo" (O agente anterior deixou de funcionar.), "Ficou online pela primeira vez", "Voltou a ficar online", "Ficou sem sinal", "Agente atualizado" (<versão antiga> → <nova>), "Máquina revogada". Vazio: "Nenhum evento ainda" / "Os eventos aparecem aqui assim que a máquina se cadastrar." Botão "Mostrar mais". Erro: "Não foi possível carregar o histórico".
- Contador ao lado de Máquinas na sidebar, com o plural no `aria-label`: "1 máquina sem sinal" / "N máquinas sem sinal". No seletor de cliente: "N sem sinal".

**Entregue no M3** (dependiam de `bots` e `jobs`): coluna "Agora", linha "Roda: …", aba Execuções e "A execução em andamento será cancelada." no AlertDialog de revogar. Os textos estão na §14.

---

## 14. Textos complementares do M3

Telas e estados do M3 (Bots, Execuções, Detalhe da execução, Dashboard e itens de Máquinas) que as seções 7, 9 e 13 não trazem. Mesma regra: usar exatamente estes textos. **Aprovados na revisão do M3.**

**Decisões de escopo do M3 (aprovadas)**
- O Dashboard do cliente não tem gráfico no M3 (o gráfico "Itens processados por dia" volta com os itens, no M5). A visão consolidada da equipe tem o gráfico "Execuções por dia · 14 dias".
- O Detalhe da execução não tem a aba "Itens" no M3 (volta no M5). "Fila", "Lote" e "Versão do bot" aparecem como "—".
- Em Bots, "Agendar" e "Próxima" ficam ocultos até o M7; a aba Versões do Detalhe do bot fica para o M4.
- Em Execuções, "Executar agora" leva a Bots, como no Dashboard.

**Bots (7.5) e Detalhe do bot (7.6)**

| Tela | Elemento | Texto |
|---|---|---|
| Bots | Subtítulo do bot na lista (no lugar de "fila · versão") | `pacote demo_busca_wikipedia` |
| Bots | Última execução, bot que nunca rodou | "Nunca rodou" |
| Bots | Busca sem resultado | "Nenhum bot com essa busca" / "Confira o nome ou limpe a busca." / "Limpar filtros" |
| Bots | Botão com a execução sendo criada | "Criando execução…" |
| Dialog "Cadastrar bot" | Título e texto | "Cadastrar bot" / "O bot roda nas máquinas do pool escolhido. A versão assinada é publicada depois." |
| Dialog "Cadastrar bot" | Campos | "Nome do bot" (exemplo "Busca na Wikipédia"); "Nome do pacote" (ajuda "É o nome da pasta do robô. Não muda depois de cadastrado."); "Pool" (sem pools: "Crie um pool antes de cadastrar o bot."); "Descrição (opcional)" |
| Dialog "Cadastrar bot" | Botões | "Voltar" e "Cadastrar bot" (carregando: "Cadastrando…") |
| Dialog "Cadastrar bot" | Erros | "Escreva um nome para o bot." · "Use letras minúsculas, números e _, começando por letra. Ex.: demo_busca_wikipedia" · "Já existe um bot com esse nome neste cliente." · "Já existe um bot com esse nome de pacote neste cliente." |
| Cadastrar bot | Toast | "Bot cadastrado" |
| Detalhe do bot | Faixa meta | "Pool", "Máquinas no pool" ("1 de 2 online"), "Sucesso · 30 dias" ("75% das execuções" ou "Sem execuções nos últimos 30 dias"), "Pacote" |
| Detalhe do bot | Abas | "Visão geral" e "Execuções" |
| Detalhe do bot | Aba Execuções vazia | "Este bot ainda não rodou" / "Use Executar agora para fazer a primeira execução." |
| Detalhe do bot | Link da aba Execuções | "Ver todas →" |
| Detalhe do bot | Erro ao carregar a aba | "Não foi possível carregar as execuções" / "O servidor não respondeu. Tente de novo em alguns segundos." / "Tentar de novo" |
| Detalhe do bot | Bot inexistente ou de outro cliente | "Bot não encontrado" / "Confira o endereço ou volte para a lista de bots." / "Voltar para Bots" |
| Detalhe do bot | Erro de carregamento | "Não foi possível carregar o bot" / "O servidor não respondeu. Tente de novo em alguns segundos." |

**Execuções (7.3)**

| Tela | Elemento | Texto |
|---|---|---|
| Execuções | Contagem | "1 execução" / "N execuções" |
| Execuções | Coluna Itens (até o M5) | "—" (com itens: "37 · 3") |
| Execuções | Gatilho de quem é da equipe Artemisys | "Manual" + "Equipe Artemisys" |
| Execuções | Vazio com filtro | "Nenhuma execução com esses filtros" / "Troque o período ou limpe os filtros." / "Limpar filtros" |
| Sidebar | Contador de Execuções (`aria-label`) | "1 execução pendente" / "N execuções pendentes" |

**Detalhe da execução (7.4)**

| Tela | Elemento | Texto |
|---|---|---|
| Detalhe da execução | Faixa meta | "Gatilho", "Pool", "Fila", "Lote" e "Versão do bot" (os três últimos "—" até o M4 e o M5) |
| Detalhe da execução | Botões em andamento | "Reexecutando…" e "Cancelando…" |
| Detalhe da execução | Toasts | "Execução criada" (ação "Ver execução") · "Cancelamento pedido" · "Execução cancelada" |
| Detalhe da execução | Banner danger de execução que falhou | "A execução falhou" + o motivo abaixo |
| Detalhe da execução | Motivo por código | `machine_lost`: "A máquina parou de responder durante a execução." · `machine_revoked`: "A máquina foi revogada durante a execução." · `timeout`: "O robô passou do tempo máximo." · `robot_failed`: "O robô terminou com erro." · `robot_not_found`: "O robô não foi encontrado nesta máquina." · `internal`: "O agente teve um problema durante a execução." · outro código: "A execução terminou com erro." |
| Timeline | Etapa final | "Finalizada" · "Finalizada · Falhou" · "Finalizada · Cancelada" |
| Timeline | Notas das etapas | "há 2 h · nenhuma máquina livre" · "há 12 min · esperando a vez" (a partir de 10 min na fila) · "esperando o robô iniciar" (atribuída) · "cancelamento pedido" (rodando) |
| Detalhe da execução | Aba de captura | "Captura de tela" (execução sem falha) ou "Captura de erro" (falhou), com a contagem |
| Detalhe da execução | Captura de execução sem falha | Título "Captura de tela"; legenda "Captura feita pelo robô durante a execução." (a menção ao prazo de retenção da fila volta no M5; com falha valem os textos do 7.4) |
| LogViewer | Copiar | "Copiar logs" vira "Copiados" por 3 s |
| LogViewer | Execução ativa sem "ao vivo" | "Atualizando a cada 15 s." |
| LogViewer | Execução que terminou sem linhas | "Esta execução não gerou logs." |
| Erros da API | Execuções e bots | "Este bot está desativado e não pode ser executado." · "Este bot ainda não tem uma versão publicada para rodar." · "Essa execução não foi encontrada. Atualize a lista e tente de novo." · "Essa execução já terminou. Atualize a tela para ver como ficou." · "Essa execução ainda está em andamento. Espere terminar para reexecutar." · "Os parâmetros da execução são grandes demais." · "Esse bot não foi encontrado. Atualize a lista e tente de novo." |

**Dashboard (7.2)**

| Tela | Elemento | Texto |
|---|---|---|
| Dashboard do cliente | Banner de máquina sem sinal | Título "<nome> está sem sinal desde 08:20"; o texto é o da §13, igual ao do 7.14 e ao da lista de máquinas |
| Dashboard do cliente | KPI "Máquinas online" | contexto "0 de 1 · 1 sem sinal" |
| Dashboard do cliente | Card "Máquinas" | "Ver máquinas →" · "Executando exec-7f3a24" · "Último sinal há 3 min" · "Ainda sem sinal" · sem máquinas: "Nenhuma máquina cadastrada ainda." |
| Dashboard do cliente | Card "Parados" (só execuções pendentes até o M5) | "Nada parado agora." |
| Dashboard | Últimas execuções sem dados | "Nenhuma execução ainda." |
| Visão geral (equipe) | Banner | "N pontos precisam de atenção" / "1 máquina sem sinal e 1 execução esperando máquina." |
| Visão geral (equipe) | KPIs | "Clientes ativos"; "Execuções · 7 dias" ("1 executando · 1 pendente"); "Taxa de sucesso · 7 dias" ("N execuções terminadas" ou "Nenhuma execução terminada"); "Máquinas sem sinal" ("1 de 3 online") |
| Visão geral (equipe) | Gráfico sem dados | "Nenhuma execução terminada nos últimos 14 dias." |
| Visão geral (equipe) | Card "Precisa de atenção" vazio | "Nada precisa de atenção agora." |
| Visão geral (equipe) | Tabela "Por cliente" | Colunas "Cliente", "Execuções · 7 dias", "Sucesso", "Máquinas" ("1 de 3 online" e "1 sem sinal"), "Última execução"; ação "Ver clientes →" |

**Máquinas e pools (7.13) e Detalhe da máquina (7.14)**

| Tela | Elemento | Texto |
|---|---|---|
| Máquinas e pools | Linha do pool | "Roda: Controle de Acordos, Busca na Wikipédia" ou "Nenhum bot usa este pool ainda" |
| Máquinas e pools | Coluna "Agora" | "Executando exec-7f3a24" (link) ou "—" |
| Detalhe da máquina | Aba Execuções | Colunas "Execução", "Bot", "Status", "Gatilho", "Início", "Duração"; vazio: "Nenhuma execução nesta máquina ainda" / "As execuções que ela rodar aparecem aqui."; erro: "Não foi possível carregar as execuções" |
| Detalhe da máquina | Revogar com execução em andamento | acrescenta ao texto: "A execução em andamento será cancelada." |

---

## 15. Textos complementares do M4

**PROPOSTA, aguardando aprovação do responsável.** Telas e estados do M4 (aba Versões, publicação, versão em uso, motivos de recusa do agente e a indicação de máquina pausada) que as seções 7, 9 e 14 não trazem. Depois de aprovada, vale a mesma regra: usar exatamente estes textos.

**Decisões de escopo do M4 (aprovadas no plano)**
- Publicar versão e colocar uma versão em uso: só a equipe Artemisys. A ativação tem registro próprio no `audit_log` (`bot.version_activated`), separado da publicação (`bot.version_published`), mesmo quando feita pela caixa do dialog.
- O painel **não** pausa nem retoma máquinas: o kill switch é local. O painel só mostra a indicação "Pausada nesta máquina".
- A versão de uma execução é a que estiver em uso **quando uma máquina a assume**. Trocar a versão em uso vale para a próxima execução assumida, inclusive as pendentes.

**Detalhe do bot (7.6): aba Versões**

| Elemento | Texto |
|---|---|
| Aba | "Versões" (a aba "Visão geral" e "Execuções" continuam) |
| Nota (já em 7.6) | "Cada versão é assinada. O agente só roda o código cujo hash bate com o publicado." |
| Colunas (já em 7.6) | "Versão" (+ badge "Em uso"), "Publicada em", "Por", "Hash", "O que mudou" |
| Ação por linha (só Artemisys, versão que não está em uso) | "Colocar em uso" (secondary) |
| Vazio (Artemisys) | "Nenhuma versão publicada" / "Publique a primeira versão assinada para este bot poder rodar." / "Publicar versão" |
| Vazio (demais papéis) | "Nenhuma versão publicada" / "A equipe Artemisys ainda não publicou uma versão deste bot." |
| Erro ao carregar | "Não foi possível carregar as versões" / "O servidor não respondeu. Tente de novo em alguns segundos." / "Tentar de novo" |
| Confirmação de "Colocar em uso" (AlertDialog) | "Colocar a versão 1.2.0 em uso?" / "A próxima execução de <bot> vai usar esta versão. Execuções em andamento continuam com a versão atual." / "Voltar" + "Colocar em uso" |
| Toasts | "Versão 1.2.0 publicada" · "Versão 1.2.0 em uso" |

**Dialog "Publicar versão" (só Artemisys)**

| Elemento | Texto |
|---|---|
| Título e texto | "Publicar versão" / "O pacote e a assinatura saem do regista-pack. A versão é lida da assinatura." |
| Campos | "Pacote (.rgpkg)"; "Assinatura (.rgsig)"; "O que mudou" (ajuda "Aparece na lista de versões. Até 2000 caracteres."); caixa "Colocar em uso assim que for publicada" (desmarcada por padrão) |
| Botões | "Voltar" e "Publicar versão" (carregando: "Conferindo…" na assinatura e "Enviando…" no pacote) |
| Erros de campo | "Escolha o arquivo do pacote (.rgpkg)." · "Escolha o arquivo da assinatura (.rgsig)." · "Esse arquivo não é uma assinatura do regista-pack." · "Escreva até 2000 caracteres." |
| Erros da API | "A assinatura não confere com nenhuma chave confiável. Gere o pacote de novo com o regista-pack." · "Este pacote é de outro cliente. Gere o pacote para o cliente deste bot." · "Este pacote não é deste bot. O nome do pacote do bot é <pacote>." · "A versão 1.2.0 já foi publicada para este bot." · "O arquivo enviado não é o que foi assinado. Gere o pacote de novo e publique outra vez." · "O pacote passa de 200 MB." · "Não foi possível enviar o pacote. Tente de novo." |
| Erro ao colocar em uso | "Essa versão não é deste bot. Atualize a lista e tente de novo." |

**Badges e subtítulos de versão**

| Tela | Elemento | Texto |
|---|---|---|
| Detalhe do bot | Badge accent no cabeçalho | "v1.2.0 em uso" |
| Detalhe do bot | Sem versão em uso (badge neutro) | "Sem versão em uso" |
| Bots | Subtítulo do bot na lista (acrescenta a versão ao da §14) | `pacote demo_busca_wikipedia · v1.2.0`; sem versão em uso: `pacote demo_busca_wikipedia · sem versão` |
| Detalhe da execução | "Versão do bot" na faixa meta | "v1.2.0" (com Tooltip do hash curto); execução em dev, sem versão: "—" |
| Erro da API ao executar | Já existe na §14 | "Este bot ainda não tem uma versão publicada para rodar." (vale também quando há versões, mas nenhuma está em uso) |

**Detalhe da execução (7.4): motivos de recusa do agente**

O banner "A execução falhou" (§14) ganha um texto **fixo por código**. O painel nunca exibe texto livre vindo do agente nestes casos: o agente envia o código do erro e, no `package_invalid`, um **código de motivo de uma lista fechada**, validado no servidor (valor fora da lista é descartado e o banner mostra só a primeira linha). A mensagem livre do agente aparece apenas nos logs da execução, escapada. Para `robot_not_allowed`, `runtime_missing` e `environment_failed` o servidor também não guarda a mensagem livre.

| Código | Texto do banner |
|---|---|
| `package_invalid` | Primeira linha: "A máquina recusou o pacote do robô." Segunda linha: o texto do motivo (tabela abaixo) |
| `robot_not_allowed` | "Este robô não está na lista de robôs permitidos desta máquina." |
| `runtime_missing` | "Falta preparar esta máquina para a versão 1.2.0 do robô: rode regista-agent setup como administrador." (a versão vem da execução, não do agente) |
| `environment_failed` | "Não foi possível montar o ambiente do robô nesta máquina." |

| Motivo (`package_invalid`) | Segunda linha do banner |
|---|---|
| `hash_mismatch` | "O arquivo do pacote não bate com o hash publicado." |
| `signature_invalid` | "A assinatura do pacote não confere." |
| `unknown_key` | "O pacote foi assinado com uma chave em que esta máquina não confia." |
| `wrong_client` | "O pacote é de outro cliente." |
| `wrong_package` | "O pacote não é deste robô." |
| `wrong_version` | "A versão do pacote não é a desta execução." |
| `unsafe_archive` | "O pacote tem um arquivo com caminho inseguro." |
| `too_large` | "O pacote tem arquivos demais ou grandes demais." |
| `malformed_package` | "O pacote está corrompido ou fora do formato esperado." |

**Máquinas (7.13 e 7.14): indicação de máquina pausada localmente**

| Tela | Elemento | Texto |
|---|---|---|
| Máquinas e pools | Marca ao lado da pill de status (só quando pausada) | "Pausada nesta máquina" |
| Detalhe da máquina | Banner de aviso (warning) | Título "Pausada nesta máquina" / "O agente continua online, mas não pega novas execuções até alguém retomar na própria máquina. O painel não consegue retomar." |
| Detalhe da máquina | Execuções pendentes por causa da pausa | sem texto novo: a Timeline mostra "nenhuma máquina livre", como hoje |

**Glossário de ações (acrescenta à §9)**

| Ação | Texto do botão | Onde |
|---|---|---|
| Subir uma versão assinada | Publicar versão | Detalhe do bot, aba Versões (já em §4) |
| Trocar a versão que roda | Colocar em uso | Detalhe do bot, aba Versões |

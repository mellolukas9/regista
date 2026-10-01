# Frontend

Painel em Next.js (App Router, TypeScript strict) com tema escuro. Tailwind com os tokens abaixo como variáveis CSS. Componentes base com shadcn/ui ajustados ao tema. Dados com TanStack Query; tabelas grandes com TanStack Table e paginação no servidor; gráficos com Recharts.

## Tokens

| Token | Valor | Uso |
|---|---|---|
| `--bg` | `#0A0C0F` | Fundo da página |
| `--sidebar` | `#0D1014` | Barra lateral, cabeçalhos de tabela |
| `--panel` | `#111419` | Cards e painéis |
| `--panel-2` | `#161A21` | Item ativo, hover |
| `--border` | `#1C2129` | Bordas de cards |
| `--border-strong` | `#232A34` | Bordas de controles |
| `--text` | `#E7EAEE` | Texto principal |
| `--text-2` | `#C9CFD8` | Texto secundário em tabelas |
| `--muted` | `#98A2B3` | Rótulos |
| `--faint` | `#7D8796` | Metadados |
| `--accent` | `#C8F169` | Ação principal, item ativo (texto escuro `#0A0C0F` sobre ele) |
| `--ok` | `#4ADE80` | Sucesso, online |
| `--warn` | `#F5C451` | Pendente, atenção |
| `--info` | `#7AB8FF` | Executando |
| `--danger` | `#FF8A7A` | Falha (barras de gráfico: `#E5604F`) |

Status usam texto + ponto colorido + fundo com 10% de opacidade da cor + borda com 24%; nunca só a cor.

Tipografia: **Geist** (interface) e **Geist Mono** (filas, horários, IDs, logs). Raio: 10px em controles, 14px em cards. Alvos de toque com no mínimo 44px.

## Layout

- Barra lateral fixa de 248px: marca, seletor de cliente (administradores da plataforma), navegação "Operação" (Dashboard, Bots, Execuções, Filas, Lotes, Agendamentos, Máquinas, Alertas), usuário e sair.
- Conteúdo com largura máxima de ~1240px; cabeçalho com caminho (cliente / página), título, indicador de sincronização, busca e notificações.
- Abaixo de 900px a barra lateral vira menu recolhível.

## Telas e marco correspondente

| Tela | Marco | Conteúdo principal |
|---|---|---|
| Login, MFA | M1 | Formulário; configuração de TOTP com QR code; códigos de recuperação |
| Usuários | M1 | Lista, convite, papéis, MFA, revogar sessões |
| Máquinas e pools | M2 | Status online/offline, último sinal, versão do agente, gerar chave de registro (exibida uma vez), revogar |
| Bots | M3 | Cards com fila, pool, última execução, faixa das últimas 10 execuções, "Executar agora", "Agendar" |
| Execuções | M3 | Filtros por status; tabela; painel lateral com linha do tempo (Criada, Na fila, Executando, Finalizada), logs estilo terminal, reexecutar, cancelar |
| Filas e itens | M5 | Contagem por status; tabela paginada de itens; detalhe com tentativas, logs do item e evidências; reprocessar |
| Lotes | M6 | Resumo reconciliado (lidas, enfileiradas, rejeitadas, sucesso, falha); relatório por referência; exportar; reprocessar falhas |
| Agendamentos | M7 | Atalhos (De hora em hora, Diário, Dias úteis, Personalizado), cron gerado, descrição em português, próximas 3 execuções |
| Alertas | M7 | Regras por alvo e evento, destino de e-mail |
| Dashboard | M3+ (evolui a cada marco) | Indicadores, execuções por dia, máquinas, últimas execuções, alerta de itens ou execuções paradas |

## Regras

- Nenhum token ou segredo em `localStorage`; autenticação só por cookie httpOnly.
- Todo texto vindo de logs, erros ou payloads é exibido escapado (nunca `dangerouslySetInnerHTML`).
- Estados vazios explicam o que fazer a seguir; erros dizem o que aconteceu e como corrigir.
- Textos da interface em português, frases curtas, sem jargão interno.

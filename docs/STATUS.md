# Status do projeto

_Atualize este arquivo ao final de cada marco ou sessão de trabalho relevante._

## Agora

- **Marco atual:** M1 — Clientes, autenticação e usuários (branch `m1-auth`, plano aprovado em 2026-10-02)
- **Situação:** M0 mergeado (PR #1). M1 em andamento, seguindo o plano aprovado (15 passos, cada um com verificação e commit).
- **Próximo passo:** executar os passos do plano do M1 em ordem; PR em rascunho depois do passo 9 (CLI e seed) para a CI rodar no Linux; ao fim, `gh pr ready`, sem merge.

## Decisões já aprovadas para o M0 (não perguntar de novo)

| Tema | Decisão |
|---|---|
| PostgreSQL | 18 (imagem `postgres:18`; o volume monta em `/var/lib/postgresql`) |
| Python | 3.13 em `.python-version`, `requires-python >=3.12` |
| SQLAlchemy | 2.1.x; se houver incompatibilidade com asyncpg/Alembic, 2.0.x |
| TypeScript | a versão mais recente **oficialmente suportada** pelo `typescript-eslint` e pelo Next.js (não usar a 7.x nativa ainda) |
| Node.js | 24 LTS |
| Next.js / Tailwind | Next 16 (o `next lint` não existe mais: `npm run lint` chama o ESLint direto) / Tailwind v4 CSS-first |
| UUID v7 | avaliar `uuidv7()` nativo do PostgreSQL 18 como default das PKs; se mantiver na aplicação, justificar em uma linha |
| RLS | políticas separadas por comando, `INSERT` em `tenants` só para admin da plataforma (já refletido em `docs/specs/security.md`) |
| Roles do banco | criados por script de init (`infra/compose/initdb/`) e pela fixture de testes; migrations com grants explícitos |
| S3 local | fora do M0; escolha por ADR no início do M3 |
| Proxy do Next | `/api/*` → API removendo o prefixo |
| Estrutura | mínima: sem pastas vazias de módulos futuros |
| `.gitattributes` | LF padrão; CRLF para `*.bat`, `*.cmd`, `*.ps1` |
| Push e PR | autorizado para `mellolukas9/regista`, após conferir `gh auth status` |

## Decisões aprovadas para o M1 (não perguntar de novo)

| Tema | Decisão |
|---|---|
| Senha esquecida | Sem autoatendimento no MVP. "Reenviar convite" (Admin do cliente ou equipe Artemisys) invalida a senha e o MFA atuais, encerra as sessões do usuário e obriga a definir senha e configurar MFA de novo |
| E-mail | Único **global** (`citext unique` em `users`). Convidar um e-mail que existe em outro cliente dá erro genérico, sem revelar qual cliente |
| Login e RLS | A busca por e-mail, token de convite e token de sessão (antes de existir cliente na sessão) usa funções `SECURITY DEFINER` mínimas (`app.lookup_login`, `app.lookup_invitation`, `app.lookup_session`), com `search_path` fixo e `EXECUTE` só para `regista_app`. A aplicação nunca usa o role owner e RLS nunca é contornado de forma genérica |
| Equipe Artemisys | Tenant interno oculto (`tenants.is_internal`, só um, nunca desativado, fora de Clientes, do seletor e das visões consolidadas). `is_platform_admin` só no tenant interno, e todo usuário dele é platform admin, garantido por trigger no banco |
| Bootstrap e gestão da equipe | CLI `regista-admin` (criar, reenviar convite, remover; recusa remover o último admin da plataforma ativo). Sem senha por argumento; nada por migration ou seed de produção. **Tela de gestão da equipe Artemisys fica fora do M1** |
| Trocar senha | Em Minhas sessões: senha atual + nova + código TOTP; encerra as outras sessões, audita e envia e-mail |
| E-mail transacional | Interface `EmailSender`; em dev imprime no console. SMTP real no M7/M8 |
| Prazos padrão | Convite 7 dias; sessão 12 h de inatividade e 7 dias no total; sessão parcial (antes do MFA concluído) 10 min. Configuráveis em `Settings` |
| Cidade da sessão | Fora do M1 (exigiria GeoIP). A lista mostra aparelho, navegador e último uso |
| Cabeçalhos | `Cache-Control: no-store` em `/auth/*` e `/account/*`; `nosniff` na API; no Next, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff` e `Referrer-Policy`. A CSP completa de scripts fica para o M8 |

**Risco conhecido e aceito no MVP:** o bloqueio progressivo por conta permite que alguém bloqueie a conta de outra pessoa errando a senha de propósito. Mitigação: teto de 15 min e rate limit por e-mail. Evolução possível: considerar o IP no bloqueio.

## Contexto

O Regista começou como um piloto (início de 2026) para orquestrar automações locais com Prefect OSS, FastAPI e Next.js. O piloto validou o fluxo, mas suas premissas não servem para um produto multi-cliente. A v2 é uma reescrita do zero com as decisões registradas em `docs/adr/`. Nenhum código do piloto foi mantido.

A interface foi desenhada e entregue como handoff em `docs/specs/design-system.md` (azul Artemisys, telas, textos, permissões e decisões de produto na seção 11). As specs foram atualizadas para refletir essas decisões.

## Decisões em aberto

| Tema | Quando decidir | Observação |
|---|---|---|
| Limites operacionais | Antes dos marcos que os usam | "Sem sinal" após 2 min e alerta após 15 min (M2/M7); uma execução por máquina (M3); retenção de 7 a 365 dias (M5); notificações por 30 dias (M7) |
| "Enviar planilha" em fila no modo Referência | Antes do M6 | Proposta: o navegador lê o arquivo e envia só referência, linha, nomes das colunas e hash |
| S3 local de desenvolvimento | Início do M3 | MinIO community sem imagens publicadas; escolher alternativa por ADR |
| Região de hospedagem (São Paulo x EUA) | Antes do M8 | Custo x preferência de clientes por dados no Brasil |
| Certificado de assinatura de código do executável Windows | Antes do M8 | Necessário para o MSI não ser bloqueado |
| Provedor de identidade externo (SSO) | Fora do MVP | Autenticação própria agora, preparada para SSO depois |

## Registro

| Data | Marco | O que aconteceu |
|---|---|---|
| 2026-09-30 | — | Documentação de base da v2 criada (arquitetura, ADRs, specs, roadmap). |
| 2026-09-30 | M0 | Plano do M0 aprovado com ajustes (ver "Decisões já aprovadas"). Spec de RLS corrigida: políticas separadas por comando. |
| 2026-10-02 | — | Pasta local perdida. Handoff de interface incorporado (`docs/specs/design-system.md`); specs, ROADMAP e ADR 0011 atualizados com as decisões de produto. |
| 2026-09-30 | M0 | Primeira execução do M0 (branch `m0-foundation`, PR #1): workspace uv, FastAPI com `/health`, SQLAlchemy async + Alembic, Postgres 18 com roles `regista_owner`/`regista_app`, migration `0001` (`tenants` + RLS com `FORCE`), `tenant_session`, testes de RLS com Testcontainers, Next 16 com proxy `/api/*`, pre-commit e CI. PKs com `uuidv7()` nativo do PG18; TypeScript 6.0.3 e ESLint 9 (versões mais recentes suportadas pelo typescript-eslint e pelo eslint-config-next); Ryuk do Testcontainers desligado nos testes (falha de porta no Docker Desktop do Windows). |
| 2026-10-02 | M0 | Pasta local recuperada a partir de `origin/m0-foundation`; docs reconciliados arquivo a arquivo (docs novos prevalecem; mantidos `.gitignore`, `uuidv7()` nativo, Comandos do `CLAUDE.md` e políticas `tenant_*` da branch). `ARCHITECTURE.md` passou a PostgreSQL 18. |
| 2026-10-02 | M0 | Frontend alinhado ao `design-system.md`: tokens da seção 2 (azul Artemisys), Geist, shell com sidebar de 248px (itens não navegáveis), topbar (caminho, sincronização, busca e sino desabilitados) e menu recolhível abaixo de 900px; `lucide-react` adicionado. Critério de pronto conferido no Windows: 15 testes, ruff, mypy, lint, typecheck e build verdes; `/health` e `/api/health` (proxy) respondem. Sem `shadcn init` (adiado até o primeiro marco que precisar de componentes). |
| 2026-10-02 | M1 | Branch `m1-auth` aberta. Testes da API passam a usar `httpx.AsyncClient` + `ASGITransport` (helper `api_client` em `apps/api/tests/conftest.py`, que também executa o lifespan do app) no lugar do `TestClient`; o `StarletteDeprecationWarning` do M0 deixou de aparecer. |
| 2026-10-02 | M1 | Leitura completa e obrigatória feita (CLAUDE.md, STATUS, ROADMAP, ARCHITECTURE, ADRs 0001–0016, specs `agent`, `orchestration`, `data-model`, `security`, `frontend` e `design-system` inteiros). Plano do M1 aprovado com ajustes (ver "Decisões aprovadas para o M1"). ADR 0017 proposta. |

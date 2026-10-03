# ADR 0017: Ajustes na autenticação (MFA para todos, convite como recuperação, tenant interno)

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Ao planejar o M1, a ADR 0005 (MFA obrigatório "para administradores") ficou em conflito com `security.md`, `data-model.md` e `design-system.md` §11.1 (MFA para todos). Além disso, o login precisa achar o usuário pelo e-mail antes de existir cliente na sessão, o que o RLS bloqueia, e a equipe Artemisys precisa de um cliente (`tenant_id` é obrigatório em `users`) sem aparecer como cliente de verdade.

## Decisão

1. **MFA TOTP obrigatório para todos os usuários**, sem exceção. A ADR 0005 continua valendo no restante.
2. **Senha esquecida sem autoatendimento.** "Reenviar convite" (Admin do cliente ou equipe Artemisys) invalida a senha e o MFA atuais, apaga os códigos de recuperação, encerra as sessões e obriga a definir senha e MFA de novo.
3. **E-mail único global** em `users`. Convidar um e-mail de outro cliente dá erro genérico, sem revelar o cliente.
4. **Busca antes do tenant por funções `SECURITY DEFINER` mínimas** (`app.lookup_login`, `app.lookup_invitation`, `app.lookup_session`, `app.rate_limit_hit`): dono `regista_owner`, `search_path` fixo, `EXECUTE` só para `regista_app`, devolvendo apenas o necessário. A aplicação continua sem `BYPASSRLS` e nunca usa o role owner; toda escrita posterior roda com o tenant do usuário.
5. **Tenant interno oculto** (`tenants.is_internal`) para a equipe Artemisys: só um, nunca desativado, fora das listas de clientes, do seletor e das visões consolidadas. `is_platform_admin` só existe nele, e todo usuário dele é platform admin (trigger no banco). Bootstrap e gestão pelo CLI `regista-admin`.
6. **Rate limit e bloqueio no Postgres** (ADR 0003): janelas fixas em `auth_rate_limits`, acessível só pela função `app.rate_limit_hit`.

## Consequências

Sem ambiguidade de login e sem ADR conflitando com as specs. As funções `SECURITY DEFINER` são a única exceção ao "nada contorna RLS" e têm teste próprio. A equipe Artemisys não tem tela de gestão no MVP (só CLI). Quem esquece a senha depende de um administrador. O bloqueio por conta permite bloquear a conta de outra pessoa de propósito (risco aceito, limitado a 15 min e ao rate limit por e-mail).

## Alternativas descartadas

E-mail único por cliente (exigiria escolher o cliente no login, tela não desenhada); redefinição de senha por e-mail (mais superfície de ataque no MVP); leitura de `users` pelo login com role `BYPASSRLS` (quebra a regra inegociável 1); platform admins no cliente de demonstração (misturaria a equipe com usuários de cliente).

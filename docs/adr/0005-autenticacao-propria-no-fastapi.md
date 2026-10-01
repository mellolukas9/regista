# ADR 0005: Autenticação própria no FastAPI

- **Status:** aceita
- **Data:** 2026-10

## Contexto

O piloto usava NextAuth com provider Credentials, dividindo a autenticação entre Next.js e FastAPI e deixando de fora MFA, bloqueio, redefinição de senha e revogação.

## Decisão

Autenticação centralizada na API: argon2id, sessão no servidor com cookie httpOnly, CSRF por header, MFA TOTP obrigatório para administradores, bloqueio progressivo e rate limit. O Next.js apenas repassa via proxy.

## Consequências

Controle total e um único lugar para regras de segurança. Desenho preparado para plugar um provedor de identidade (SSO) no futuro.

## Alternativas descartadas

NextAuth/Auth.js; provedores gerenciados (Cognito, Clerk, Auth0) ou self-hosted (Keycloak, Zitadel), adiados até um cliente pedir SSO.

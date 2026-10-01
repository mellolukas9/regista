# Segurança

Princípio: **nenhum lado confia cegamente no outro.** Uma máquina de cliente comprometida não alcança outros clientes, e um Regista comprometido não consegue executar código arbitrário nas máquinas dos clientes.

## Isolamento entre tenants (Row-Level Security)

### Roles do banco

| Role | Uso | Permissões |
|---|---|---|
| `regista_owner` | Migrations; dono das tabelas | DDL |
| `regista_app` | Runtime da API e das tarefas | DML conforme necessário; **sem `BYPASSRLS`**, não é dono de tabela, não é superuser |

### Padrão para toda tabela de tenant

```sql
ALTER TABLE <tabela> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <tabela> FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON <tabela>
  USING (
    tenant_id = app.current_tenant_id()
    OR app.is_platform_admin()
  )
  WITH CHECK (tenant_id = app.current_tenant_id());
```

Funções auxiliares (schema `app`):

```sql
CREATE FUNCTION app.current_tenant_id() RETURNS uuid
  LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;

CREATE FUNCTION app.is_platform_admin() RETURNS boolean
  LANGUAGE sql STABLE AS
  $$ SELECT coalesce(current_setting('app.platform_admin', true), '') = 'on' $$;
```

Regras:

- Toda unidade de trabalho abre uma transação e executa `select set_config('app.tenant_id', :tid, true)` (o `true` limita à transação). Nada de `SET` em nível de sessão: conexões voltam ao pool.
- Sem tenant definido, as consultas retornam zero linhas.
- `app.platform_admin = 'on'` só é definido para usuários com `is_platform_admin` e permite **leitura** entre tenants (visão "Todos os clientes"). Escrita sempre exige o tenant de contexto (`WITH CHECK`).
- Requisições de agente e de robô definem o tenant a partir do token, nunca de parâmetro da requisição.
- Testes: a fixture de isolamento roda cada rota autenticada com usuário de outro tenant e espera `404` (preferível a `403`, para não revelar existência).

## Autenticação de usuários

- Senhas com argon2id (`argon2-cffi`), política mínima de tamanho e verificação contra senhas comuns.
- Sessão no servidor: cookie `regista_session` httpOnly, `Secure` (em produção), `SameSite=Lax`; banco guarda só o hash do token.
- CSRF: token por sessão enviado no header `X-CSRF-Token` em toda requisição que altera dados.
- MFA por TOTP (`pyotp`), obrigatório para `tenant_admin` e administradores da plataforma; códigos de recuperação com hash.
- Bloqueio progressivo após tentativas erradas e rate limit por IP e por e-mail no login.
- Sessões revogáveis; expiração por inatividade e absoluta.
- Em dev, o Next.js faz proxy de `/api/*` para a API, mantendo o cookie first-party.

## Autenticação de máquinas e robôs

Ver `agent.md`: chave de registro de uso único → par Ed25519 local → desafio assinado → token de 15 min com escopo de máquina. Robôs recebem token de job com escopo limitado às filas do job.

## Segredos e criptografia

- Abstração `KeyProvider` com `encrypt(tenant_id, plaintext) -> (ciphertext, key_id)` e `decrypt`.
  - Dev: `LocalKeyProvider` (chave mestra em variável de ambiente, AES-GCM).
  - Produção (M8): `KmsKeyProvider` com envelope encryption e **uma chave por tenant**.
- Encerramento de contrato: apagar a chave do tenant torna ilegível o que restar, inclusive em backups.
- Segredos de robôs só são entregues durante a execução do job que os usa; nunca em disco no agente.

## Pacotes assinados

- A chave privada de assinatura **não** fica no servidor nem no repositório: fica na máquina de build da Artemisys (ou em CI isolado).
- O agente embute a chave pública e recusa qualquer pacote sem assinatura válida.
- A API do agente não tem nenhuma forma de enviar comando de shell ou código fora de pacote assinado.

## Modelo de ameaças

| Ameaça | Contenção |
|---|---|
| Máquina do cliente invadida | Credencial restrita à máquina e ao tenant; revogável; sem listagem geral |
| Rota esquece o filtro de tenant | RLS no Postgres + testes de isolamento em todas as rotas |
| Banco do Regista vaza | Chaves privadas ficam nas máquinas; segredos e payloads criptografados por tenant |
| Servidor do Regista invadido | Pacotes assinados fora do servidor; sem comando arbitrário; allowlist local no agente |
| Rede do cliente exposta | Somente conexão de saída; sem portas abertas; domínio fixo; TLS verificado |
| Robô explorado | Usuário Windows dedicado sem admin |
| Conteúdo malicioso em logs | Tratado como não confiável: limite de tamanho, exibição escapada |
| Uso indevido de conta | MFA, bloqueio, sessões revogáveis, auditoria |

## Dados do cliente

Cada item tem duas partes:

- **Controle** (referência, status, tentativas, tempos, tipo de erro): sempre no Regista.
- **Conteúdo**: segue o `data_mode` da fila.

| Modo | Conteúdo | Painel mostra |
|---|---|---|
| `reference` (padrão) | Fica no sistema do cliente; o item guarda um ponteiro (`payload_ref`) | Referência, status e campos visíveis |
| `central` | No Regista, criptografado com a chave do tenant (`payload_enc`) | Tudo |
| `e2e` (reservado, fora do MVP) | Cifrado com chave que só existe no ambiente do cliente | Referência e status |

Vazamentos laterais tratados:

- `reference` deve ser um identificador neutro (ex.: `ACORDO-0042`), nunca CPF.
- O SDK mascara CPF, CNPJ e e-mail em mensagens de erro e logs antes de enviar.
- Campos visíveis (`visible_fields`) são escolhidos por fila.
- Retenção: o conteúdo é apagado após `retention_days` da conclusão; o controle permanece.
- Screenshots de filas sensíveis podem ficar no armazenamento do cliente (evolução futura; no MVP vão para o S3 do Regista com acesso por URL pré-assinada de curta duração).

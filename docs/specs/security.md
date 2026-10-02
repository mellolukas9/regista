# Segurança

Princípio: **nenhum lado confia cegamente no outro.** Uma máquina de cliente comprometida não alcança outros clientes, e um Regista comprometido não consegue executar código arbitrário nas máquinas dos clientes.

## Isolamento entre tenants (Row-Level Security)

### Roles do banco

| Role | Uso | Permissões |
|---|---|---|
| `regista_owner` | Migrations; dono das tabelas | DDL |
| `regista_app` | Runtime da API e das tarefas | DML conforme necessário; **sem `BYPASSRLS`**, não é dono de tabela, não é superuser |

### Padrão para toda tabela de tenant

Políticas **separadas por comando**. Uma política única com `USING (... OR is_platform_admin())` valeria também para `UPDATE` e `DELETE`, permitindo ao admin da plataforma alterar ou apagar dados de qualquer tenant; por isso a leitura entre tenants fica restrita ao `SELECT`.

```sql
ALTER TABLE <tabela> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <tabela> FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_select ON <tabela> FOR SELECT
  USING (tenant_id = app.current_tenant_id() OR app.is_platform_admin());

CREATE POLICY tenant_insert ON <tabela> FOR INSERT
  WITH CHECK (tenant_id = app.current_tenant_id());

CREATE POLICY tenant_update ON <tabela> FOR UPDATE
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());

CREATE POLICY tenant_delete ON <tabela> FOR DELETE
  USING (tenant_id = app.current_tenant_id());
```

Os nomes das políticas (`tenant_select` etc.) são por tabela. O padrão é gerado pelo helper `regista_api.core.rls.tenant_rls_statements(tabela)`, que as migrations devem usar. O teste `test_every_tenant_table_has_forced_rls_and_policies` falha se uma tabela nascer sem RLS habilitado e forçado e sem as quatro políticas.

Tabela `tenants` (usa `id`): `SELECT` com `id = app.current_tenant_id() OR app.is_platform_admin()`; `INSERT` liberado só para `app.is_platform_admin()` (quem cria tenants); `UPDATE` só no próprio tenant de contexto. **Não há `DELETE`** (nem grant, nem política): tenants são desativados (`is_active`), nunca apagados.

Roles: criados por script de init do banco (dev: `infra/compose/initdb/`; testes: fixture como superuser; produção: script de provisionamento), nunca por migration. A migration roda como `regista_owner` e concede `GRANT` explícitos por tabela a `regista_app` (sem `DEFAULT PRIVILEGES`), além de `USAGE` no schema `app` e `EXECUTE` nas funções. Scripts de init só rodam com volume vazio.

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
- Para o admin da plataforma, o cliente de contexto vem do cookie `rg_client` validado pela API (vazio = "Todos os clientes": leitura consolidada).
- Permissões por papel (matriz em `design-system.md` §4) são aplicadas **no servidor**. Exclusivas da Artemisys: criar cliente, cadastrar bot, publicar versão, criar fila, mudar modo de dados e máximo de tentativas.
- Testes: a fixture de isolamento roda cada rota autenticada com usuário de outro tenant e espera `404` (preferível a `403`, para não revelar existência).

## Autenticação de usuários

- Senhas com argon2id (`argon2-cffi`), política mínima de tamanho e verificação contra senhas comuns.
- Sessão no servidor: cookie `regista_session` httpOnly, `Secure` (em produção), `SameSite=Lax`; banco guarda só o hash do token.
- CSRF: token por sessão enviado no header `X-CSRF-Token` em toda requisição que altera dados.
- MFA por TOTP (`pyotp`) **obrigatório para todos os usuários**; 10 códigos de recuperação de uso único (gerar novos invalida os anteriores e exige confirmar a senha).
- Entrada de usuários só por **convite** (link com token de uso único → definir senha → configurar MFA). Não há autocadastro.
- Senha esquecida: sem autoatendimento no MVP; o Admin do cliente (ou a Artemisys) usa "Reenviar convite", que invalida senha, MFA, códigos e sessões (ADR 0017).
- Troca de senha pelo próprio usuário exige senha atual, nova senha e código TOTP; encerra as outras sessões.
- Busca antes do tenant (login, convite, sessão, rate limit): funções `SECURITY DEFINER` mínimas, `search_path` fixo, `EXECUTE` só para `regista_app`; nada de role owner nem `BYPASSRLS` na aplicação (ADR 0017).
- Respostas de `/auth/*` e `/account/*` levam `Cache-Control: no-store`; a API envia `X-Content-Type-Options: nosniff`.
- "Remover acesso" desativa o usuário e encerra as sessões na hora. Encerrar sessões nunca interrompe robôs.
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

"Enviar planilha" numa fila em modo `reference`: o conteúdo da planilha não pode ser guardado no Regista. Proposta (a confirmar antes do M6): o navegador lê o arquivo localmente e envia só referência, número da linha, nomes das colunas e hash do arquivo; nada do conteúdo é armazenado. Em modo `central`, o arquivo vai para a API normalmente.

Vazamentos laterais tratados:

- `reference` deve ser um identificador neutro (ex.: `ACORDO-0042`), nunca CPF.
- O SDK mascara CPF, CNPJ e e-mail em mensagens de erro e logs antes de enviar.
- Campos visíveis (`visible_fields`) são escolhidos por fila.
- Retenção: o conteúdo é apagado após `retention_days` da conclusão; o controle permanece.
- Screenshots de filas sensíveis podem ficar no armazenamento do cliente (evolução futura; no MVP vão para o S3 do Regista com acesso por URL pré-assinada de curta duração).

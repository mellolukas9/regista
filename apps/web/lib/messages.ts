import { ApiError, NETWORK_ERROR_CODE } from "@/lib/api";

// Textos de erro por código da API (docs/specs/design-system.md, seções 7 e 12).
// O servidor devolve só o código; o texto vive aqui, em português.
const MESSAGES: Record<string, string> = {
  rate_limited:
    "Muitas tentativas. Espere alguns minutos e tente de novo.",
  invalid_code:
    "Código incorreto ou expirado. Os códigos mudam a cada 30 segundos; use o que está na tela agora.",
  invalid_recovery_code: "Código de recuperação inválido ou já usado.",
  password_incorrect: "Senha atual incorreta.",
  too_short: "Use pelo menos 12 caracteres.",
  too_common: "Essa senha é muito comum. Escolha outra.",
  same_as_current: "A nova senha precisa ser diferente da atual.",
  already_has_access: "Esta pessoa já tem acesso a este cliente.",
  email_in_use: "Este e-mail já é usado em outra conta do Regista. Use outro e-mail.",
  client_name_taken: "Já existe um cliente com esse nome. Use outro nome.",
  last_admin: "O cliente precisa de pelo menos um Admin do cliente ativo.",
  cannot_change_own_role: "Você não pode mudar o seu próprio papel.",
  cannot_remove_own_access: "Você não pode remover o seu próprio acesso.",
  cannot_reinvite_self: "Você não pode reenviar o convite para você mesmo.",
  user_disabled: "O acesso desta pessoa foi removido. Convide-a de novo para ela voltar.",
  user_not_found: "Essa pessoa não foi encontrada. Atualize a lista e tente de novo.",
  session_not_found: "Essa sessão já foi encerrada.",
  cannot_end_current_session: "Esta é a sua sessão atual. Use Sair, no menu da barra lateral.",
  client_context_required:
    "Escolha um cliente na barra lateral para fazer esta alteração.",
  client_not_found: "Esse cliente não está disponível.",
  machine_name_taken: "Já existe uma máquina com esse nome neste cliente.",
  pool_name_taken: "Já existe um pool com esse nome.",
  invalid_name: "Escreva um nome para o pool.",
  name_mismatch: "O nome não confere.",
  machine_not_found: "Essa máquina não foi encontrada. Atualize a lista e tente de novo.",
  pool_not_found: "Esse pool não foi encontrado. Atualize a lista e tente de novo.",
  machine_revoked: "Esta máquina foi revogada. Cadastre a máquina outra vez para usá-la.",
  forbidden: "Você não tem permissão para fazer isso.",
  validation: "Confira os campos e tente de novo.",
  [NETWORK_ERROR_CODE]:
    "Não foi possível falar com o servidor. Seus robôs continuam rodando; só o painel está sem resposta.",
};

export const FALLBACK_MESSAGE = "Algo deu errado. Tente de novo; se continuar, avise a Artemisys.";

export function messageFor(error: unknown): string {
  if (error instanceof ApiError) return MESSAGES[error.code] ?? FALLBACK_MESSAGE;
  return FALLBACK_MESSAGE;
}

export function isApiError(error: unknown, code: string): boolean {
  return error instanceof ApiError && error.code === code;
}

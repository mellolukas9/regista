// Códigos de recuperação recém-gerados, só em memória: nunca em localStorage, sessionStorage
// ou URL. Recarregar a página os perde (e a tela pede um novo conjunto).

let codes: string[] | null = null;

export function holdRecoveryCodes(value: string[]) {
  codes = value;
}

export function peekRecoveryCodes(): string[] | null {
  return codes;
}

export function clearRecoveryCodes() {
  codes = null;
}

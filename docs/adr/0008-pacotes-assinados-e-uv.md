# ADR 0008: Pacotes de robô assinados e ambientes isolados com uv

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Se o servidor do Regista for comprometido, o atacante não pode conseguir executar código arbitrário nas máquinas dos clientes.

## Decisão

Robôs são distribuídos como pacotes versionados, assinados com Ed25519 por uma chave mantida fora do servidor. O agente embute a chave pública, verifica sha256 e assinatura e recusa pacotes inválidos. Não existe comando de shell no protocolo. Cada versão roda em ambiente próprio criado com `uv`. Allowlist local e kill switch no agente.

## Consequências

Os detalhes (formato do pacote, chaves e rotação, amarração ao cliente, runtime) estão na [ADR 0021](0021-pacotes-assinados-formato-chaves-e-runtime.md).


O servidor só consegue mandar executar versões assinadas. Publicar versão exige o passo de assinatura (CLI `regista-pack`).

## Alternativas descartadas

Executar scripts enviados pelo servidor; confiar apenas no TLS.

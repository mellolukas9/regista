# ADR 0010: Modos do agente: serviço, sessão de usuário e único

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Serviços Windows rodam na sessão 0, sem área de trabalho: Playwright headless funciona, automação de janelas e aplicativos desktop não. Containers precisam de execução efêmera.

## Decisão

Um único agente com três modos: `service` (segundo plano, headless), `session` (inicia no logon de usuário dedicado com login automático, para robôs com tela) e `oneshot` (pega um job e encerra; fora do MVP, mas o código fica preparado).

## Consequências

Cobre estação de atendimento, VM e nuvem com o mesmo binário. O modo `session` exige configuração de login automático e bloqueio de tela na máquina.

## Alternativas descartadas

Agentes diferentes por ambiente.

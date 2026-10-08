# ADR 0010: Modos do agente: serviço, sessão de usuário e único

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Serviços Windows rodam na sessão 0, sem área de trabalho: Playwright headless funciona, automação de janelas e aplicativos desktop não. Containers precisam de execução efêmera.

## Decisão

Um único agente com três modos: `service` (segundo plano, headless), `session` (inicia no logon de usuário dedicado com login automático, para robôs com tela) e `oneshot` (pega um job e encerra; fora do MVP, mas o código fica preparado).

## Emenda (M4b, ADR 0022)

O agente roda **sempre como serviço** (`NT SERVICE\RegistaAgent`, sessão 0) e é o único que acessa a chave da máquina. O robô não roda mais com a conta do agente: quem o inicia é o **hospedeiro do robô** (`regista-agent host`), com identidade própria. Os modos `service` e `session` passam a dizer **onde o hospedeiro roda**:

- `service`: o hospedeiro é o serviço `RegistaRobot` (conta virtual `NT SERVICE\RegistaRobot`), na sessão 0, headless;
- `session`: o hospedeiro é iniciado por uma tarefa agendada "ao fazer logon" do **usuário dedicado** (sem senha guardada: o gatilho é o próprio logon), no desktop dele, para robôs com tela e aplicativos desktop configurados no perfil dele.

O login automático e o bloqueio de tela continuam sendo exigência do modo `session`.

## Consequências

Cobre estação de atendimento, VM e nuvem com o mesmo binário. O modo `session` exige configuração de login automático e bloqueio de tela na máquina.

## Alternativas descartadas

Agentes diferentes por ambiente.

# ADR 0014: Execução no ambiente do cliente primeiro

- **Status:** aceita
- **Data:** 2026-10

## Contexto

Executar robôs na infraestrutura da Artemisys implica guardar dados dos clientes, pagar infraestrutura e não alcançar sistemas internos dos clientes.

## Decisão

No MVP os robôs rodam na máquina física, VM ou nuvem do próprio cliente (EC2 criada por template). Execução interna e containers elásticos só quando houver demanda, reutilizando o mesmo agente.

## Consequências

Menor risco e custo; acesso natural à rede do cliente. Disponibilidade da máquina é responsabilidade do cliente; o Regista monitora e alerta.

## Alternativas descartadas

Execução centralizada na AWS da Artemisys desde o início; acesso cross-account à AWS do cliente (inverte o modelo de segurança).

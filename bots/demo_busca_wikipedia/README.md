# demo_busca_wikipedia

Robô de demonstração do Regista: pesquisa um termo na Wikipédia (em português) e tira uma captura
de tela. É o robô que aparece no painel como **Busca na Wikipédia** (ver
`uv run regista-admin seed-dev`, ou cadastre-o pelo painel, como explica a seção Comandos do
`CLAUDE.md`).

- **Parâmetro:** `term` (texto; padrão "automação de processos"). O painel do M3 dispara sem
  parâmetros.
- **Saída:** linhas de log (os cinco primeiros títulos de resultado) e `resultado.png` (ou
  `erro.png`).
- **Cancelamento:** o agente cria o arquivo `REGISTA_CANCEL_FILE` e pede que o processo pare; o
  robô confere o arquivo entre as etapas.
- **Rede:** só acessa `pt.wikipedia.org`, e se identifica com um agente de usuário próprio.

## Rodar no ambiente de desenvolvimento (antes dos pacotes assinados, M4)

O robô roda com o Python do próprio workspace. O Playwright e o navegador entram uma vez:

```powershell
uv sync --group bots
uv run playwright install chromium
```

Depois, o agente de desenvolvimento (veja a seção Comandos do `CLAUDE.md`) com
`REGISTA_ENVIRONMENT=dev`, `REGISTA_DEV_UNSIGNED=1` e `REGISTA_DEV_BOTS_DIR` apontando para a
pasta `bots` deste repositório executa este robô quando alguém clica em "Executar agora".

Para testar o robô sozinho, sem o agente:

```powershell
$env:REGISTA_ARTIFACTS_DIR = "$env:TEMP\demo-artifacts"
$env:REGISTA_JOB_PARAMS = '{"term": "automação de processos"}'
uv run python bots/demo_busca_wikipedia/main.py
```

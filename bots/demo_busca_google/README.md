# demo_busca_google

Robô de demonstração do Regista: abre o Google, pesquisa um termo e tira uma captura de tela.
É o robô que aparece no painel como **Busca no Google** (ver `uv run regista-admin seed-dev`).

- **Parâmetro:** `term` (texto; padrão "Regista Artemisys"). O painel do M3 dispara sem parâmetros.
- **Saída:** linhas de log (os cinco primeiros resultados) e `resultado.png` (ou `erro.png`).
- **Cancelamento:** o agente cria o arquivo `REGISTA_CANCEL_FILE` e pede que o processo pare; o
  robô confere o arquivo entre as etapas.
- **Risco:** o Google pode pedir uma verificação anti-robô. O robô avisa no log e a captura
  mostra a página que apareceu.

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
$env:REGISTA_JOB_PARAMS = '{"term": "Regista Artemisys"}'
uv run python bots/demo_busca_google/main.py
```

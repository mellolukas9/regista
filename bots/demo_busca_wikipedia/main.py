"""Robô de demonstração: pesquisa um termo na Wikipédia e tira uma captura de tela.

Roda sob o agente do Regista (Playwright, API assíncrona). Parâmetros (JSON em
REGISTA_JOB_PARAMS): `term`, o que pesquisar (padrão "automação de processos").

O que o agente entrega ao robô, por variáveis de ambiente:
- REGISTA_JOB_PARAMS: os parâmetros da execução;
- REGISTA_ARTIFACTS_DIR: pasta onde as capturas de tela devem ser gravadas (o agente envia);
- REGISTA_CANCEL_FILE: se este arquivo existir, a execução foi cancelada: pare e saia.

Cada linha escrita na saída vira uma linha de log no painel. Linhas que começam com INFO, WARNING
ou ERROR mantêm o nível.
"""

import asyncio
import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

from playwright.async_api import Page, async_playwright

DEFAULT_TERM = "automação de processos"
SEARCH_URL = (
    "https://pt.wikipedia.org/w/index.php?title=Especial:Pesquisar&fulltext=1&ns0=1&search={term}"
)
USER_AGENT = "RegistaDemo/1.0 (robô de demonstração da Artemisys)"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("demo_busca_wikipedia")

ARTIFACTS = Path(os.environ.get("REGISTA_ARTIFACTS_DIR", "."))
CANCEL_FILE = (
    Path(os.environ["REGISTA_CANCEL_FILE"]) if "REGISTA_CANCEL_FILE" in os.environ else None
)


def cancelled() -> bool:
    return CANCEL_FILE is not None and CANCEL_FILE.exists()


async def search(page: Page, term: str) -> None:
    log.info("Pesquisando na Wikipédia: %s", term)
    await page.goto(SEARCH_URL.format(term=quote_plus(term)), wait_until="domcontentloaded")
    if cancelled():
        return
    titles = await page.locator(".mw-search-result-heading a").all_inner_texts()
    for position, title in enumerate(titles[:5], start=1):
        log.info("Resultado %d: %s", position, title)
    if not titles:
        log.warning("Nenhum resultado encontrado para esse termo.")


async def main() -> int:
    term = str(json.loads(os.environ.get("REGISTA_JOB_PARAMS") or "{}").get("term") or DEFAULT_TERM)
    await asyncio.to_thread(ARTIFACTS.mkdir, parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="pt-BR", viewport={"width": 1280, "height": 800}, user_agent=USER_AGENT
        )
        page = await context.new_page()
        try:
            await search(page, term)
            if cancelled():
                log.warning("Cancelamento visto; encerrando antes da captura.")
                return 0
            await page.screenshot(path=str(ARTIFACTS / "resultado.png"))
            log.info("Captura de tela gravada")
            return 0
        except Exception as exc:
            log.error("A pesquisa falhou: %s", exc)
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(ARTIFACTS / "erro.png"))
            return 1
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

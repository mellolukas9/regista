"""Robô de demonstração: pesquisa um termo no Google e tira uma captura de tela.

Roda sob o agente do Regista (Playwright, API assíncrona). Parâmetros (JSON em
REGISTA_JOB_PARAMS): `term`, o que pesquisar (padrão "Regista Artemisys").

O que o agente entrega ao robô, por variáveis de ambiente:
- REGISTA_JOB_PARAMS: os parâmetros da execução;
- REGISTA_ARTIFACTS_DIR: pasta onde as capturas de tela devem ser gravadas (o agente envia);
- REGISTA_CANCEL_FILE: se este arquivo existir, a execução foi cancelada: pare e saia.

Cada linha escrita na saída vira uma linha de log no painel. Linhas que começam com INFO, WARNING
ou ERROR mantêm o nível.

Risco conhecido: o Google pode mostrar uma verificação anti-robô. A captura mostra o que apareceu.
"""

import asyncio
import contextlib
import json
import logging
import os
import sys
from pathlib import Path

from playwright.async_api import Page, async_playwright

DEFAULT_TERM = "Regista Artemisys"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("demo_busca_google")

ARTIFACTS = Path(os.environ.get("REGISTA_ARTIFACTS_DIR", "."))
CANCEL_FILE = (
    Path(os.environ["REGISTA_CANCEL_FILE"]) if "REGISTA_CANCEL_FILE" in os.environ else None
)
CONSENT_BUTTONS = ("Aceitar tudo", "Rejeitar tudo", "Accept all", "Reject all")


def cancelled() -> bool:
    return CANCEL_FILE is not None and CANCEL_FILE.exists()


async def accept_consent(page: Page) -> None:
    """The consent notice appears for some visitors. Whatever shows up, close it."""
    for label in CONSENT_BUTTONS:
        button = page.get_by_role("button", name=label)
        if await button.count():
            log.info("Fechando o aviso de cookies (%s)", label)
            await button.first.click()
            return


async def search(page: Page, term: str) -> None:
    log.info("Abrindo o Google")
    await page.goto("https://www.google.com/?hl=pt-BR", wait_until="domcontentloaded")
    await accept_consent(page)
    if cancelled():
        return
    log.info("Pesquisando: %s", term)
    box = page.locator("textarea[name=q], input[name=q]").first
    await box.fill(term)
    await box.press("Enter")
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_selector("#search, #rso, #captcha-form", timeout=15_000)
    if await page.locator("#captcha-form").count():
        log.warning("O Google pediu uma verificação anti-robô; a captura mostra a página.")
        return
    titles = await page.locator("#search h3").all_inner_texts()
    for position, title in enumerate(titles[:5], start=1):
        log.info("Resultado %d: %s", position, title)
    if not titles:
        log.warning("Nenhum resultado encontrado na página.")


async def main() -> int:
    term = str(json.loads(os.environ.get("REGISTA_JOB_PARAMS") or "{}").get("term") or DEFAULT_TERM)
    await asyncio.to_thread(ARTIFACTS.mkdir, parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(locale="pt-BR", viewport={"width": 1280, "height": 800})
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

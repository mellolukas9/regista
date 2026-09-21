"""
Regista — Bot de demonstracao: abre o Google no Chrome e faz uma pesquisa.

Uso local (sem Prefect):
    python bot_busca_google.py

Uso via Prefect:
    python register_deployment.py bot_busca_google.py:busca_google \
        --name "Busca no Google" \
        --queue queue-local-test
"""

import os
from prefect import flow, get_run_logger
from playwright.sync_api import sync_playwright, Page

TERMO_PADRAO = "Regista RPA automacao"
SLOW_MO_MS = 400  # milissegundos entre acoes, para dar pra acompanhar na tela


# Nao decorar com @task: o Prefect executa tasks em outra thread, e a API
# sincrona do Playwright fica presa a thread onde o browser foi lancado.
def abrir_google(page: Page, logger) -> None:
    logger.info("Abrindo o Google")
    page.goto("https://www.google.com")
    page.wait_for_load_state("networkidle")

    for botao in ["Aceitar tudo", "Accept all", "I agree", "Aceito"]:
        try:
            page.get_by_role("button", name=botao).click(timeout=2000)
            logger.info("Cookie banner fechado (%s)", botao)
            break
        except Exception:
            continue


def pesquisar(page: Page, termo: str, logger) -> None:
    logger.info("Pesquisando por: %s", termo)

    caixa = page.locator("textarea[name='q'], input[name='q']").first
    caixa.click()
    caixa.type(termo, delay=120)  # digita devagar, letra por letra
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    logger.info("Resultados carregados")


@flow(name="Busca no Google", log_prints=True)
def busca_google(termo: str = "") -> dict:
    logger = get_run_logger()
    termo = termo or os.getenv("BOT_TERMO_BUSCA", TERMO_PADRAO)

    logger.info("Iniciando Bot de Busca no Google")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, slow_mo=SLOW_MO_MS)
        context = browser.new_context(viewport={"width": 1366, "height": 768}, locale="pt-BR")
        page = context.new_page()
        page.set_default_timeout(30_000)

        try:
            abrir_google(page, logger)
            pesquisar(page, termo, logger)
            page.wait_for_timeout(3000)  # deixa o resultado na tela por um tempo
            return {"termo_pesquisado": termo}
        finally:
            browser.close()


if __name__ == "__main__":
    resultado = busca_google()
    print("Resultado:", resultado)

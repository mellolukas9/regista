"""
Regista — Template base para bots Python/Playwright via Prefect

Como usar:
1. Copie este arquivo para o nome do seu bot (ex: bot_relatorio_vendas.py)
2. Preencha as variáveis de configuração no topo
3. Implemente a lógica dentro de `executar_bot()`
4. Registre o deployment no Prefect:
       prefect deployment build bot_relatorio_vendas.py:meu_bot \
           --name "Relatorio de Vendas" \
           --work-queue queue-rdp-01 \
           --apply
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime

from prefect import flow, task, get_run_logger
from prefect.context import get_run_context
from playwright.sync_api import sync_playwright, Page, Browser

# ---------------------------------------------------------------------------
# Configuração do bot — ajuste aqui
# ---------------------------------------------------------------------------
BOT_NAME        = "Nome do Bot"
BOT_VERSION     = "1.0.0"
TARGET_URL      = "https://exemplo.com"
HEADLESS        = False       # False = abre janela (necessário em RDP com sessão ativa)
SLOW_MO_MS      = 50          # milissegundos entre ações (0 para máxima velocidade)
TIMEOUT_MS      = 30_000      # timeout padrão para seletores (30s)
SCREENSHOT_DIR  = Path("C:/regista-worker/screenshots")


# ---------------------------------------------------------------------------
# Tasks Prefect — cada etapa lógica vira uma @task separada
# ---------------------------------------------------------------------------

@task(name="Inicializar browser", retries=2, retry_delay_seconds=10)
def inicializar_browser(playwright) -> tuple[Browser, Page]:
    logger = get_run_logger()
    logger.info("Iniciando browser Chromium (headless=%s)", HEADLESS)

    browser = playwright.chromium.launch(
        headless=HEADLESS,
        slow_mo=SLOW_MO_MS,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        viewport={"width": 1366, "height": 768},
        locale="pt-BR",
    )
    page = context.new_page()
    page.set_default_timeout(TIMEOUT_MS)
    return browser, page


@task(name="Fazer login")
def fazer_login(page: Page, usuario: str, senha: str) -> None:
    logger = get_run_logger()
    logger.info("Navegando para %s", TARGET_URL)

    page.goto(TARGET_URL)
    page.wait_for_load_state("networkidle")

    # TODO: ajuste os seletores para o sistema alvo
    page.fill("#username", usuario)
    page.fill("#password", senha)
    page.click("button[type='submit']")
    page.wait_for_load_state("networkidle")

    logger.info("Login realizado com sucesso")


@task(name="Executar automacao principal")
def executar_automacao(page: Page) -> dict:
    """
    Implemente aqui a lógica principal do bot.
    Retorne um dict com os resultados/métricas para o relatório.
    """
    logger = get_run_logger()
    logger.info("Executando automação principal")

    # TODO: implemente a lógica do bot aqui
    resultado = {
        "registros_processados": 0,
        "erros": 0,
        "observacoes": "",
    }

    # Exemplo: extrair dados de uma tabela
    # linhas = page.query_selector_all("table tbody tr")
    # for linha in linhas:
    #     ...

    logger.info("Automação concluída: %s", resultado)
    return resultado


@task(name="Tirar screenshot de evidencia")
def tirar_screenshot(page: Page, nome: str) -> Path:
    logger = get_run_logger()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = SCREENSHOT_DIR / f"{nome}_{timestamp}.png"
    page.screenshot(path=str(caminho), full_page=True)

    logger.info("Screenshot salvo em %s", caminho)
    return caminho


@task(name="Fechar browser")
def fechar_browser(browser: Browser) -> None:
    browser.close()
    get_run_logger().info("Browser encerrado")


# ---------------------------------------------------------------------------
# Flow principal — ponto de entrada do Prefect
# ---------------------------------------------------------------------------

@flow(
    name=BOT_NAME,
    version=BOT_VERSION,
    retries=3,
    retry_delay_seconds=60,
    log_prints=True,
)
def meu_bot(
    usuario: str = "",
    senha: str = "",
) -> dict:
    """
    Flow principal do bot. Parâmetros podem ser passados via Prefect UI,
    API ou definidos como variáveis de ambiente.
    """
    logger = get_run_logger()

    # Credenciais: parâmetro > variável de ambiente > erro
    usuario = usuario or os.getenv("BOT_USUARIO", "")
    senha   = senha   or os.getenv("BOT_SENHA", "")

    if not usuario or not senha:
        raise ValueError(
            "Credenciais não fornecidas. "
            "Passe como parâmetro ou defina BOT_USUARIO e BOT_SENHA no ambiente."
        )

    logger.info("Iniciando %s v%s", BOT_NAME, BOT_VERSION)

    with sync_playwright() as playwright:
        browser, page = inicializar_browser(playwright)

        try:
            fazer_login(page, usuario, senha)
            resultado = executar_automacao(page)
            tirar_screenshot(page, "conclusao")
            return resultado

        except Exception as exc:
            # Screenshot de erro para diagnóstico
            try:
                tirar_screenshot(page, "ERRO")
            except Exception:
                pass
            logger.error("Bot falhou: %s", exc)
            raise

        finally:
            fechar_browser(browser)


# ---------------------------------------------------------------------------
# Execução local direta (para testes sem Prefect)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    resultado = meu_bot(
        usuario=os.getenv("BOT_USUARIO", "admin"),
        senha=os.getenv("BOT_SENHA", "senha"),
    )
    print("Resultado:", resultado)

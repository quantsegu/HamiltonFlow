"""Browser test of the Builder UI. Skipped unless Playwright and Chromium are available."""

import glob
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
CHROME = next(iter(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")), None)
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chromium found")
DATA = Path(__file__).parents[1] / "data" / "csv"


@pytest.fixture
def server(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "hamilton_flow.cli", "serve", "--project", str(tmp_path / "p"), "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
            break
        except OSError:
            time.sleep(0.25)
    yield f"http://127.0.0.1:{port}"
    proc.terminate()


def test_build_model_and_stack_metrics(server):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page, errors = browser.new_page(), []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server)
        page.wait_for_selector("#view-data:not([hidden])")
        for name in ("orders", "customers"):
            page.fill("#tname", name)
            page.set_input_files("#file", str(DATA / f"{name}.csv"))
            page.wait_for_selector(f"text=Loaded main.{name}")
        page.click('[data-act=model][data-t="main.orders"]')
        page.click('[data-act=model][data-t="main.customers"]')
        page.click("[data-view=metrics]")
        n = page.locator("#view-metrics .card h3 .badge:text-is('simple')").count()
        page.click("[data-act=newmetric][data-type=ratio]")
        page.fill(f'[data-p="metrics.{n}.name"]', "avg_order_value")
        page.select_option(f'[data-p="metrics.{n}.numerator"]', "amount")
        page.select_option(f'[data-p="metrics.{n}.denominator"]', "orders_count")
        page.click("[data-act=newmetric][data-type=derived]")
        page.fill(f'[data-p="metrics.{n + 1}.name"]', "aov_net")
        page.fill(f'[data-p="metrics.{n + 1}.expr"]', "avg_order_value * 0.9")
        page.select_option(f'[data-p="metrics.{n + 1}.inputs.0.name"]', "avg_order_value")
        page.wait_for_function("document.querySelector('#bar-status').innerText.includes('valid')")
        page.click("#save")
        page.wait_for_function("document.querySelector('#bar-status').innerText.includes('Saved')")
        page.click("[data-view=explore]")
        page.check("[data-m=aov_net]")
        page.wait_for_selector("[data-d$=country]")
        page.check("[data-d$=country]")
        page.click("#run")
        page.wait_for_selector("table")
        text = page.inner_text("table")
        assert "45" in text and "180" in text and not errors
        browser.close()

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


def kpi(page):
    return page.inner_text("#pvbody .kpi")


def test_compose_metrics_with_wizard_and_stack_them(server):
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
        page.click('[data-act=model][data-t="main.customers"]')  # joins orders to customers via customer_id
        page.click("[data-view=metrics]")
        page.wait_for_selector(".tiles, .mitem")

        # 1. aggregate a column, with a filter built from dropdowns
        page.click("[data-act=wiz-open][data-kind=agg]")
        page.select_option("[data-w=table]", "main.orders")
        page.select_option("[data-w=column]", "amount")
        page.fill("[data-w=name]", "paid_revenue")
        page.click("[data-fadd=wf]")
        page.wait_for_selector('[data-f=wf][data-k=dimension] option[value="order__status"]', state="attached")
        page.select_option("[data-f=wf][data-k=dimension]", "order__status")
        page.fill("[data-f=wf][data-k=value]", "cancelled")
        page.click("[data-act=wiz-create]")
        page.wait_for_selector("#pvbody .kpi")
        page.wait_for_function("document.querySelector('#pvbody .kpi').innerText==='0'")  # cancelled order amount is 0

        # 2. count rows, 3. ratio, 4. formula on the ratio
        page.click("[data-act=wiz-open]")
        page.click("[data-act=wiz-kind][data-kind=count]")
        page.select_option("[data-w=table]", "main.orders")
        page.fill("[data-w=name]", "order_total")
        page.click("[data-act=wiz-create]")
        page.wait_for_function("document.querySelector('#pvbody .kpi')?.innerText==='4'")

        page.click("[data-act=wiz-open]")
        page.click("[data-act=wiz-kind][data-kind=agg]")
        page.select_option("[data-w=table]", "main.orders")
        page.select_option("[data-w=column]", "amount")
        page.fill("[data-w=name]", "revenue")
        page.click("[data-act=wiz-create]")
        page.wait_for_function("document.querySelector('#pvbody .kpi')?.innerText==='350'")

        page.click("[data-act=wiz-open]")
        page.click("[data-act=wiz-kind][data-kind=ratio]")
        page.select_option("[data-w=numerator]", "revenue")
        page.select_option("[data-w=denominator]", "order_total")
        page.fill("[data-w=name]", "aov")
        page.click("[data-act=wiz-create]")
        page.wait_for_function("document.querySelector('#pvbody .kpi')?.innerText.startsWith('87.5')")

        page.click("[data-act=wiz-open]")
        page.click("[data-act=wiz-kind][data-kind=formula]")
        page.click("[data-ins=fx-w][data-v=aov]")
        page.click("[data-ins=fx-w][data-v='*']")
        page.type("#fx-w", " 0.9")
        page.fill("[data-w=name]", "aov_net")
        page.click("[data-act=wiz-create]")
        page.wait_for_function("document.querySelector('#pvbody .kpi')?.innerText==='78.75'")

        # 5. growth needs a time grouping; the preview picks one itself
        page.click("[data-act=wiz-open]")
        page.click("[data-act=wiz-kind][data-kind=growth]")
        page.select_option("[data-w=base]", "order_total")
        page.select_option("[data-w=period]", "day")
        page.fill("[data-w=name]", "order_growth")
        page.click("[data-act=wiz-create]")
        page.wait_for_selector("#pvbody table")
        assert "-0.5" in page.inner_text("#pvbody table")

        # invalid names are rejected before saving
        page.fill("#mname", "Bad Name")
        page.dispatch_event("#mname", "change")
        assert "lowercase" in page.inner_text("#mname-err")

        page.click("#save")
        page.wait_for_function("document.querySelector('#bar-status').innerText.includes('Saved')")
        page.click("[data-view=explore]")
        page.check("[data-m=aov_net]")
        page.wait_for_selector("[data-d$=country]")
        page.check("[data-d$=country]")
        page.click("#run")
        page.wait_for_selector("table")
        text = page.inner_text("table")
        assert "45" in text and "180" in text and not [e for e in errors if "favicon" not in e]
        browser.close()

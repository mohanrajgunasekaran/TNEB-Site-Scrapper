"""
TNEB Consumer Billing Scraper
Extracts billing details from https://www.tnebnet.org/qwp/qpay
Manual CAPTCHA entry required per iteration.
"""

import time
import re
import logging
import argparse
from datetime import datetime
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException
)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

# ─────────────────────── logging ────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("tneb_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────── constants ──────────────────────
BASE_URL      = "https://www.tnebnet.org/qwp/qpay?login_error=1"
WAIT_TIMEOUT  = 20          # seconds for explicit waits
CAPTCHA_WAIT  = 120         # max seconds to wait for user CAPTCHA input
MAX_RETRIES   = 2
COLUMNS       = [
    "Consumer No",
    "Consumer Name",
    "Consumer Address",
    "Bill Amount (Rs)",
    "Due Date",
    "Info",
    "Status",
]

# ─────────────────────── driver setup ───────────────────
def build_driver(headless: bool = False, chromedriver_path: Optional[str] = None) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--window-size=1280,900")

    svc = Service(chromedriver_path) if chromedriver_path else Service()
    driver = webdriver.Chrome(service=svc, options=opts)
    driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return driver


# ─────────────────────── selectors (resilient) ──────────
# Ordered by reliability: ID → name → CSS → XPath fallback
def _find_element_multi(driver, strategies: list, timeout: int = WAIT_TIMEOUT):
    wait = WebDriverWait(driver, timeout)
    for by, value in strategies:
        try:
            el = wait.until(EC.presence_of_element_located((by, value)))
            if el.is_displayed():
                return el
        except TimeoutException:
            continue
    raise NoSuchElementException(f"Element not found with any strategy: {strategies}")


CONSUMER_INPUT_STRATEGIES = [
    (By.ID,   "consumerNo"),
    (By.NAME, "consumerNo"),
    (By.CSS_SELECTOR, "input[placeholder*='Consumer']"),
    (By.CSS_SELECTOR, "input[placeholder*='consumer']"),
    (By.XPATH, "//input[contains(@id,'consumer') or contains(@name,'consumer')]"),
    (By.XPATH, "//input[@type='text'][1]"),
]

CAPTCHA_INPUT_STRATEGIES = [
    (By.ID,   "captcha"),
    (By.NAME, "captcha"),
    (By.CSS_SELECTOR, "input[placeholder*='aptcha']"),
    (By.CSS_SELECTOR, "input[placeholder*='APTCHA']"),
    (By.XPATH, "//input[contains(@id,'captcha') or contains(@name,'captcha') or contains(@placeholder,'aptcha')]"),
    (By.XPATH, "//input[@type='text'][2]"),
]

SUBMIT_STRATEGIES = [
    (By.CSS_SELECTOR, "button[type='submit']"),
    (By.CSS_SELECTOR, "input[type='submit']"),
    (By.XPATH, "//button[@type='submit' or contains(text(),'Submit') or contains(text(),'Pay')]"),
    (By.XPATH, "//input[@type='submit']"),
]

RESULT_CONTAINER_STRATEGIES = [
    (By.CSS_SELECTOR, ".bill-details, .consumer-details, #billDetails, #consumerInfo"),
    (By.XPATH, "//*[contains(@class,'bill') or contains(@class,'consumer') or contains(@id,'bill')]"),
    (By.XPATH, "//table[.//th or .//td]"),
    (By.TAG_NAME, "table"),
]


# ─────────────────────── form interaction ───────────────
def navigate_to_form(driver: webdriver.Chrome):
    driver.get(BASE_URL)
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "form"))
    )
    log.info("Form page loaded.")


def enter_consumer_number(driver: webdriver.Chrome, consumer_no: str):
    field = _find_element_multi(driver, CONSUMER_INPUT_STRATEGIES)
    field.clear()
    field.send_keys(consumer_no)
    log.info(f"Entered consumer number: {consumer_no}")


def wait_for_captcha_and_submit(driver: webdriver.Chrome, consumer_no: str):
    """
    Prompts user to type CAPTCHA in terminal, then injects it into the page
    and submits. Falls back to asking user to click Submit manually.
    """
    captcha_value = input(
        f"\n[MANUAL] Enter CAPTCHA shown on browser for Consumer {consumer_no}: "
    ).strip()

    captcha_field = _find_element_multi(driver, CAPTCHA_INPUT_STRATEGIES)
    captcha_field.clear()
    captcha_field.send_keys(captcha_value)
    log.info("CAPTCHA entered.")

    submit_btn = _find_element_multi(driver, SUBMIT_STRATEGIES)
    submit_btn.click()
    log.info("Form submitted.")


# ─────────────────────── data extraction ────────────────
def _text(el) -> str:
    return el.get_attribute("textContent").strip() if el else ""


def _extract_by_label(driver: webdriver.Chrome, label_patterns: list[str]) -> str:
    """
    Searches the page for a label matching any pattern and returns the
    adjacent cell value. Tries <td> pairs, <th>/<td>, and definition lists.
    """
    page_source = driver.page_source

    # Strategy 1: table row label–value pairs
    for pattern in label_patterns:
        xpaths = [
            f"//td[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{pattern.upper()}')]/following-sibling::td[1]",
            f"//th[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{pattern.upper()}')]/following-sibling::td[1]",
            f"//dt[contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{pattern.upper()}')]/following-sibling::dd[1]",
            f"//*[contains(@class,'label') and contains(translate(text(),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'{pattern.upper()}')]/../*[contains(@class,'value')]",
        ]
        for xp in xpaths:
            try:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    val = _text(el)
                    if val:
                        return val
            except Exception:
                continue

    # Strategy 2: regex fallback on raw page source
    for pattern in label_patterns:
        regex = re.compile(
            rf"{re.escape(pattern)}\s*[:\-]?\s*([^\n<>{{}}]+)",
            re.IGNORECASE
        )
        match = regex.search(page_source)
        if match:
            return match.group(1).strip()

    return ""


def extract_billing_data(driver: webdriver.Chrome) -> dict:
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.any_of(
            EC.presence_of_element_located((By.XPATH, "//table[.//td]")),
            EC.presence_of_element_located((By.CSS_SELECTOR, ".error, .alert, .message, #errorMsg")),
        )
    )
    time.sleep(0.8)  # brief settle

    page_text = driver.find_element(By.TAG_NAME, "body").get_attribute("textContent")

    # Detect error / informational states
    info = _detect_info_message(driver, page_text)

    name    = _extract_by_label(driver, ["Consumer Name", "Name", "CONSUMER NAME"])
    address = _extract_by_label(driver, ["Address", "Consumer Address", "SERVICE ADDRESS"])
    amount  = _extract_by_label(driver, ["Amount", "Bill Amount", "NET AMOUNT", "AMOUNT PAYABLE", "Total Amount"])
    due     = _extract_by_label(driver, ["Due Date", "Last Date", "DUE DATE", "PAYMENT DUE"])

    # Clean amount: keep only digits, commas, dots
    if amount:
        amount = re.sub(r"[^\d.,]", "", amount).strip().strip(",")

    return {
        "Consumer Name":     name,
        "Consumer Address":  address,
        "Bill Amount (Rs)":  amount,
        "Due Date":          due,
        "Info":              info,
    }


def _detect_info_message(driver: webdriver.Chrome, page_text: str) -> str:
    keywords = [
        "NO PENDING BILL",
        "NO DUE",
        "INVALID CONSUMER",
        "NOT FOUND",
        "WRONG CAPTCHA",
        "SESSION EXPIRED",
        "ERROR",
    ]
    upper_text = page_text.upper()
    for kw in keywords:
        if kw in upper_text:
            return kw

    # Check for explicit error elements
    for sel in [".error", ".alert-danger", "#errorMsg", ".error-message"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            msg = _text(el)
            if msg:
                return msg[:200]
        except NoSuchElementException:
            continue

    return ""


# ─────────────────────── validation ─────────────────────
def validate_record(record: dict, name_filters: list[str]) -> dict:
    if not name_filters:
        record["Status"] = "OK"
        return record

    consumer_name = record.get("Consumer Name", "").lower()
    matched = any(kw.lower() in consumer_name for kw in name_filters)
    record["Status"] = "OK" if matched else "NAME_MISMATCH"
    return record


# ─────────────────────── navigation ─────────────────────
def go_back_to_form(driver: webdriver.Chrome):
    try:
        driver.back()
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.TAG_NAME, "form"))
        )
        log.info("Navigated back to form.")
    except TimeoutException:
        log.warning("Back navigation timed out; reloading base URL.")
        navigate_to_form(driver)


# ─────────────────────── export ─────────────────────────
def export_to_excel(records: list[dict], filepath: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "TNEB Billing Data"

    header_fill = PatternFill("solid", start_color="1F4E79")
    header_font = Font(bold=True, color="FFFFFF", name="Arial", size=11)
    alt_fill    = PatternFill("solid", start_color="D6E4F0")
    mis_fill    = PatternFill("solid", start_color="FFD7D7")

    col_widths = {
        "Consumer No":      18,
        "Consumer Name":    30,
        "Consumer Address": 45,
        "Bill Amount (Rs)": 18,
        "Due Date":         16,
        "Info":             30,
        "Status":           16,
    }

    # Header row
    for col_idx, col_name in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[cell.column_letter].width = col_widths.get(col_name, 20)
    ws.row_dimensions[1].height = 22

    # Data rows
    for row_idx, rec in enumerate(records, 2):
        is_mismatch = rec.get("Status") == "NAME_MISMATCH"
        row_fill    = mis_fill if is_mismatch else (alt_fill if row_idx % 2 == 0 else None)
        for col_idx, col_name in enumerate(COLUMNS, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=rec.get(col_name, ""))
            cell.font      = Font(name="Arial", size=10)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if row_fill:
                cell.fill = row_fill

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    wb.save(filepath)
    log.info(f"Results exported → {filepath}")


# ─────────────────────── main scrape loop ───────────────
def scrape(
    start: int,
    end: int,
    zero_pad: int         = 0,
    name_filters: list    = None,
    headless: bool        = False,
    delay: float          = 1.5,
    chromedriver_path: Optional[str] = None,
    output_path: Optional[str]       = None,
):
    name_filters = name_filters or []
    records: list[dict] = []

    if not output_path:
        ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"tneb_billing_{ts}.xlsx"

    driver = build_driver(headless=headless, chromedriver_path=chromedriver_path)

    try:
        navigate_to_form(driver)

        for num in range(start, end + 1):
            consumer_no = str(num).zfill(zero_pad) if zero_pad else str(num)
            log.info(f"── Processing consumer: {consumer_no} ──")

            record = {col: "" for col in COLUMNS}
            record["Consumer No"] = consumer_no
            success = False

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    enter_consumer_number(driver, consumer_no)
                    wait_for_captcha_and_submit(driver, consumer_no)

                    data = extract_billing_data(driver)
                    record.update(data)
                    record = validate_record(record, name_filters)

                    log.info(
                        f"  Name={record['Consumer Name']!r} | "
                        f"Amount={record['Bill Amount (Rs)']!r} | "
                        f"Due={record['Due Date']!r} | "
                        f"Info={record['Info']!r} | "
                        f"Status={record['Status']}"
                    )
                    success = True
                    break

                except (TimeoutException, NoSuchElementException, WebDriverException) as exc:
                    log.warning(f"  Attempt {attempt}/{MAX_RETRIES} failed: {exc}")
                    if attempt < MAX_RETRIES:
                        log.info("  Retrying from form…")
                        go_back_to_form(driver)
                        time.sleep(1)

                except KeyboardInterrupt:
                    log.warning("Interrupted by user — saving collected data.")
                    records.append(record)
                    export_to_excel(records, output_path)
                    return

            if not success:
                record["Info"]   = "SCRAPE_FAILED"
                record["Status"] = "ERROR"
                log.error(f"  Skipped {consumer_no} after {MAX_RETRIES} attempts.")

            records.append(record)

            # Periodic save every 10 records
            if len(records) % 10 == 0:
                export_to_excel(records, output_path)
                log.info(f"  Auto-saved {len(records)} records.")

            go_back_to_form(driver)
            time.sleep(delay)

    finally:
        export_to_excel(records, output_path)
        driver.quit()
        log.info(f"Done. Total records: {len(records)}")

    return records


# ─────────────────────── CLI ────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="TNEB Consumer Billing Scraper (manual CAPTCHA)"
    )
    p.add_argument("--start",    type=int, required=True,  help="Start consumer number")
    p.add_argument("--end",      type=int, required=True,  help="End consumer number (inclusive)")
    p.add_argument("--zeropad",  type=int, default=0,      help="Zero-pad width (0 = disabled)")
    p.add_argument("--names",    nargs="*", default=[],    help="Name filter keywords (partial match)")
    p.add_argument("--headless", action="store_true",      help="Run Chrome in headless mode")
    p.add_argument("--delay",    type=float, default=1.5,  help="Delay between requests (seconds)")
    p.add_argument("--driver",   default=None,             help="Path to chromedriver binary")
    p.add_argument("--output",   default=None,             help="Output Excel file path")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    scrape(
        start             = args.start,
        end               = args.end,
        zero_pad          = args.zeropad,
        name_filters      = args.names,
        headless          = args.headless,
        delay             = args.delay,
        chromedriver_path = args.driver,
        output_path       = args.output,
    )

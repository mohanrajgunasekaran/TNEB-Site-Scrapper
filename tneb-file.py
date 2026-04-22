#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    InvalidSessionIdException,
    NoSuchElementException,
    SessionNotCreatedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver import Chrome
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait


DEFAULT_URL = "https://www.tnebnet.org/qwp/qpay?login_error=1"


@dataclass
class ResultRow:
    consumer_no: str
    consumer_name: str = ""
    consumer_address: str = ""
    bill_amount_rs: str = ""
    due_date: str = ""
    info: str = ""


class TNEBScraper:
    def __init__(
        self,
        driver: Chrome,
        wait_timeout: int = 20,
        delay_seconds: float = 1.5,
        max_retries: int = 1,
        name_keywords: Optional[Sequence[str]] = None,
    ) -> None:
        self.driver = driver
        self.wait = WebDriverWait(
            driver,
            wait_timeout,
            poll_frequency=0.4,
            ignored_exceptions=(StaleElementReferenceException,),
        )
        self.delay_seconds = delay_seconds
        self.max_retries = max_retries
        self.name_keywords = [k.strip().lower() for k in (name_keywords or []) if k.strip()]
        self.cached_captcha: Optional[str] = None

    def open_portal(self, url: str) -> None:
        self.driver.get(url)
        self.wait_for_form_ready()

    def wait_for_form_ready(self) -> None:
        self.wait.until(lambda d: len(d.find_elements(By.TAG_NAME, "body")) > 0)
        self.wait.until(lambda d: self.find_consumer_input() is not None)
        self.wait.until(lambda d: self.find_submit_button() is not None)

    def find_consumer_input(self) -> Optional[WebElement]:
        candidates = [
            (By.CSS_SELECTOR, "input[name*='cons' i]"),
            (By.CSS_SELECTOR, "input[id*='cons' i]"),
            (By.CSS_SELECTOR, "input[name*='service' i]"),
            (By.CSS_SELECTOR, "input[id*='service' i]"),
            (By.CSS_SELECTOR, "input[name*='ack' i]"),
            (By.CSS_SELECTOR, "input[id*='ack' i]"),
        ]
        for by, sel in candidates:
            for elem in self.driver.find_elements(by, sel):
                if elem.is_displayed() and elem.is_enabled():
                    return elem

        text_inputs = self.driver.find_elements(
            By.XPATH,
            "//input[@type='text' or @type='tel' or @type='number' or not(@type)]",
        )
        visible = [e for e in text_inputs if e.is_displayed() and e.is_enabled()]
        return visible[0] if visible else None

    def find_captcha_input(self) -> Optional[WebElement]:
        candidates = [
            (By.CSS_SELECTOR, "input[name*='captcha' i]"),
            (By.CSS_SELECTOR, "input[id*='captcha' i]"),
            (By.XPATH, "//label[contains(translate(., 'CAPTCHA', 'captcha'), 'captcha')]/following::input[1]"),
            (By.XPATH, "//input[@type='text' or @type='tel'][position() <= 5]"),
        ]
        consumer_input = self.find_consumer_input()
        for by, sel in candidates:
            for elem in self.driver.find_elements(by, sel):
                if elem.is_displayed() and elem.is_enabled():
                    if consumer_input is not None and elem == consumer_input:
                        continue
                    return elem
        return None

    def find_submit_button(self) -> Optional[WebElement]:
        candidates = [
            (By.XPATH, "//button[normalize-space()='Submit']"),
            (By.XPATH, "//input[@type='submit']"),
            (By.XPATH, "//button[contains(translate(., 'SUBMIT', 'submit'), 'submit')]"),
            (By.XPATH, "//a[contains(translate(., 'SUBMIT', 'submit'), 'submit')]"),
        ]
        for by, sel in candidates:
            for elem in self.driver.find_elements(by, sel):
                if elem.is_displayed() and elem.is_enabled():
                    return elem
        return None

    def set_consumer_number(self, consumer_no: str) -> None:
        inp = self.find_consumer_input()
        if inp is None:
            raise NoSuchElementException("Consumer number input not found")
        try:
            inp.click()
        except WebDriverException:
            pass
        inp.send_keys(Keys.CONTROL, "a")
        inp.send_keys(Keys.DELETE)
        inp.send_keys(consumer_no)

    def prompt_manual_captcha(self, reuse_cached: bool = True) -> str:
        captcha_input = self.find_captcha_input()

        if reuse_cached and self.cached_captcha:
            if captcha_input is not None:
                captcha_input.click()
                captcha_input.send_keys(Keys.CONTROL, "a")
                captcha_input.send_keys(Keys.DELETE)
                captcha_input.send_keys(self.cached_captcha)
            return self.cached_captcha

        entered = input("\nEnter CAPTCHA shown in browser: ").strip()
        self.cached_captcha = entered

        if captcha_input is not None:
            captcha_input.click()
            captcha_input.send_keys(Keys.CONTROL, "a")
            captcha_input.send_keys(Keys.DELETE)
            captcha_input.send_keys(entered)

        return entered

    def clear_cached_captcha(self) -> None:
        self.cached_captcha = None

    def submit_form(self) -> None:
        button = self.find_submit_button()
        if button is None:
            raise NoSuchElementException("Submit button not found")
        try:
            button.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", button)

    def wait_for_result_transition(self) -> None:
        def changed(d: Chrome) -> bool:
            body = d.find_element(By.TAG_NAME, "body").text.lower()
            markers = [
                "consumer name",
                "bill amount",
                "due date",
                "no pending bill",
                "invalid",
                "not found",
                "status",
                "captcha",
            ]
            return any(marker in body for marker in markers)

        self.wait.until(changed)

    def captcha_rejected(self, body_text: str) -> bool:
        upper = " ".join(body_text.split()).upper()
        rejection_markers = [
            "INVALID CAPTCHA",
            "ENTER VALID CAPTCHA",
            "CAPTCHA",
            "WRONG CAPTCHA",
            "CHECK CAPTCHA",
        ]
        return any(marker in upper for marker in rejection_markers) and not any(
            ok in upper for ok in ["NO PENDING BILL", "CONSUMER NAME", "BILL AMOUNT", "DUE DATE"]
        )

    def extract_details(self, consumer_no: str) -> ResultRow:
        body_text = self.driver.find_element(By.TAG_NAME, "body").text

        if self.captcha_rejected(body_text):
            self.clear_cached_captcha()
            return ResultRow(
                consumer_no=consumer_no,
                info="INVALID_CAPTCHA",
            )

        name = self.extract_field_value(
            label_patterns=[r"consumer\s*name", r"name\/address.*consumer", r"name"],
            body_text=body_text,
        )
        address = self.extract_field_value(
            label_patterns=[r"consumer\s*address", r"address", r"name\/address.*consumer"],
            body_text=body_text,
            multiline=True,
        )
        bill_amount = self.extract_amount(body_text)
        due_date = self.extract_due_date(body_text)
        info = self.extract_info(body_text, name=name, amount=bill_amount, due_date=due_date)

        if self.name_keywords and not self.name_matches(name):
            info = f"{info} | NAME_MISMATCH" if info else "NAME_MISMATCH"

        return ResultRow(
            consumer_no=consumer_no,
            consumer_name=name,
            consumer_address=address,
            bill_amount_rs=bill_amount,
            due_date=due_date,
            info=info,
        )

    def extract_field_value(
        self,
        label_patterns: Sequence[str],
        body_text: str,
        multiline: bool = False,
    ) -> str:
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        joined = "\n".join(lines)

        for pattern in label_patterns:
            regexes = [
                rf"(?im)^\s*{pattern}\s*[:\-]?\s*(.+)$",
                rf"(?is){pattern}\s*[:\-]?\s*(.+?)(?:\n[A-Z][A-Za-z /\-]{{1,40}}\s*[:\-]|$)",
            ]
            for rx in regexes:
                match = re.search(rx, joined)
                if match:
                    value = match.group(1).strip()
                    if not multiline:
                        value = value.splitlines()[0].strip()
                    value = self.clean_field(value)
                    if value:
                        return value

        if multiline:
            for pattern in label_patterns:
                for idx, line in enumerate(lines):
                    if re.search(pattern, line, flags=re.I):
                        chunk = " ".join(lines[idx + 1: idx + 4]).strip()
                        chunk = self.clean_field(chunk)
                        if chunk:
                            return chunk

        return ""

    def extract_amount(self, body_text: str) -> str:
        patterns = [
            r"(?i)bill\s*amount\s*[:\-]?\s*(?:rs\.?|inr)?\s*([0-9]+(?:\.[0-9]{1,2})?)",
            r"(?i)(?:rs\.?|inr)\s*([0-9]+(?:\.[0-9]{1,2})?)\s*\/?-?",
        ]
        for rx in patterns:
            match = re.search(rx, body_text)
            if match:
                return match.group(1).strip()
        return ""

    def extract_due_date(self, body_text: str) -> str:
        patterns = [
            r"(?i)due\s*date\s*[:\-]?\s*([0-3]?\d[\/\-][0-1]?\d[\/\-]\d{2,4})",
            r"(?i)pay\s+this\s+bill\s+by.*?([0-3]?\d[\/\-][0-1]?\d[\/\-]\d{2,4})",
        ]
        for rx in patterns:
            match = re.search(rx, body_text, flags=re.S)
            if match:
                return match.group(1).strip()
        return ""

    def extract_info(self, body_text: str, name: str, amount: str, due_date: str) -> str:
        upper = " ".join(body_text.split()).upper()
        status_markers = [
            "NO PENDING BILL",
            "INVALID",
            "INVALID CONSUMER",
            "INVALID CONSUMER NO",
            "CHECK CAPTCHA",
            "NOT FOUND",
            "SERVICE NOT AVAILABLE",
            "TRY AGAIN",
        ]
        found = [marker for marker in status_markers if marker in upper]
        if found:
            return " | ".join(found)

        if name or amount or due_date:
            return "OK"

        return "UNPARSED"

    def clean_field(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip(" :-\n\t")

    def name_matches(self, extracted_name: str) -> bool:
        if not self.name_keywords:
            return True
        name_lower = (extracted_name or "").lower()
        return any(keyword in name_lower for keyword in self.name_keywords)

    def navigate_back_to_form(self) -> None:
        self.driver.back()
        self.wait.until(lambda d: self.find_consumer_input() is not None)
        self.wait_for_form_ready()

    def process_consumer(self, consumer_no: str) -> ResultRow:
        attempt = 0
        last_error = None

        while attempt <= self.max_retries:
            try:
                logging.info("Processing consumer no: %s (attempt %s)", consumer_no, attempt + 1)
                self.set_consumer_number(consumer_no)
                self.prompt_manual_captcha(reuse_cached=True)
                self.submit_form()
                self.wait_for_result_transition()

                result = self.extract_details(consumer_no)
                if result.info == "INVALID_CAPTCHA":
                    logging.warning("Cached/manual CAPTCHA rejected for consumer %s", consumer_no)
                    if attempt >= self.max_retries:
                        return result
                    self.safe_recover_to_form()
                    self.clear_cached_captcha()
                    attempt += 1
                    continue

                return result

            except (TimeoutException, NoSuchElementException, WebDriverException) as exc:
                last_error = exc
                logging.warning("Consumer %s failed on attempt %s: %s", consumer_no, attempt + 1, exc)
                if attempt >= self.max_retries:
                    break
                self.safe_recover_to_form()
                attempt += 1

        return ResultRow(
            consumer_no=consumer_no,
            info=f"ERROR: {type(last_error).__name__}: {last_error}" if last_error else "ERROR",
        )

    def safe_recover_to_form(self) -> None:
        try:
            self.navigate_back_to_form()
            return
        except Exception:
            pass
        self.driver.get(DEFAULT_URL)
        self.wait_for_form_ready()


def build_driver(headless: bool = False, user_data_dir: Optional[str] = None) -> Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=en-IN")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if user_data_dir:
        options.add_argument(f"--user-data-dir={user_data_dir}")

    try:
        driver = webdriver.Chrome(options=options)
    except (SessionNotCreatedException, WebDriverException) as exc:
        raise RuntimeError(
            "Failed to start Chrome WebDriver. Ensure Chrome and matching ChromeDriver are installed."
        ) from exc

    driver.set_page_load_timeout(60)
    driver.implicitly_wait(0)
    return driver


def generate_consumer_numbers(start: str, end: str) -> Iterable[str]:
    if not start.isdigit() or not end.isdigit():
        raise ValueError("Start and end consumer numbers must be numeric strings")

    width = max(len(start), len(end))
    start_num = int(start)
    end_num = int(end)

    if end_num < start_num:
        raise ValueError("End consumer number must be greater than or equal to start")

    for num in range(start_num, end_num + 1):
        yield str(num).zfill(width)


def export_to_excel(rows: Sequence[ResultRow], output_path: str) -> Path:
    df = pd.DataFrame(
        [
            {
                "Consumer No": row.consumer_no,
                "Consumer Name": row.consumer_name,
                "Consumer Address": row.consumer_address,
                "Bill Amount (Rs)": row.bill_amount_rs,
                "Due Date": row.due_date,
                "Info": row.info,
            }
            for row in rows
        ]
    )
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="TNEB Bills")
        ws = writer.sheets["TNEB Bills"]
        widths = {"A": 18, "B": 28, "C": 45, "D": 18, "E": 16, "F": 30}
        for col, width in widths.items():
            ws.column_dimensions[col].width = width

    return out


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract consumer billing details from the TNEB/TNPDCL Quickpay portal using Selenium."
    )
    parser.add_argument("--start", required=True, help="Start consumer number, numeric, zero-padded if needed")
    parser.add_argument("--end", required=True, help="End consumer number, numeric, zero-padded if needed")
    parser.add_argument(
        "--keywords",
        nargs="*",
        default=[],
        help="Optional consumer name keywords for case-insensitive partial matching",
    )
    parser.add_argument("--output", default="tneb_billing_details.xlsx", help="Excel output path")
    parser.add_argument("--url", default=DEFAULT_URL, help="Portal URL")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between iterations in seconds")
    parser.add_argument("--retries", type=int, default=1, help="Retries per consumer on failure")
    parser.add_argument("--timeout", type=int, default=20, help="Explicit wait timeout in seconds")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome in headless mode; only use if manual CAPTCHA entry remains practical in your setup",
    )
    parser.add_argument("--user-data-dir", default=None, help="Optional Chrome user data directory")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)

    driver = None
    results: List[ResultRow] = []

    try:
        driver = build_driver(headless=args.headless, user_data_dir=args.user_data_dir)
        scraper = TNEBScraper(
            driver=driver,
            wait_timeout=args.timeout,
            delay_seconds=args.delay,
            max_retries=max(0, args.retries),
            name_keywords=args.keywords,
        )

        scraper.open_portal(args.url)

        for consumer_no in generate_consumer_numbers(args.start, args.end):
            try:
                row = scraper.process_consumer(consumer_no)
                results.append(row)
                logging.info(
                    "Recorded | consumer=%s | name=%s | amount=%s | due=%s | info=%s",
                    row.consumer_no,
                    row.consumer_name,
                    row.bill_amount_rs,
                    row.due_date,
                    row.info,
                )
            except (InvalidSessionIdException, RuntimeError) as exc:
                logging.error("Fatal browser/session issue at consumer %s: %s", consumer_no, exc)
                results.append(ResultRow(consumer_no=consumer_no, info=f"FATAL_ERROR: {exc}"))
                break
            except Exception as exc:
                logging.exception("Unexpected failure for consumer %s", consumer_no)
                results.append(ResultRow(consumer_no=consumer_no, info=f"UNEXPECTED_ERROR: {exc}"))

            try:
                scraper.navigate_back_to_form()
            except Exception as exc:
                logging.warning("Back navigation failed after consumer %s: %s", consumer_no, exc)
                try:
                    scraper.safe_recover_to_form()
                except Exception as recover_exc:
                    logging.error("Recovery failed after consumer %s: %s", consumer_no, recover_exc)
                    break

            time.sleep(max(0.0, args.delay))

        output_file = export_to_excel(results, args.output)
        logging.info("Exported %s rows to %s", len(results), output_file)
        return 0

    except KeyboardInterrupt:
        logging.warning("Interrupted by user")
        if results:
            output_file = export_to_excel(results, args.output)
            logging.info("Partial results exported to %s", output_file)
        return 130
    except Exception as exc:
        logging.exception("Unhandled fatal error: %s", exc)
        if results:
            output_file = export_to_excel(results, args.output)
            logging.info("Partial results exported to %s", output_file)
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
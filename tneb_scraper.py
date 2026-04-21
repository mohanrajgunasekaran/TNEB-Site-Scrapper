#!/usr/bin/env python3
"""
TNEB consumer lookup automation (Selenium).

Important:
- Use only if you are authorized to access the queried consumer records.
- Respect TNEB terms of use, applicable law, and reasonable rate limits.
- CAPTCHA must be entered by a human (no CAPTCHA bypass in this script).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

DEFAULT_BASE_URL = "https://www.tnebnet.org/qwp/qpay?login_error=1"


def is_valid_captcha(value: str) -> bool:
    value = value.strip()
    return value.isdigit() and 4 <= len(value) <= 8


@dataclass
class ConsumerRecord:
    consumer_no: str
    consumer_name: str
    consumer_address: str
    bill_amount: str
    due_date: str
    info: str


class TNEBScraper:
    def __init__(
        self,
        base_url: str,
        desired_names: Iterable[str],
        output_file: str,
        headless: bool,
        delay_seconds: float,
        timeout_seconds: int,
        post_submit_wait: float,
        fixed_captcha: Optional[str] = None,
    ) -> None:
        self.base_url = base_url
        self.desired_names = [n.strip().lower() for n in desired_names if n.strip()]
        self.output_file = output_file
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.post_submit_wait = post_submit_wait
        self.fixed_captcha = fixed_captcha.strip() if fixed_captcha else None
        self.driver = self._build_driver(headless=headless)
        self.wait = WebDriverWait(self.driver, timeout_seconds)
        self.records: List[ConsumerRecord] = []

    @staticmethod
    def _build_driver(headless: bool) -> WebDriver:
        options = Options()
        options.page_load_strategy = "eager"
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,1000")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        return webdriver.Chrome(options=options)

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def open_form(self) -> None:
        self.driver.get(self.base_url)
        self._wait_for_form_ready()

    def _wait_for_form_ready(self) -> None:
        self._find_first(
            [
                (By.CSS_SELECTOR, "input[name*='cons']"),
                (By.CSS_SELECTOR, "input[id*='cons']"),
                (By.XPATH, "//input[contains(@placeholder,'Consumer') or contains(@aria-label,'Consumer')]"),
                (By.XPATH, "//label[contains(translate(., 'consumer', 'CONSUMER'), 'CONSUMER')]/following::input[1]"),
            ]
        )

    def _find_first(self, locators: List[tuple]):
        last_err: Optional[Exception] = None
        for by, selector in locators:
            try:
                return self.wait.until(EC.presence_of_element_located((by, selector)))
            except Exception as exc:
                last_err = exc
        raise TimeoutException(f"No matching element found. Last error: {last_err}")

    def _fill_consumer_no(self, consumer_no: str) -> None:
        input_el = self._find_first(
            [
                (By.CSS_SELECTOR, "input[name*='cons']"),
                (By.CSS_SELECTOR, "input[id*='cons']"),
                (By.XPATH, "//label[contains(translate(., 'consumer', 'CONSUMER'), 'CONSUMER')]/following::input[1]"),
            ]
        )
        input_el.clear()
        input_el.send_keys(consumer_no)

    def _get_captcha_for_iteration(self, consumer_no: str) -> str:
        if self.fixed_captcha:
            return self.fixed_captcha

        while True:
            captcha = input(f"Enter CAPTCHA digits visible in browser for consumer {consumer_no}: ").strip()
            if is_valid_captcha(captcha):
                return captcha
            print("Invalid CAPTCHA format. Please enter 4 to 8 digits.")

    def _fill_captcha(self, captcha: str) -> None:
        try:
            input_el = self._find_first(
                [
                    (By.CSS_SELECTOR, "input[name*='capt']"),
                    (By.CSS_SELECTOR, "input[id*='capt']"),
                    (By.XPATH, "//input[@maxlength='4' or @maxlength='5' or @maxlength='6' or @maxlength='7' or @maxlength='8']"),
                    (By.XPATH, "//label[contains(translate(., 'captcha', 'CAPTCHA'), 'CAPTCHA')]/following::input[1]"),
                    (By.XPATH, "(//input[(@type='text' or not(@type)) and not(@type='hidden')])[2]"),
                ]
            )
        except TimeoutException:
            text_inputs = [
                el
                for el in self.driver.find_elements(By.XPATH, "//input[(@type='text' or not(@type)) and not(@type='hidden')]")
                if el.is_displayed() and el.is_enabled()
            ]
            if len(text_inputs) < 2:
                raise
            input_el = text_inputs[1]

        input_el.clear()
        input_el.send_keys(captcha)

    def _submit(self) -> None:
        btn = self._find_first(
            [
                (By.CSS_SELECTOR, "button[type='submit']"),
                (By.CSS_SELECTOR, "input[type='submit']"),
                (By.XPATH, "//button[contains(translate(., 'submit', 'SUBMIT'), 'SUBMIT') or contains(translate(., 'search', 'SEARCH'), 'SEARCH')]"),
                (By.XPATH, "//input[contains(@value,'Submit') or contains(@value,'Search') or contains(@value,'Pay')]"),
            ]
        )
        self.driver.execute_script("arguments[0].click();", btn)

    def _extract_text_after_label(self, label: str) -> str:
        xpath_candidates = [
            f"//*[contains(translate(normalize-space(.), '{label.lower()}', '{label.upper()}'), '{label.upper()}')]/following::*[1]",
            f"//*[contains(translate(normalize-space(.), '{label.lower()}', '{label.upper()}'), '{label.upper()}')]/following-sibling::*[1]",
        ]
        for xp in xpath_candidates:
            try:
                el = self.driver.find_element(By.XPATH, xp)
                txt = el.text.strip()
                if txt:
                    return txt
            except Exception:
                continue

        page_text = self.driver.find_element(By.TAG_NAME, "body").text
        pattern = re.compile(rf"{re.escape(label)}\s*[:\-]\s*(.+)", re.IGNORECASE)
        match = pattern.search(page_text)
        return match.group(1).strip() if match else ""

    def _parse_result(self, consumer_no: str) -> ConsumerRecord:
        body_text = self.driver.find_element(By.TAG_NAME, "body").text
        consumer_name = self._extract_text_after_label("Consumer Name") or ""
        consumer_address = self._extract_text_after_label("Consumer Address") or ""
        bill_amount = self._extract_text_after_label("Bill Amount") or ""
        due_date = self._extract_text_after_label("Due Date") or ""

        if not bill_amount:
            amt_match = re.search(r"Bill\s*Amount\s*[:\-]?\s*([A-Za-z0-9./\- ]+)", body_text, flags=re.IGNORECASE)
            if amt_match:
                bill_amount = amt_match.group(1).strip()

        if not due_date:
            due_match = re.search(r"Due\s*Date\s*[:\-]?\s*([A-Za-z0-9./\- ]+)", body_text, flags=re.IGNORECASE)
            if due_match:
                due_date = due_match.group(1).strip()

        info = ""
        for key in ["NO PENDING BILL", "PENDING BILL", "INVALID", "NOT FOUND", "ERROR"]:
            if key in body_text.upper():
                info = key
                break

        if not info:
            info = "OK"

        return ConsumerRecord(
            consumer_no=consumer_no,
            consumer_name=consumer_name,
            consumer_address=consumer_address,
            bill_amount=bill_amount or "-",
            due_date=due_date or "-",
            info=info,
        )

    def _name_matches(self, name: str) -> bool:
        if not self.desired_names:
            return True
        lower = name.lower()
        return any(pattern in lower for pattern in self.desired_names)

    def _go_back_without_refresh(self) -> None:
        self.driver.back()
        self._wait_for_form_ready()

    def _return_to_form(self) -> None:
        try:
            WebDriverWait(self.driver, 2).until(
                lambda d: len(d.find_elements(By.XPATH, "//input[(@type='text' or not(@type)) and not(@type='hidden') and not(@disabled)]")) >= 2
            )
            return
        except Exception:
            self._go_back_without_refresh()

    def process_consumer(self, consumer_no: str) -> None:
        self._fill_consumer_no(consumer_no)
        captcha = self._get_captcha_for_iteration(consumer_no)
        self._fill_captcha(captcha)
        self._submit()

        time.sleep(self.post_submit_wait)
        record = self._parse_result(consumer_no)

        if not self._name_matches(record.consumer_name):
            record.info = f"{record.info} | NAME_MISMATCH"
            print(f"[ROW]   {consumer_no} -> {record.consumer_name or 'N/A'} (name mismatch)")
        else:
            print(f"[ROW]   {consumer_no} -> {record.consumer_name or 'N/A'}")

        # Requirement: each search should create a row.
        self.records.append(record)

        self._return_to_form()
        time.sleep(self.delay_seconds)

    def run_range(self, start: str, end: str) -> None:
        if not start.isdigit() or not end.isdigit():
            raise ValueError("Consumer number range must be numeric.")
        if int(start) > int(end):
            raise ValueError("Start consumer number must be <= end consumer number.")

        width = max(len(start), len(end))
        self.open_form()

        for value in range(int(start), int(end) + 1):
            consumer_no = str(value).zfill(width)
            try:
                self.process_consumer(consumer_no)
            except Exception as exc:
                print(f"[ERROR] {consumer_no}: {exc}")
                self.records.append(
                    ConsumerRecord(
                        consumer_no=consumer_no,
                        consumer_name="",
                        consumer_address="",
                        bill_amount="-",
                        due_date="-",
                        info=f"ERROR: {exc}",
                    )
                )
                try:
                    self._return_to_form()
                except Exception:
                    self.driver.get(self.base_url)
                    self._wait_for_form_ready()

        self.export_excel()

    def export_excel(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "Consumer No": r.consumer_no,
                    "Consumer Name": r.consumer_name,
                    "Consumer Address": r.consumer_address,
                    "Bill Amount (Rs)": r.bill_amount,
                    "Due Date": r.due_date,
                    "Info": r.info,
                }
                for r in self.records
            ]
        )
        if df.empty:
            df = pd.DataFrame(
                columns=[
                    "Consumer No",
                    "Consumer Name",
                    "Consumer Address",
                    "Bill Amount (Rs)",
                    "Due Date",
                    "Info",
                ]
            )
        df.to_excel(self.output_file, index=False)
        print(f"Saved {len(df)} rows to {self.output_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape TNEB consumer details into Excel.")
    parser.add_argument("--start", required=True, help="Start consumer number (inclusive).")
    parser.add_argument("--end", required=True, help="End consumer number (inclusive).")
    parser.add_argument(
        "--captcha",
        default=None,
        help="Fixed CAPTCHA (4 to 8 digits; use only if CAPTCHA does not change between queries).",
    )
    parser.add_argument(
        "--names",
        nargs="*",
        default=[],
        help="Desired consumer names (partial match, case-insensitive). Non-matches are still logged as NAME_MISMATCH.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default="tneb_consumers.xlsx")
    parser.add_argument("--delay", type=float, default=0.4, help="Delay between searches (seconds).")
    parser.add_argument("--post-submit-wait", type=float, default=0.35, help="Small wait after submit before parsing (seconds).")
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.captcha is not None and not is_valid_captcha(args.captcha):
        print("Error: --captcha must be 4 to 8 digits when provided.")
        return 2

    scraper = TNEBScraper(
        base_url=args.base_url,
        desired_names=args.names,
        output_file=args.output,
        headless=args.headless,
        delay_seconds=args.delay,
        timeout_seconds=args.timeout,
        post_submit_wait=args.post_submit_wait,
        fixed_captcha=args.captcha,
    )

    try:
        scraper.run_range(args.start, args.end)
    except (ValueError, WebDriverException) as exc:
        print(f"Fatal error: {exc}")
        return 1
    finally:
        scraper.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

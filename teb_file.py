import time
import re
import logging
import pandas as pd
from typing import List, Optional, Dict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, UnexpectedAlertPresentException, NoSuchElementException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TNEBScraper:
    def __init__(self, start_num: int, end_num: int, keywords: Optional[List[str]] = None, headless: bool = False):
        self.start_num = start_num
        self.end_num = end_num
        self.keywords = [k.lower() for k in keywords] if keywords else []
        self.headless = headless
        self.base_url = "https://www.tnebnet.org/qwp/qpay?login_error=1"
        self.results = []
        self.driver = self._setup_driver()

    def _setup_driver(self) -> webdriver.Chrome:
        options = webdriver.ChromeOptions()
        if self.headless:
            options.add_argument('--headless')
            logging.warning("Headless mode enabled. Ensure you have a way to view the CAPTCHA.")
        
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        return webdriver.Chrome(options=options)

    def _wait_and_find(self, by: By, selector: str, timeout: int = 10):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, selector))
        )

    def _interact_with_form(self, consumer_no: str):
        try:
            # Resilient selectors targeting common naming conventions
            user_input = self._wait_and_find(By.XPATH, "//input[contains(translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'user') or contains(@id, 'user')]")
            captcha_input = self._wait_and_find(By.XPATH, "//input[contains(translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'captcha') or contains(@id, 'captcha')]")
            
            user_input.clear()
            user_input.send_keys(consumer_no)
            
            captcha_val = input(f"\n[Consumer {consumer_no}] Enter CAPTCHA from browser: ").strip()
            captcha_input.clear()
            captcha_input.send_keys(captcha_val)
            
            submit_btn = self.driver.find_element(By.XPATH, "//input[@type='submit' or @type='button'] | //button[contains(., 'Submit')]")
            submit_btn.click()
            return True
            
        except TimeoutException:
            logging.error(f"[{consumer_no}] Form elements not found.")
            return False

    def _extract_data(self) -> Dict:
        data = {
            "Consumer Name": "N/A", "Consumer Address": "N/A",
            "Bill Amount": "N/A", "Due Date": "N/A", "Info": "N/A"
        }
        
        try:
            # Wait for either a result table or an error message to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//table | //*[contains(text(), 'Invalid') or contains(text(), 'Pending')]"))
            )
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            
            # 1. Attempt structured extraction (Assume standard table th/td relationships)
            # This logic depends highly on actual DOM structure. Using basic siblings here.
            try:
                name_elem = self.driver.find_element(By.XPATH, "//*[contains(text(), 'Name')]/following-sibling::*")
                data["Consumer Name"] = name_elem.text.strip()
            except NoSuchElementException:
                pass 

            # 2. Fallback to Regex parsing against raw page text
            if data["Consumer Name"] == "N/A":
                # Matches basic patterns; adjust based on actual portal output
                amt_match = re.search(r"(?:Amount|Rs\.?)\s*[:\-]?\s*([\d,]+\.?\d*)", page_text, re.IGNORECASE)
                date_match = re.search(r"(?:Date)\s*[:\-]?\s*(\d{2}[-/]\d{2}[-/]\d{4})", page_text, re.IGNORECASE)
                
                if amt_match: data["Bill Amount"] = amt_match.group(1)
                if date_match: data["Due Date"] = date_match.group(1)
                
                if "invalid" in page_text.lower():
                    data["Info"] = "INVALID CONSUMER"
                elif "no pending" in page_text.lower():
                    data["Info"] = "NO PENDING BILL"
                    
        except TimeoutException:
            data["Info"] = "PAGE LOAD TIMEOUT / NO DATA FOUND"
            
        return data

    def _validate(self, data: Dict) -> str:
        if not self.keywords or data["Consumer Name"] == "N/A":
            return data["Info"]
            
        name_lower = data["Consumer Name"].lower()
        if any(keyword in name_lower for keyword in self.keywords):
            return data["Info"]
        
        return "NAME_MISMATCH" if data["Info"] == "N/A" else f"{data['Info']} | NAME_MISMATCH"

    def _navigate_back(self):
        self.driver.back()
        try:
            WebDriverWait(self.driver, 3).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            alert.accept()
        except TimeoutException:
            pass 

    def run(self):
        self.driver.get(self.base_url)
        
        for num in range(self.start_num, self.end_num + 1):
            consumer_no = str(num).zfill(10) # Adjust padding based on actual TNEB format
            success = False
            attempts = 0
            
            while not success and attempts < 2:
                attempts += 1
                logging.info(f"Processing {consumer_no} (Attempt {attempts}/2)")
                
                if not self._interact_with_form(consumer_no):
                    self.driver.refresh()
                    continue
                
                extracted_data = self._extract_data()
                
                if extracted_data["Info"] == "PAGE LOAD TIMEOUT / NO DATA FOUND" and attempts == 1:
                    logging.warning(f"[{consumer_no}] Verification failed. Retrying...")
                    self._navigate_back()
                    continue
                
                extracted_data["Info"] = self._validate(extracted_data)
                
                self.results.append({
                    "Consumer No": consumer_no,
                    "Consumer Name": extracted_data["Consumer Name"],
                    "Consumer Address": extracted_data["Consumer Address"],
                    "Bill Amount (Rs)": extracted_data["Bill Amount"],
                    "Due Date": extracted_data["Due Date"],
                    "Info": extracted_data["Info"]
                })
                
                success = True
                
            self._navigate_back()
            time.sleep(1.5) 
            
        self.driver.quit()
        self._export_to_excel()

    def _export_to_excel(self):
        if not self.results:
            logging.info("No data to export.")
            return
            
        df = pd.DataFrame(self.results)
        filename = f"TNEB_Billing_{self.start_num}_to_{self.end_num}.xlsx"
        df.to_excel(filename, index=False)
        logging.info(f"Successfully exported data to {filename}")

if __name__ == "__main__":
    # Example execution
    scraper = TNEBScraper(
        start_num=1000000001, 
        end_num=1000000005, 
        keywords=["kumar", "enterprise"], 
        headless=False
    )
    scraper.run()
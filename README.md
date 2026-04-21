# TNEB Site Scraper

## 1) Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> You also need Google Chrome and a compatible ChromeDriver available in PATH.

## 2) Run the script

### Recommended (manual CAPTCHA each request)
```bash
python tneb_scraper.py \
  --start 02302013170 \
  --end 02302013180 \
  --names "SIVASA" "RAMESH" \
  --output tneb_consumers.xlsx \
  --delay 2
```

- Browser opens TNEB page.
- For each consumer number, read the CAPTCHA shown in browser and enter it in terminal.
- Script submits, captures details, goes back using browser history, then continues to next consumer number.

### Fixed CAPTCHA mode (only if same CAPTCHA is valid repeatedly)
```bash
python tneb_scraper.py \
  --start 02302013170 \
  --end 02302013180 \
  --captcha 123456 \
  --names "SIVASA" "RAMESH"
```

## 3) Output format
Excel file contains columns:
- Consumer No
- Consumer Name
- Consumer Address
- Bill Amount (Rs)
- Due Date
- Info

Each consumer number attempted is written as a new row. If a name does not match the `--names` filter, it is still included with `NAME_MISMATCH` in `Info`.

## 4) Notes
- Respect TNEB website terms and legal requirements.
- Keep delays reasonable to avoid overloading the service.
- CAPTCHA solving is not automated.

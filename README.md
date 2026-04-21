# TNEB Site Scraper

## Quick fix for your error
If you see:
`ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
it means you are running commands in the wrong folder.

You must first `cd` into the folder that contains these files:
- `tneb_scraper.py`
- `requirements.txt`

---

## 1) Open terminal in project folder

### If you downloaded as ZIP
```bat
cd %USERPROFILE%\Downloads
cd TNEB-Site-Scrapper
```

### If you cloned with git
```bat
git clone <your-repo-url>
cd TNEB-Site-Scrapper
```

Verify files exist:
```bat
dir
```
You should see `requirements.txt` in the output.

### If `cd TNEB-Site-Scrapper` fails (path not found)
Use one of these to locate the folder:

```bat
dir %USERPROFILE%\Downloads
dir %USERPROFILE%\Desktop
where /r %USERPROFILE% requirements.txt
```

Then `cd` to the parent folder reported by `where`, for example:
```bat
cd C:\Users\egxxmoh\Desktop\TNEB-Site-Scrapper
dir
```


### If clone contains only `.gitkeep`
This means the branch you cloned does not include scraper files yet.

Check branches:
```bat
git branch -a
git fetch --all
```

Switch to whichever remote branch has scraper files (example shown):
```bat
git switch -c codex/create-web-scraping-script-for-tneb-data origin/codex/create-web-scraping-script-for-tneb-data
dir
```

Or use any other remote branch that contains `tneb_scraper.py` and `requirements.txt`.

If no branch contains `tneb_scraper.py`, the repository owner has not pushed/merged the scraper files yet.


---

## 2) Create and activate virtual environment (Windows)

```bat
python -m venv .venv
.venv\Scripts\activate
```

> `source .venv/bin/activate` is Linux/macOS syntax. On Windows CMD, use `.venv\Scripts\activate`.

---

## 3) Install dependencies

```bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## 4) Run the scraper

### Recommended (manual CAPTCHA each request)
```bat
python tneb_scraper.py --start 02302013170 --end 02302013180 --names "SIVASA" "RAMESH" --output tneb_consumers.xlsx --delay 0.4 --post-submit-wait 0.35
```

What happens:
- Browser opens the TNEB page.
- Script asks CAPTCHA once, then reuses it for remaining consumer numbers by default.
- It submits, extracts data, goes back without refresh, and continues.

### Optional fixed CAPTCHA mode
(Use only if one CAPTCHA is valid for repeated requests; accepts 4 to 8 digits.)
```bat
python tneb_scraper.py --start 02302013170 --end 02302013180 --captcha 123456 --names "SIVASA" "RAMESH"
```

### Optional: ask CAPTCHA every time
```bat
python tneb_scraper.py --start 02302013170 --end 02302013180 --names "SIVASA" "RAMESH" --ask-each-captcha
```

---

## 5) Output
Excel file columns:
- Consumer No
- Consumer Name
- Consumer Address
- Bill Amount (Rs)
- Due Date
- Info

Each consumer attempt is written as a new row.

---

## Notes
- Respect TNEB website terms and applicable laws.
- Keep delay reasonable to avoid overloading the service.
- CAPTCHA solving is not automated.


## Speed tuning
- Use smaller delay values for faster processing (example: `--delay 0.2`).
- Use a small `--post-submit-wait` value (example: `--post-submit-wait 0.2`) if your network/browser is stable.
- If the site starts rejecting requests or showing errors, increase delay gradually.

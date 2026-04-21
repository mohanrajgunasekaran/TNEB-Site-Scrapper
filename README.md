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
python tneb_scraper.py --start 02302013170 --end 02302013180 --names "SIVASA" "RAMESH" --output tneb_consumers.xlsx --delay 2
```

What happens:
- Browser opens the TNEB page.
- For each consumer number, script asks CAPTCHA in terminal.
- It submits, extracts data, goes back without refresh, and continues.

### Optional fixed CAPTCHA mode
(Use only if one CAPTCHA is valid for repeated requests.)
```bat
python tneb_scraper.py --start 02302013170 --end 02302013180 --captcha 123456 --names "SIVASA" "RAMESH"
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

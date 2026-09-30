# Domain collector backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The first start creates `data/domains.db` and a local `data/.cookie.key`.
Cookies are encrypted before they are stored. Set `COOKIE_ENCRYPTION_KEY` in
production and keep it outside the repository.

The built-in GNAME source reads `/download/expired`, downloads
`/request/downfile?xm=delete&date=YYYY-MM-DD&lx=txt|csv`, and rejects the run
when GNAME returns its login page. Additional sources use the same HTTP
template and line/CSV parser through the generic adapter.

To configure GNAME, sign in in your browser, open DevTools → Network, copy the
`Cookie` request header from a GNAME request, and paste it into the source form.
The UI never reads the browser cookie automatically; the backend stores the
provided value encrypted and only sends it to the configured source.

The scheduler is deliberately daily and minute-based: it checks enabled jobs
every 20 seconds, selects the newest date published by the source, and records
the run before starting a download. SQLite keeps the domain key unique; newer
imports update `joined_at` and older imports never overwrite it.

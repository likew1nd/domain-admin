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

## Dynadot 鉴权诊断

在 `backend` 目录执行以下 PowerShell 命令。填写同一次生成、同一环境的
API Key 和 API Secret；这里不使用 Dynadot 账户登录密码。

```powershell
$env:DYNADOT_KEY = Read-Host 'Dynadot API Key' -MaskInput
$env:DYNADOT_SECRET = Read-Host 'Dynadot API Secret' -MaskInput
$env:DYNADOT_ENDPOINT = 'https://api.dynadot.com'
python test_dynadot_auth.py
```

生产环境地址为 `https://api.dynadot.com`（默认）；沙盒测试时，将
`DYNADOT_ENDPOINT` 设为 `https://api-sandbox.dynadot.com`，并使用对应的沙盒凭据。
环境由接口地址指定，不能根据密钥前缀判断。

脚本复用正式 `DynadotRegistrar`，只读取账户信息来检查鉴权，不执行注册或下单。
输出仅包含成功状态、HTTP 状态码或固定的错误提示，不输出凭据、签名或账户详情。
退出码为 `0` 表示成功，`1` 表示请求或鉴权失败，`2` 表示环境变量配置不完整或无效。
模块导入不会发送请求；原有诊断脚本名称仍可运行，均转发到此入口。

使用后可清除当前 PowerShell 会话里的凭据：

```powershell
Remove-Item Env:DYNADOT_KEY, Env:DYNADOT_SECRET -ErrorAction SilentlyContinue
```

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

阿里云数据源使用 `https://domainapi.aliyun.com/partnerReserve/snatchExport.json`，
需要在数据源配置中保存登录 `mi.aliyun.com` 后的完整 Cookie（CK），沿用加密存储。
手动采集按所选释放日期逐个后缀请求 TXT；后缀留空默认 `com`，多个后缀用逗号或空格分隔。
“采集今日”和定时任务使用当天释放日期，不表示源站提供历史文件索引。
多个后缀合并去重后入库；CK 失效会记录失败并提示重新登录。
单个后缀导出达到 10000 条时，任务会提示结果可能不完整并终止入库；
可在源站缩小筛选条件后导出，再使用本页的“导入 TXT”。

域名文件使用严格解码：默认先尝试 UTF-8，再尝试 GBK/GB18030，解码失败会终止导入。
西部数码下载明确使用 GB18030（兼容 GBK），避免将“凭证.com”等中文域名误读为乱码；UTF-8 BOM 优先。

例外和预订方案固定按“且”匹配：所有已填写条件均须命中；不再提供匹配逻辑选项，历史“或”配置也按“且”执行。排除字符始终优先否决；多个方案之间仍为“或”，同一字段内的多个选值仍为任选其一。预订的备案时间与备案性质须由同一条备案记录满足。
例如长度 4、后缀 com 选择“或”，`abcd.cn` 和 `longname.com` 均命中。
查询范围先于例外策略生效：基础长度若限定为 4，其他长度不会进入查询；修改配置后需重新启动查询任务。
预订设置保存后应用于监控列表；所选通道还须支持域名后缀。
例外和预订方案支持“域名组成”条件（英文、数字、中文、符号）：不选表示不限，多选表示域名主体只由所选类别组成，可仅含其中一种；与长度、后缀等条件按“且”匹配。

To configure GNAME, sign in in your browser, open DevTools → Network, copy the
`Cookie` request header from a GNAME request, and paste it into the source form.
The UI never reads the browser cookie automatically; the backend stores the
provided value encrypted and only sends it to the configured source.

## Gname 自动预订

在“监控任务 → API 设置”添加并启用 Gname 账号，Token 使用 `AppID:AppKey`。
在“监控任务 → 预订设置”配置方案、选择账号、刷新并选择通道，开启自动预订后保存。
系统每 5 秒扫描监控列表，已有和新进入列表的域名命中方案后调用 `/api/backorder/add`；通道来自 `/api/backorder/channel`。
已踢出、已注册、未进入监控列表的域名不提交预订；旧查询配置中的预订方案自动迁移一次。
注册时间使用 WHOIS 注册日期，备案时间使用备案记录的审核/更新日期；缺失日期不命中时间条件。
预订不依赖监控任务启动或自动注册开关，不改变原有符合条件及注册流程。

“监控任务 → 预订日志”显示等待、提交中、成功、失败、待核实状态。
“域名管理 → 预订列表”展示所有预订记录，成功和失败均保留，包含检测与备案信息、数据来源、命中方案、预订商、通道、预订结果和预订时间；预订结果显示“成功”或具体错误，未完成的记录显示等待提交、正在提交或待核实。预订时间优先使用完成时间，未完成时使用入队时间；同一域名的不同账号、通道分别展示。
同一域名、账号、通道永久去重，多个方案命中也只提交一次。
通道按选择顺序尝试，例如先选 9、8、7，则 9 号明确失败后尝试 8 号，8 号明确失败后尝试 7 号；任一通道成功即停止该域名的后续预订。多个方案同时命中时按方案列表顺序尝试，已明确失败的账号/通道不重试。
每次尝试分别保留预订记录，尚未轮到的通道不提前创建记录。旧版留下的未发送队列项按当前顺序整理，已有成功或待核实记录的域名不再提交其他通道。
提交超时或提交过程中服务重启时标为待核实，不自动重发，也不切换其他通道；请在 Gname 核实订单。
预订成功仅代表已提交预订并冻结资金，不代表已注册成功。

The scheduler is deliberately daily and minute-based: it checks enabled jobs
every 20 seconds, selects the newest date published by the source, and records
the run before starting a download. SQLite keeps the domain key unique; newer
imports update `joined_at` and older imports never overwrite it.

## Dynadot 鉴权诊断

RESTful v2 注册必填 `domain.duration` 和 `domain.privacy`。适配器在未配置时
默认发送 `{"domain":{"duration":1,"privacy":"full"}}`；已有空 `payload` 配置也适用。
可在 `payload.domain` 内覆盖这两个值并指定联系人 ID；`privacy` 可选 `off`、
`partial`、`full`。特殊后缀需按注册局规则设置年限（例如 `.ai` 至少 2 年）。
参考：[Dynadot RESTful API 注册文档](https://www.dynadot.com/domain/api-document)。

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

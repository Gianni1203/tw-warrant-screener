# 權證挑選器

台灣上市、上櫃權證篩選工具，純靜態網頁，部署在 GitHub Pages，資料由 GitHub Actions 每個交易日盤後自動更新，全部免費。

可篩選條件：

1. **差槓比**（價差比 ÷ 實際槓桿，越小越好）
2. **到期日**（剩餘天數）
3. **行使比例**
4. **價內外 %**（價內為正、價外為負）
5. **認購 / 認售**

另外還有標的搜尋、實際槓桿下限、上市/上櫃、需有報價、當日有成交等條件，結果可以點欄位標題排序。

## 架構

```
GitHub Actions（週一至週五 17:40 台灣時間）
  └─ scripts/fetch_warrants.py
       ├─ 證交所 OpenAPI：上市權證基本資料、上市個股收盤價
       ├─ 證交所每日收盤行情 MI_INDEX（0999 認購 / 0999P 認售）：權證買賣價
       ├─ 櫃買中心 OpenAPI：上櫃權證基本資料、上櫃權證行情、上櫃個股收盤價
       └─ 計算 IV、Delta、實際槓桿、價差比、差槓比 → data/warrants.json（commit 回 repo）
GitHub Pages
  └─ index.html + app.js 讀取 data/warrants.json，在瀏覽器篩選
```

權證相關 API 的路徑不寫死：程式會讀取交易所的 `swagger.json`，依中文說明找出「權證基本資料」「權證行情」等端點。

## 部署步驟

1. 在 GitHub 建立一個新的 repo（例如 `tw-warrant-screener`），把這個資料夾的內容 push 上去：
   ```bash
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<你的帳號>/tw-warrant-screener.git
   git push -u origin main
   ```
2. **Settings → Pages**：Source 選 `Deploy from a branch`，Branch 選 `main`、資料夾 `/ (root)`，按 Save。
3. **Settings → Actions → General**：最下面 Workflow permissions 選 `Read and write permissions`（讓 Actions 能 commit 資料）。
4. **Actions** 分頁 → 左側「更新權證資料」→ `Run workflow`，手動跑第一次。
5. 跑完後打開 `https://<你的帳號>.github.io/tw-warrant-screener/`。

repo 裡附的 `data/warrants.json` 是**示範資料**（假資料），頁面上會顯示提示；第一次 workflow 跑成功後就會換成真實資料。

## 計算方式

| 指標 | 公式 |
|---|---|
| 價內外 % | 認購 (S − K) / K；認售 (K − S) / K |
| 價差比 % | (賣價 − 買價) / 賣價 |
| 隱含波動率 | 以買賣中價 ÷ 行使比例，代入 Black-Scholes 用二分法反推 |
| Delta | Black-Scholes Delta（使用上面的 IV） |
| 實際槓桿 | \|Delta\| × 標的價 × 行使比例 ÷ 權證賣價 |
| 差槓比 | 價差比(%) ÷ 實際槓桿 |

S＝標的收盤價、K＝最新履約價、剩餘期間以履約截止日計算、無風險利率 1.5%（在 `scripts/pricing.py` 的 `RISK_FREE` 調整）、不考慮股利。
牛熊證不列入。以指數為標的的權證因為抓不到指數價格，指標會留空。

## 本機測試

```bash
pip install -r requirements.txt
python scripts/fetch_warrants.py          # 抓真實資料（需要能連到證交所）
python scripts/fetch_warrants.py --demo   # 產生示範資料
python -m http.server 8000                # 打開 http://localhost:8000
```

> 必須透過 http server 開啟，直接雙擊 `index.html` 會因為瀏覽器限制讀不到 JSON。

## 如果 workflow 失敗

打開該次執行的 log，`[fetch]` 開頭的訊息會寫出它找到哪些 API、各抓到幾筆。常見狀況：

- **找不到某個 API**：交易所改了 swagger 的說明文字，調整 `find_paths(...)` 的關鍵字即可。
- **所有權證都沒有買賣價**：欄位名稱改了，對照 log 調整 `pick(...)` 裡的欄位名稱。
- **連線逾時 / 被拒**：交易所偶爾會擋國外 IP 或限制頻率，可以重跑一次；長期被擋的話改成在自己電腦執行腳本後 push。
- **上櫃失敗**：程式會只輸出上市權證，不會讓整個更新失敗。

## 免責聲明

資料來自證交所、櫃買中心公開資料，計算結果僅供參考，不構成任何投資建議。

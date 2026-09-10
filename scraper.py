import os
import json
import time
import re
from datetime import datetime
import feedparser
import requests
from google import genai
from google.genai import types
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ==========================================
# ⚙️ 系統常數與環境配置
# ==========================================
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "YOUR_SPREADSHEET_ID_HERE")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 限速防護設定
GEMINI_MODEL = "gemini-3.6-flash"
RATE_LIMIT_DELAY = 12  # 每次呼叫強制間隔 12 秒 (每分鐘 <= 5 次請求)
MAX_RETRIES = 3        # 遇到 503 時最大重試次數

# 美食快篩關鍵字清單（英文、法文、義文、中文）
FOOD_KEYWORDS = [
    "restaurant", "bistrot", "bistro", "trattoria", "osteria", "pizzeria",
    "cuisine", "gastronomie", "dégustation", "chef", "menu", "carte",
    "michelin", "gault", "gambero", "slow food", "bar", "café", "pasticceria",
    "boulangerie", "vin", "vino", "gourmet", "food", "dining", "dish",
    "美食", "餐廳", "小吃", "必比登", "私房"
]

# ==========================================
# 🛡️ 輔助函式：本地雙重快篩
# ==========================================
def is_food_related(title: str, summary: str) -> bool:
    """第一道防線：確認標題或摘要是否包含餐飲美食關鍵字"""
    text = f"{title} {summary}".lower()
    return any(keyword in text for keyword in FOOD_KEYWORDS)

def match_target_city(region_key: str, title: str, summary: str, geo_registry: dict):
    """第二道防線：比對文章內文是否包含該大區在 _CONFIG_CITIES 定義的目標城鎮與關鍵字"""
    region_info = geo_registry.get(region_key, {})
    if not region_info:
        return None

    full_text = f"{title} {summary}".lower()

    for city_name, details in region_info.items():
        # 1. 檢查城市名稱本身
        if city_name.lower() in full_text:
            return city_name, details.get("tier", "")
        
        # 2. 檢查在試算表中設定的別名/地標關鍵字
        keywords = details.get("keywords", [])
        for kw in keywords:
            if kw.strip() and kw.strip().lower() in full_text:
                return city_name, details.get("tier", "")

    return None

# ==========================================
# 🤖 Gemini API 呼叫（具備退避重試與結構化輸出）
# ==========================================
def analyze_article_with_gemini(client, title: str, summary: str, target_city: str, region_key: str):
    """呼叫 Gemini 萃取店家資訊，具備 503 指數退避重試與 12s 間隔保護"""
    prompt = f"""
你是一位精通全球飲食與在地旅遊的米其林級美食特派員。
請分析以下文章，若文章內有推薦位於【{target_city}】（區域：{region_key}）的具體餐廳或小吃名店，請萃取出來。

【文章標題】：{title}
【文章內容摘要】：{summary}

請嚴格輸出 JSON 格式（不要加上額外 markdown 說明文字）：
{{
  "is_recommendation": true 或 false,
  "stores": [
    {{
      "name": "店家名稱",
      "category": "類別 (例如：Bistrot, Trattoria, 街頭小吃, 烘焙甜點)",
      "must_try": "必點菜色或特色招牌",
      "badge": "獲獎或認證標章 (如：米其林推薦, 慢食Presidia, 紅蝦評鑑，無則留空)",
      "tip": "造訪建議 (例如：需前三天預約, 僅收現金, 避開週一公休)",
      "address": "店家完整街道地址 (若文中無門牌，請盡可能推測「街名 + 城市名」，若無法確認填寫城鎮名)"
    }}
  ]
}}
若文章並非實質餐廳推薦、或找不到具體店名，請回傳 {{"is_recommendation": false, "stores": []}}。
"""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            
            # 強制休眠，保護免費 RPM/RPD 額度
            time.sleep(RATE_LIMIT_DELAY)
            
            # 清理 Markdown 代碼區塊標記並解析 JSON
            raw_text = response.text.strip() if response.text else ""
            clean_json = re.sub(r"^```json\s*", "", raw_text)
            clean_json = re.sub(r"\s*```$", "", clean_json).strip()
            
            if not clean_json:
                return {"is_recommendation": False, "stores": []}

            return json.loads(clean_json)

        except Exception as e:
            err_msg = str(e)
            print(f"   ⚠️ Gemini 呼叫異常 (嘗試 {attempt}/{MAX_RETRIES}): {err_msg}")
            
            # 若為 429 配額限制或 503 服務端忙碌，採用指數退避
            if "429" in err_msg or "503" in err_msg:
                if attempt < MAX_RETRIES:
                    backoff_time = attempt * 20
                    print(f"   ⏳ 觸發頻率限制或服務忙碌，暫停 {backoff_time} 秒後重試...")
                    time.sleep(backoff_time)
                else:
                    print("   ❌ 已達最大重試次數，略過此篇文章以確保爬蟲持續執行。")
                    return {"is_recommendation": False, "stores": []}
            else:
                # 其他非頻率性錯誤（例如連線中斷或格式解析失敗），小幅等待後重試或略過
                if attempt < MAX_RETRIES:
                    time.sleep(5)
                else:
                    return {"is_recommendation": False, "stores": []}

    # 關鍵修正：Python 布林值首字必須大寫 False
    return {"is_recommendation": False, "stores": []}
    
# ==========================================
# 📊 Google Sheets API 連線與寫入
# ==========================================
def get_sheets_service():
    """初始化 Google Sheets API 服務"""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            creds_dict, 
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    elif os.path.exists("credentials.json"):
        creds = Credentials.from_service_account_file(
            "credentials.json", 
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    else:
        print("⚠️ 未檢測到 Google 服務帳號憑證，將僅在終端機輸出結果而不寫入試算表。")
        return None

    return build("sheets", "v4", credentials=creds)

def append_to_sheet(sheets_service, spreadsheet_id, sheet_name, row_data):
    """將單筆美食資料追加至指定的試算表分頁"""
    if not sheets_service:
        return
    try:
        body = {"values": [row_data]}
        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:K",
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        print(f"   📥 [成功入庫] 已寫入分頁【{sheet_name}】：{row_data[3]} ({row_data[2]})")
    except Exception as e:
        print(f"   ❌ 寫入試算表分頁【{sheet_name}】失敗: {e}")

# ==========================================
# 🚀 爬蟲排程核心主迴圈
# ==========================================
def main():
    print("🚀 啟動 Private Food Radar 智慧定向爬蟲系統...")

    # 1. 載入組態檔
    if not os.path.exists("sources.json") or not os.path.exists("geo_registry.json"):
        print("❌ 找不到 sources.json 或 geo_registry.json，請先在 Google 試算表執行一鍵發布！")
        return

    with open("sources.json", "r", encoding="utf-8") as f:
        sources_data = json.load(f)

    with open("geo_registry.json", "r", encoding="utf-8") as f:
        geo_registry = json.load(f)

    # 2. 初始化 Gemini 與 Google Sheets 客戶端
    if not GEMINI_API_KEY:
        print("❌ 未設定 GEMINI_API_KEY 環境變數！")
        return

    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    sheets_service = get_sheets_service()

    total_scraped = 0
    total_accepted = 0

    # 3. 依大區分組執行爬取
    for region_key, feeds in sources_data.items():
        print(f"\n==========================================")
        print(f"📂 正在掃描大區情報：[{region_key}] (共有 {len(feeds)} 個來源)")
        print(f"==========================================")

        for feed_info in feeds:
            feed_name = feed_info.get("name", "未命名來源")
            feed_url = feed_info.get("url", "")
            print(f"\n📡 抓取頻道: {feed_name}")

            try:
                feed = feedparser.parse(feed_url)
            except Exception as e:
                print(f"   ❌ RSS 連線失敗，略過: {e}")
                continue

            # 每個來源處理最新 10 篇文章
            for entry in feed.entries[:10]:
                title = getattr(entry, "title", "").strip()
                summary = getattr(entry, "summary", "").strip()
                link = getattr(entry, "link", "").strip()

                if not title:
                    continue

                total_scraped += 1

                # 🛡️ 本地快篩 1：餐飲關鍵字過濾
                if not is_food_related(title, summary):
                    continue

                # 🛡️ 本地快篩 2：目標城市白名單過濾
                matched = match_target_city(region_key, title, summary, geo_registry)
                if not matched:
                    continue

                target_city, target_tier = matched
                print(f"\n🔍 [命中目標城鎮: {target_city} ({target_tier})] 文章: {title[:30]}...")

                # 🤖 送交 Gemini 進行語意萃取
                result = analyze_article_with_gemini(
                    gemini_client, title, summary, target_city, region_key
                )

                if result.get("is_recommendation") and result.get("stores"):
                    today_str = datetime.now().strftime("%Y-%m-%d")

                    for store in result["stores"]:
                        store_name = store.get("name", "").strip()
                        if not store_name:
                            continue

                        # 組裝符合 11 欄骨架的標準資料列
                        row_data = [
                            region_key,                  # A: 大區
                            target_tier,                 # B: 城市等級
                            target_city,                 # C: 城市/市鎮
                            store_name,                  # D: 店名
                            store.get("category", ""),   # E: 類別
                            store.get("must_try", ""),   # F: 必點招牌
                            store.get("badge", ""),      # G: 認證標章
                            store.get("tip", ""),        # H: 探訪秘訣
                            store.get("address", ""),    # I: 地址
                            link,                        # J: 來源連結
                            today_str                    # K: 抓取日期
                        ]

                        # 寫入 Google 試算表對應分頁
                        append_to_sheet(sheets_service, SPREADSHEET_ID, region_key, row_data)
                        total_accepted += 1

    print(f"\n🎉 爬蟲排程執行完畢！共掃描 {total_scraped} 篇文章，成功收錄 {total_accepted} 間新店家。")

if __name__ == "__main__":
    main()

import os
import json
import time
import re
from datetime import datetime
import feedparser
from google import genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ==========================================
# ⚙️ 系統常數與 24h 平攤節奏配置
# ==========================================
GEMINI_MODEL = "gemini-3.6-flash"

# 節奏與額度防護
RATE_LIMIT_DELAY = 45        # 每次呼叫間隔 45 秒 (約 1.3 RPM，遠低於 15 RPM 上限)
MAX_CALLS_PER_RUN = 8        # 每小時上限 8 次 (24 小時累計 192 次，確保不擊穿 200 RPD)
MAX_ENTRIES_PER_FEED = 10    # 每個 RSS 來源掃描最新 10 篇
MAX_RETRIES = 3              # 最大重試次數

# 檔案路徑與設定
CREDENTIALS_FILE = "credentials.json"
SOURCES_FILE = "sources.json"
GEO_REGISTRY_FILE = "geo_registry.json"

# 美食快篩關鍵字清單
FOOD_KEYWORDS = [
    "美食", "餐廳", "小吃", "甜點", "咖啡", "拉麵", "火鍋", "壽司", "燒肉",
    "居酒屋", "餐酒館", "排隊", "必吃", "料理", "早午餐", "私廚", "米其林",
    "ristorante", "trattoria", "osteria", "pizzeria", "bar", "caffè",
    "gelato", "cucina", "pasta", "pizza", "bistecca", "vino",
    "restaurant", "bistrot", "bistro", "gastronomie", "dégustation", "boulangerie"
]

def load_json_file(filepath):
    if not os.path.exists(filepath):
        print(f"❌ 找不到設定檔: {filepath}")
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def is_food_related(title, summary):
    content = f"{title} {summary}".lower()
    return any(keyword.lower() in content for keyword in FOOD_KEYWORDS)

def match_target_city(region_key: str, title: str, summary: str, geo_registry: dict):
    """
    通用城市快篩與大區自動歸屬模組：
    1. 若 region_key 結尾為 '_ALL'（如 TW_ALL, FR_ALL, IT_ALL），
       自動掃描 geo_registry 中該國家的所有實際大區分頁，實現全國性來源自動歸檔。
    2. 若為特定大區代碼（如 TW_Taipei, FR_Paris），維持單區精準快篩。
    回傳：(matched_region, city_name, tier)
    """
    full_text = f"{title} {summary}".lower()

    if region_key.endswith("_ALL"):
        country_prefix = region_key.split("_")[0] + "_"
        target_regions = [r for r in geo_registry.keys() if r.startswith(country_prefix) and not r.endswith("_ALL")]
    else:
        target_regions = [region_key] if region_key in geo_registry else []

    for reg in target_regions:
        region_data = geo_registry.get(reg, {})
        # 相容兩種設定檔格式（若包含頂層 "cities" 或直接列出城市字典）
        cities = region_data.get("cities", region_data)
        
        for city_name, city_info in cities.items():
            if not isinstance(city_info, dict):
                continue
            
            # 1. 優先檢查城市名稱本身
            if city_name.lower() in full_text:
                return reg, city_name, city_info.get("tier", "未分級")
            
            # 2. 檢查在試算表中設定的別名或商圈地標關鍵字 (keywords / aliases)
            aliases = city_info.get("keywords", []) or city_info.get("aliases", [])
            for alias in aliases:
                cleaned_alias = alias.strip().lower()
                if cleaned_alias and cleaned_alias in full_text:
                    return reg, city_name, city_info.get("tier", "未分級")

    return None, None, None

def analyze_article_with_gemini(client, title, summary, target_city, region_key):
    prompt = f"""
你是一個極度嚴格的跨國私房美食雷達。請分析以下 RSS 新聞/文章資訊：
【文章標題】：{title}
【文章摘要】：{summary}
【目標區域】：{region_key} - {target_city}

任務：
1. 判斷這篇文章是否在「明確推薦具體的特定實體餐廳/店家」給讀者？
2. 如果是通泛新聞、食品安全問題、集團財報、非餐廳促銷、沒有具體店名，一律回傳 "is_recommendation": false。
3. 若確實推薦了位於該目標城市或周邊的具體店家，請提取資訊。

請務必嚴格輸出合法的 JSON 格式：
{{
  "is_recommendation": true 或 false,
  "stores": [
    {{
      "name": "店家完整名稱",
      "category": "料理分類 (如: 燒鳥/義式餐酒館/手沖咖啡)",
      "must_try": "招牌或必點名物 (15字內)",
      "badge": "官方或特殊認證 (如: 米其林指南/必比登/500盤/老舖，無則留空)",
      "tip": "造訪秘訣 (如: 需提前一個月預約/僅收現金/露天景觀佳，20字內)",
      "address": "店家地址或具體鄰近地標/街區"
    }}
  ]
}}
"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "temperature": 0.2
                }
            )
            # 呼叫成功後執行節奏平攤冷卻
            time.sleep(RATE_LIMIT_DELAY)
            
            clean_text = response.text.strip()
            clean_text = re.sub(r"^```json\s*", "", clean_text)
            clean_text = re.sub(r"\s*```$", "", clean_text).strip()
            
            if not clean_text:
                return {"is_recommendation": False, "stores": []}

            return json.loads(clean_text)

        except Exception as e:
            err_msg = str(e)
            print(f"   ⚠️ Gemini 呼叫異常 (嘗試 {attempt}/{MAX_RETRIES}): {err_msg}")
            
            if "429" in err_msg or "503" in err_msg:
                if attempt < MAX_RETRIES:
                    # 指數退避起始調整為 45 秒
                    backoff_time = attempt * 45
                    
                    # 嘗試捕捉 Google 回應中的 retryDelay 建議值
                    delay_match = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)s", err_msg)
                    if delay_match:
                        backoff_time = max(backoff_time, int(delay_match.group(1)) + 5)
                        
                    print(f"   ⏳ 觸發頻率限制或服務忙碌，暫停 {backoff_time} 秒後重試...")
                    time.sleep(backoff_time)
                else:
                    print("   ❌ 已達最大重試次數，略過此篇文章以保護配額。")
                    return {"is_recommendation": False, "stores": []}
            else:
                if attempt < MAX_RETRIES:
                    time.sleep(5)
                else:
                    return {"is_recommendation": False, "stores": []}

    return {"is_recommendation": False, "stores": []}

def init_google_services():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    elif os.path.exists(CREDENTIALS_FILE):
        creds = Credentials.from_service_account_file(
            CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    else:
        raise ValueError("找不到 Google 服務帳號憑證。")

    sheets_service = build("sheets", "v4", credentials=creds)
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_api_key:
        raise ValueError("未設定環境變數 GEMINI_API_KEY。")

    gemini_client = genai.Client(api_key=gemini_api_key)
    return sheets_service, gemini_client

def append_to_sheet(service, spreadsheet_id, sheet_name, row_values):
    range_name = f"{sheet_name}!A:K"
    body = {"values": [row_values]}
    try:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()
        print(f"      ✅ 已成功回寫試算表 [{sheet_name}]: {row_values[3]}")
    except Exception as e:
        print(f"      ❌ 寫入試算表失敗: {e}")

def main():
    print("🚀 啟動美食雷達每小時定時掃描任務...")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        print("❌ 未設定 SPREADSHEET_ID。")
        return

    sources_data = load_json_file(SOURCES_FILE)
    geo_registry = load_json_file(GEO_REGISTRY_FILE)
    if not sources_data or not geo_registry:
        return

    try:
        sheets_service, gemini_client = init_google_services()
    except Exception as e:
        print(f"❌ 雲端服務初始化失敗: {e}")
        return

    gemini_call_count = 0
    circuit_broken = False
    total_scraped = 0
    total_accepted = 0

    for region_key, feeds in sources_data.items():
        if circuit_broken:
            break

        print(f"\n==========================================")
        print(f"📂 正在掃描大區情報：[{region_key}] (共有 {len(feeds)} 個來源)")
        print(f"==========================================")

        for feed_info in feeds:
            if circuit_broken:
                break

            feed_name = feed_info.get("name", "未命名來源")
            feed_url = feed_info.get("url", "")
            print(f"\n📡 抓取頻道: {feed_name}")

            try:
                feed = feedparser.parse(feed_url)
            except Exception as e:
                print(f"   ❌ RSS 連線失敗，略過: {e}")
                continue

            # 擴充讀取每個來源最新 10 篇文章
            for entry in feed.entries[:MAX_ENTRIES_PER_FEED]:
                title = getattr(entry, "title", "").strip()
                summary = getattr(entry, "summary", "").strip()
                link = getattr(entry, "link", "").strip()

                if not title:
                    continue

                total_scraped += 1

                # 本地快篩 1：餐飲美食關鍵字過濾 (0 額度消耗)
                if not is_food_related(title, summary):
                    continue

                # 本地快篩 2：動態大區與目標城市白名單判定 (0 額度消耗)
                matched_region, target_city, target_tier = match_target_city(region_key, title, summary, geo_registry)
                if not matched_region:
                    continue

                # 每小時額度熔斷保護
                if gemini_call_count >= MAX_CALLS_PER_RUN:
                    print(f"\n☕ 已達本小時安全分析上限 ({MAX_CALLS_PER_RUN} 次)，結束本輪，等待下個小時排程。")
                    circuit_broken = True
                    break

                gemini_call_count += 1
                print(f"\n🔍 [本輪進度 {gemini_call_count}/{MAX_CALLS_PER_RUN}] 命中大區 [{matched_region}] 城市 [{target_city}] 送審: {title[:30]}...")

                result = analyze_article_with_gemini(
                    gemini_client, title, summary, target_city, matched_region
                )

                if result.get("is_recommendation") and result.get("stores"):
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    for store in result["stores"]:
                        store_name = store.get("name", "").strip()
                        if not store_name:
                            continue

                        # 組裝標準資料列，A 欄使用動態判定的 matched_region
                        row_data = [
                            matched_region,              # A: 動態大區 (如 TW_South, FR_SudOuest)
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
                        
                        # 寫入試算表動態對應的目標分頁（而非固定寫入 region_key）
                        append_to_sheet(sheets_service, spreadsheet_id, matched_region, row_data)
                        total_accepted += 1

    print("\n==========================================")
    print(f"🏁 本輪掃描完成！")
    print(f"   總讀取文章數: {total_scraped}")
    print(f"   本輪 Gemini 呼叫數: {gemini_call_count}/{MAX_CALLS_PER_RUN}")
    print(f"   成功收錄店家數: {total_accepted}")
    print("==========================================")

if __name__ == "__main__":
    main()

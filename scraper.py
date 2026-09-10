import os
import json
import time
import feedparser
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# 1. 結構化資料定義
class FoodPlace(BaseModel):
    city: str = Field(description="城市或地區名稱，如：台北市中山區、Paris 11e、京都烏丸")
    name: str = Field(description="實體店面名稱")
    category: str = Field(description="店家料理類別，如：Bistrot、麵包坊、拉麵、小吃")
    must_try: str = Field(description="在地人必點或評鑑推薦的招牌菜品")
    badge: str = Field(description="獲得的認證，如：慢食協會推薦、500碗入選、Baguette 競賽首獎")
    tip: str = Field(description="探訪秘訣或避雷須知，如：需提前預約、僅收現金")
    address: str = Field(description="完整地址或最近的捷運/地鐵站與路口")

# 2. 本地關鍵字快篩清單（過濾無關新聞，省下 API 額度）
FOOD_KEYWORDS = [
    # 中文
    "餐廳", "小吃", "美食", "必吃", "招牌", "麵", "飯", "拉麵", "咖啡", "私廚", "甜點", "烘焙", "入選",
    # 法文
    "restaurant", "bistrot", "boulangerie", "croissant", "café", "pâtisserie", "chef", "table",
    # 義文
    "trattoria", "pizzeria", "osteria", "ristorante", "caffè", "pasta", "pizza", "gelato", "sagra",
    # 日文
    "ラーメン", "うどん", "そば", "食堂", "カフェ", "ベーカリー", "百名店", "新店", "名店"
]

def is_food_related(title: str, summary: str = "") -> bool:
    text = (title + " " + summary).lower()
    return any(keyword.lower() in text for keyword in FOOD_KEYWORDS)

# 3. 帶重試與退避的 Gemini 分析函式
def analyze_with_gemini(client, title: str, summary: str, source_name: str, max_retries: int = 3):
    system_instruction = (
        "你是一個極度嚴謹的美食雷達分析師。請分析以下美食文章或社群討論，篩選出具體推薦的實體餐飲店家。"
        "嚴格標準：\n"
        "1. 必須是明確推薦、具備職人水準、在地認證或特色招牌的實體店面。\n"
        "2. 若文章純屬新聞政策、人物訪談而未推薦具體實體好店，請回傳空陣列 []。\n"
        "3. 地址必須具備足夠辨識度以利地圖定位。"
    )

    prompt = f"來源：{source_name}\n標題：{title}\n內文摘要：{summary}\n\n請從中提取符合標準的好店清單。"

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=list[FoodPlace]
                )
            )
            data = json.loads(response.text)
            return data if isinstance(data, list) else []
        except Exception as e:
            err_msg = str(e)
            if ("429" in err_msg or "503" in err_msg) and attempt < max_retries:
                wait_time = attempt * 20  # 遇到頻率限制，梯次等待 20s、40s
                print(f"⚠️ 觸發配額限制或伺服器忙碌，等待 {wait_time} 秒後重試 (第 {attempt} 次)...")
                time.sleep(wait_time)
            else:
                print(f"Gemini 分析失敗: {e}")
                return []
    return []

# 4. 主執行流程
def main():
    gemini_key = os.environ.get("GEMINI_API_KEY")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    gcp_sa_key = os.environ.get("GCP_SA_KEY")

    if not all([gemini_key, spreadsheet_id, gcp_sa_key]):
        raise ValueError("環境變數未完整設定 (GEMINI_API_KEY, SPREADSHEET_ID, GCP_SA_KEY)")

    client = genai.Client(api_key=gemini_key)

    # 連線 Google Sheets
    sa_info = json.loads(gcp_sa_key)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_info, scope)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(spreadsheet_id)

    # 讀取訂閱清單 sources.json
    with open("sources.json", "r", encoding="utf-8") as f:
        sources_data = json.load(f)

    today_str = time.strftime("%Y-%m-%d")

    for country, feeds in sources_data.items():
        try:
            worksheet = spreadsheet.worksheet(country)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=country, rows="1000", cols="10")
            worksheet.append_row(["城市", "店名", "類別", "在地必點招牌", "公會/評鑑認證", "探訪秘訣", "地址", "來源連結", "抓取日期"])

        existing_names = set(worksheet.col_values(2)[1:])  # 避免重複寫入相同店名

        for feed_info in feeds:
            feed_name = feed_info["name"]
            feed_url = feed_info["url"]
            print(f"[{country}] 正在讀取 RSS: {feed_name}")

            try:
                parsed = feedparser.parse(feed_url)
            except Exception as e:
                print(f"RSS 解析失敗: {feed_name} -> {e}")
                continue

            # 抓取最新 3 篇
            for entry in parsed.entries[:3]:
                title = getattr(entry, "title", "")
                summary = getattr(entry, "summary", "")
                link = getattr(entry, "link", "")

                # 本地前置過濾：非美食文章直接跳過，省下 API 呼叫次數
                if not is_food_related(title, summary):
                    continue

                print(f"-> 正在以 Gemini 分析: {title}")
                places = analyze_with_gemini(client, title, summary, feed_name)

                # 頻率保護：每次呼叫後強制暫停 12 秒，將呼叫頻率控制在 ~5 RPM 以內
                time.sleep(12)

                rows_to_insert = []
                for p in places:
                    if p["name"] in existing_names:
                        continue
                    rows_to_insert.append([
                        p.get("city", ""),
                        p.get("name", ""),
                        p.get("category", ""),
                        p.get("must_try", ""),
                        p.get("badge", ""),
                        p.get("tip", ""),
                        p.get("address", ""),
                        link,
                        today_str
                    ])
                    existing_names.add(p["name"])

                if rows_to_insert:
                    worksheet.append_rows(rows_to_insert)
                    print(f"   已成功寫入 {len(rows_to_insert)} 間好店至 [{country}] 分頁！")

if __name__ == "__main__":
    main()

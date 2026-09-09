import os
import json
import datetime
import feedparser
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types

# ================= 全域設定與來源清單 =================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GCP_SA_KEY = os.environ.get("GCP_SA_KEY")

# 定義各國標準標題列
HEADERS_CONFIG = {
    "France": ["城市", "店名", "類別", "在地必點招牌", "公會/評鑑認證", "探訪秘訣", "地址", "來源連結", "抓取日期"],
    "Italy": ["城市", "店名", "類別", "在地必點招牌", "慢食/紅蝦認證", "探訪秘訣", "地址", "來源連結", "抓取日期"],
    "Japan": ["城市/區域", "店名", "類別", "在地必點招牌", "Tabelog/雜誌認證", "探訪秘訣", "鄰近車站/地址", "來源連結", "抓取日期"],
    "Taiwan": ["縣市/區域", "店名", "類別", "在地必點招牌", "500碗盤/名老店", "探訪秘訣", "地址/所在市場", "來源連結", "抓取日期"]
}

def get_or_create_worksheet(sh, country):
    try:
        return sh.worksheet(country)
    except gspread.exceptions.WorksheetNotFound:
        # 分頁不存在時自動建立，並寫入該國專屬標題列
        ws = sh.add_worksheet(title=country, rows=100, cols=10)
        headers = HEADERS_CONFIG.get(country, ["城市", "店名", "類別", "在地必點招牌", "認證", "探訪秘訣", "地址", "來源連結", "抓取日期"])
        ws.append_row(headers)
        return ws

RSS_FEEDS = [
    # --- 法國 ---
    {"country": "France", "name": "Le Fooding", "url": "https://lefooding.com/feed"},
    {"country": "France", "name": "Gault & Millau Actu", "url": "https://fr.gaultmillau.com/fr/rss/news"},
    {"country": "France", "name": "Le Figaro Restos & Palmarès", "url": "https://www.lefigaro.fr/rss/figaro_gastronomie.xml"},
    {"country": "France", "name": "Time Out Paris", "url": "https://www.timeout.fr/paris/fr/feed"},
    {"country": "France", "name": "My Little Paris", "url": "https://www.mylittleparis.com/feed"},
    {"country": "France", "name": "France Inter (On va déguster)", "url": "https://radiofrance-podcast.net/podcast09/rss_11568.xml"},
    {"country": "France", "name": "Très Très Bon", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC6P01x66w1o1Fk1vL-bEwEg"},
    {"country": "France", "name": "La Meilleure Boulangerie (M6)", "url": "https://news.google.com/rss/search?q=La+Meilleure+Boulangerie+de+France+gagnant+when:7d&hl=fr&gl=FR&ceid=FR:fr"},
    {"country": "France", "name": "Concours Baguette & Croissant", "url": "https://news.google.com/rss/search?q=(Grand+Prix+Baguette+Tradition+OR+Meilleur+Croissant+au+Beurre)+Paris+when:14d&hl=fr&gl=FR&ceid=FR:fr"},
    {"country": "France", "name": "Sud Ouest (Gastronomie)", "url": "https://www.sudouest.fr/culture-et-loisirs/gastronomie/rss.xml"},
    {"country": "France", "name": "Ouest-France (Saveurs & Terroir)", "url": "https://news.google.com/rss/search?q=site:ouest-france.fr+(restaurant+OR+creperie+OR+boulangerie)+when:14d&hl=fr&gl=FR&ceid=FR:fr"},
    {"country": "France", "name": "Le Progrès (Sorties & Resto)", "url": "https://news.google.com/rss/search?q=site:leprogres.fr+(restaurant+OR+bouchon+OR+boulangerie)+when:14d&hl=fr&gl=FR&ceid=FR:fr"},
    {"country": "France", "name": "Reddit Paris Food", "url": "https://www.reddit.com/r/paris/search.rss?q=restaurant+OR+boulangerie&sort=new&restrict_sr=on"},
    {"country": "France", "name": "Reddit France Food", "url": "https://www.reddit.com/r/france/search.rss?q=resto+OR+boulangerie&sort=new&restrict_sr=on"},

    # --- 義大利 ---
    {"country": "Italy", "name": "Slow Food News", "url": "https://www.slowfood.it/feed/"},
    {"country": "Italy", "name": "Gambero Rosso Storie", "url": "https://www.gamberorosso.it/feed/"},
    {"country": "Italy", "name": "50 Top Pizza & Italy", "url": "https://news.google.com/rss/search?q=(50+Top+Pizza+OR+50+Top+Italy)+classifica+when:14d&hl=it&gl=IT&ceid=IT:it"},
    {"country": "Italy", "name": "Accademia Italiana Cucina", "url": "https://news.google.com/rss/search?q=site:accademiaitalianadellacucina.it+ristoranti+when:30d&hl=it&gl=IT&ceid=IT:it"},
    {"country": "Italy", "name": "Dissapore", "url": "https://www.dissapore.com/feed/"},
    {"country": "Italy", "name": "Puntarella Rossa", "url": "https://www.puntarellarossa.it/feed/"},
    {"country": "Italy", "name": "Scatti di Gusto", "url": "https://www.scattidigusto.it/feed/"},
    {"country": "Italy", "name": "Agrodolce", "url": "https://www.agrodolce.it/feed/"},
    {"country": "Italy", "name": "Luciano Pignataro Blog", "url": "https://www.lucianopignataro.it/feed/"},
    {"country": "Italy", "name": "AVPN Vera Pizza Napoletana", "url": "https://news.google.com/rss/search?q=Vera+Pizza+Napoletana+AVPN+when:14d&hl=it&gl=IT&ceid=IT:it"},
    {"country": "Italy", "name": "Sagre e Feste d'Italia", "url": "https://news.google.com/rss/search?q=(Sagra+del+OR+Festa+del)+quando+dove+when:7d&hl=it&gl=IT&ceid=IT:it"},
    {"country": "Italy", "name": "Pesto & Focaccia di Recco", "url": "https://news.google.com/rss/search?q=(Campionato+Mondiale+Pesto+OR+Focaccia+di+Recco+Consorzio)+when:30d&hl=it&gl=IT&ceid=IT:it"},

    # --- 日本 ---
    {"country": "Japan", "name": "dancyu WEB", "url": "https://dancyu.jp/feed"},
    {"country": "Japan", "name": "おとなの週末", "url": "https://otonano-shumatsu.com/feed"},
    {"country": "Japan", "name": "散步達人 (San-tatsu)", "url": "https://san-tatsu.jp/feed/"},
    {"country": "Japan", "name": "Hanako (Bread & Cafe)", "url": "https://hanako.tokyo/feed/"},
    {"country": "Japan", "name": "Tabelog 百名店速報", "url": "https://news.google.com/rss/search?q=site:tabelog.com+(百名店+発表)+when:30d&hl=ja&gl=JP&ceid=JP:ja"},
    {"country": "Japan", "name": "Reddit Tokyo Food", "url": "https://www.reddit.com/r/tokyo/search.rss?q=kissaten+OR+restaurant+OR+bakery&sort=new&restrict_sr=on"},

    # --- 台灣 ---
    {"country": "Taiwan", "name": "500輯", "url": "https://500times.udn.com/rss/news/1007"},
    {"country": "Taiwan", "name": "500碗/500盤 追蹤", "url": "https://news.google.com/rss/search?q=(500碗+OR+500盤)+完整名單+when:30d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"},
    {"country": "Taiwan", "name": "鳴人堂 飲食文化", "url": "https://opinion.udn.com/rss/tag/飲食"},
    {"country": "Taiwan", "name": "旅飯 Pantravel", "url": "https://pantravel.life/feed/"},
    {"country": "Taiwan", "name": "鏡食旅", "url": "https://www.mirrormedia.mg/rss/category/foodtravel.xml"},
    {"country": "Taiwan", "name": "Reddit Taiwan Food", "url": "https://www.reddit.com/r/taiwan/search.rss?q=breakfast+OR+restaurant+OR+food+stall&sort=new&restrict_sr=on"}
]

# ================= 初始化 Gemini API 客戶端 =================
client = genai.Client(api_key=GEMINI_API_KEY)

def analyze_with_gemini(country: str, title: str, content: str):
    country_rules = {
        "France": "優先收錄：手工長棍冠軍（Baguette de tradition）、奶油可頌冠軍、Levain酸種、Le Fooding、Gault & Millau、On va déguster、地方日報副刊（Sud Ouest, Ouest-France）、ASOM美乃滋蛋、5A肉腸。排除美式早午餐與全日供餐觀光店。",
        "Italy": "優先收錄：慢食小蝸牛（Chiocciola）、紅蝦三隻蝦/三條麵包、50 Top Pizza、AVPN披薩、老烤爐（Forno）、Maritozzo生乳包、Sagra鄉村節慶。排除夏威夷披薩、雞肉義大利麵與觀光連鎖店。",
        "Japan": "優先收錄：純喫茶Morning服務、國產小麥天然酵母麵包、Tabelog 3.5分以上與百名店、町中華、十割蕎麥、日替わり定食。排除連鎖居酒屋與網紅打卡店。",
        "Taiwan": "優先收錄：手工厚燒餅夾蛋、粉漿蛋餅、老市場晨間熱湯（溫體牛肉湯、黑白切米粉湯）、500碗小吃、500盤推薦單道菜、老麵麵包。排除連鎖加盟早午餐與夜市半成品。"
    }

    system_instruction = f"""你是一位精通法、義、日、中多國常民飲食文化的資深獨立食評家。
任務：評估文章推薦的實體店家是否符合「在地熟客喜愛、非觀光打卡導向」。

【{country} 專屬標準】
{country_rules.get(country, "")}

【輸出規定】
僅萃取符合標準的實體店家，輸出為 JSON 陣列。若無符合者請回傳 []。"""

    prompt = f"文章標題：{title}\n文章內容：{content[:3500]}"

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=list[dict]
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Gemini 分析失敗: {e}")
        return []

# ================= 試算表操作 =================
def setup_google_sheet():
    creds_info = json.loads(GCP_SA_KEY)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID)

def main():
    sh = setup_google_sheet()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    for feed in RSS_FEEDS:
        country = feed["country"]
        name = feed["name"]
        url = feed["url"]

        try:
            worksheet = sh.worksheet(country)
        except gspread.exceptions.WorksheetNotFound:
            print(f"工作表 {country} 不存在，跳過。")
            continue

        # 讀取第 8 欄（來源連結）進行去重
        existing_urls = set(worksheet.col_values(8)[1:])

        print(f"[{country}] 正在讀取 RSS: {name}")
        parsed_feed = feedparser.parse(url)

        for entry in parsed_feed.entries[:2]:
            link = getattr(entry, "link", "").strip()
            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "")

            if not link or link in existing_urls:
                continue

            print(f"-> 正在以 Gemini 分析: {title}")
            places = analyze_with_gemini(country, title, summary)

            if places and isinstance(places, list):
                rows_to_append = []
                for p in places:
                    row = [
                        p.get("city", ""),
                        p.get("name", ""),
                        p.get("category", ""),
                        p.get("must_try", ""),
                        p.get("badge", ""),
                        p.get("tip", ""),
                        p.get("address", ""),
                        link,
                        today_str
                    ]
                    rows_to_append.append(row)
                
                if rows_to_append:
                    worksheet.append_rows(rows_to_append)
                    print(f"   已成功寫入 {len(rows_to_append)} 間好店至 [{country}] 分頁！")
                    existing_urls.add(link)

if __name__ == "__main__":
    main()

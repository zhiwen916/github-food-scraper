import json
import requests
import feedparser

def verify_feed(feed_name, url):
    try:
        resp = requests.get(
            url, 
            timeout=10, 
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        
        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            return False, "無效的 XML/RSS 格式"
        
        if len(parsed.entries) == 0:
            return False, "RSS 內容為空"
            
        latest_title = parsed.entries[0].get("title", "無標題")
        return True, f"正常 (最新文章: {latest_title[:25]}...)"
    except Exception as e:
        return False, str(e)

def main():
    try:
        with open("sources.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("❌ 找不到 sources.json，請先在試算表執行一鍵同步！")
        return

    print("🚀 開始體檢 sources.json 中的情報來源...")
    for region, feeds in data.items():
        print(f"\n📂 檢查大區: [{region}]")
        for feed in feeds:
            ok, msg = verify_feed(feed["name"], feed["url"])
            mark = "✅" if ok else "❌"
            print(f" {mark} {feed['name']}: {msg}")

if __name__ == "__main__":
    main()

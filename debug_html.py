import requests
from bs4 import BeautifulSoup

url = "https://www.espn.com/mlb/schedule/_/date/20260331"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

resp = requests.get(url, headers=headers, timeout=30)
print(f"Status: {resp.status_code}")
print(f"Content length: {len(resp.text)}")

soup = BeautifulSoup(resp.text, 'html.parser')

# Find all tables
tables = soup.find_all('table')
print(f"\nFound {len(tables)} tables")

# Look for game rows with odds
target_tr = None
for table in tables:
    rows = table.find_all('tr')
    for row in rows:
        tds = row.find_all('td')
        if len(tds) >= 4:  # game rows usually have several tds
            target_tr = row
            break
    if target_tr:
        break

if target_tr:
    print("\n=== FIRST GAME ROW RAW HTML ===")
    print(target_tr.prettify())

    tds = target_tr.find_all('td')
    print(f"\nNumber of tds in row: {len(tds)}")

    if len(tds) > 1:
        print("\n=== tds[1].find_all('a') ===")
        anchors = tds[1].find_all('a')
        print(f"Found {len(anchors)} anchors")
        for i, a in enumerate(anchors):
            print(f"  [{i}] text={repr(a.get_text(strip=True))}, href={repr(a.get('href'))}")

    # Also show all tds briefly
    print("\n=== All TDs in this row ===")
    for i, td in enumerate(tds):
        print(f"  td[{i}]: class={td.get('class')}, text={repr(td.get_text(strip=True)[:80])}")
else:
    print("\nNo game rows found! Looking at raw page structure...")
    # Show first 3000 chars
    print(resp.text[:3000])

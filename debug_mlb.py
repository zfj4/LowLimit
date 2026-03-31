import sys
sys.path.insert(0, 'c:/GitHub/LowLimit')

import django
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'lowlimit.settings'
django.setup()

from datetime import date
from betting.utils import scrape_espn_schedule

today = date(2026, 3, 31)
games = scrape_espn_schedule(today, today)
mlb_games = [g for g in games if g['gender'] == 'B']
print(f"Total games: {len(games)}, MLB games: {len(mlb_games)}")
for g in mlb_games[:5]:
    print(g)

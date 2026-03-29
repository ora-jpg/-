"""
סוכן זמינות טיסות — ארקיע, ישראיר, אל על
==========================================
בודק כל דקה אם טיסות שהיו מלאות נפתחו
TLV → אירופה | 29.3–3.4.2026
התראות ב-WhatsApp דרך Twilio
"""

import os
import asyncio
import json
import logging
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from playwright.async_api import async_playwright
import requests

# ============================================================
# הגדרות
# ============================================================
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN",  "")
TWILIO_FROM        = os.environ.get("TWILIO_FROM_NUMBER", "whatsapp:+14155238886")
TWILIO_TO          = os.environ.get("TWILIO_TO_NUMBER",   "whatsapp:+972XXXXXXXXX")

DATE_FROM          = os.environ.get("DATE_FROM", "2026-03-29")
DATE_TO            = os.environ.get("DATE_TO",   "2026-04-03")
SCAN_INTERVAL_SEC  = int(os.environ.get("SCAN_INTERVAL_SEC", "60"))

STATE_FILE = Path("/tmp/availability_state.json")

# יעדי אירופה נפוצים מישראל
EUROPE_DESTINATIONS = [
    "ATH",  # אתונה
    "FCO",  # רומא
    "BCN",  # ברצלונה
    "CDG",  # פריז
    "LHR",  # לונדון
    "AMS",  # אמסטרדם
    "FRA",  # פרנקפורט
    "MUC",  # מינכן
    "VIE",  # וינה
    "ZRH",  # ציריך
    "BRU",  # בריסל
    "MAD",  # מדריד
    "LIS",  # ליסבון
    "PRG",  # פראג
    "BUD",  # בודפשט
    "WAW",  # ורשה
    "CPH",  # קופנהגן
    "ARN",  # שטוקהולם
    "OSL",  # אוסלו
    "HEL",  # הלסינקי
    "SKG",  # סלוניקי
    "RHO",  # רודוס
    "HER",  # כרתים
    "MLA",  # מלטה
    "CAG",  # סרדיניה
    "PMI",  # מיורקה
    "NCE",  # ניס
    "MRS",  # מרסיי
    "LYS",  # ליון
    "TLS",  # טולוז
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ============================================================
# WhatsApp
# ============================================================
def send_whatsapp(message: str):
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
            data={"From": TWILIO_FROM, "To": TWILIO_TO, "Body": message},
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=10,
        )
        r.raise_for_status()
        log.info("✅ WhatsApp נשלח")
    except Exception as e:
        log.error(f"❌ WhatsApp נכשל: {e}")


# ============================================================
# מצב שמור
# ============================================================
def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}

def save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
    except Exception as e:
        log.error(f"שגיאת שמירה: {e}")

def flight_key(airline: str, date: str, dest: str) -> str:
    return hashlib.md5(f"{airline}|{date}|{dest}".encode()).hexdigest()


# ============================================================
# גרידת ארקיע
# ============================================================
async def check_arkia(page, date: str, dest: str) -> str:
    """
    מחזיר: 'available' | 'sold_out' | 'no_flight' | 'error'
    """
    try:
        url = f"https://www.arkia.com/flights/tlv/{dest.lower()}/{date}/1/0/0/Economy"
        await page.goto(url, timeout=20000, wait_until="networkidle")
        await page.wait_for_timeout(3000)

        # בדיקת "אזל"
        sold_out = await page.query_selector_all("text=אזל")
        if sold_out:
            return "sold_out"

        # בדיקת מחיר — אם יש מחיר, הטיסה זמינה
        prices = await page.query_selector_all(".price, [class*='price']")
        if prices:
            return "available"

        return "no_flight"
    except Exception as e:
        log.warning(f"ארקיע {dest} {date}: {e}")
        return "error"


# ============================================================
# גרידת ישראיר
# ============================================================
async def check_israir(page, date: str, dest: str) -> str:
    try:
        formatted_date = datetime.strptime(date, "%Y-%m-%d").strftime("%d/%m/%Y")
        url = f"https://www.israir.co.il/flight-search?origin=TLV&destination={dest}&departureDate={formatted_date}&passengers=1&tripType=ONE_WAY"
        await page.goto(url, timeout=20000, wait_until="networkidle")
        await page.wait_for_timeout(4000)

        # בדיקת "טיסה מלאה"
        full = await page.query_selector_all("text=טיסה מלאה")
        if full:
            # בדיקה אם יש גם טיסות פנויות
            available = await page.query_selector_all("text=הזמן")
            if available:
                return "partial"  # חלק מלא, חלק פנוי
            return "sold_out"

        available = await page.query_selector_all("text=הזמן")
        if available:
            return "available"

        return "no_flight"
    except Exception as e:
        log.warning(f"ישראיר {dest} {date}: {e}")
        return "error"


# ============================================================
# גרידת אל על
# ============================================================
async def check_elal(page, date: str, dest: str) -> str:
    try:
        formatted_date = datetime.strptime(date, "%Y-%m-%d").strftime("%d/%m/%Y")
        url = f"https://www.elal.com/he-IL/Israel/Pages/ResultsPage.aspx?origin=TLV&destination={dest}&outboundDate={formatted_date}&adult=1&child=0&infant=0&tripType=ONE_WAY"
        await page.goto(url, timeout=25000, wait_until="networkidle")
        await page.wait_for_timeout(4000)

        # בדיקת "המידע אינו זמין"
        unavailable = await page.query_selector_all("text=המידע אינו זמין")
        if unavailable:
            return "sold_out"

        # בדיקת כפתור הזמנה פעיל
        book_btn = await page.query_selector_all("text=להזמנת טיסה")
        if book_btn:
            return "available"

        book_btn2 = await page.query_selector_all("text=בזמן")
        if book_btn2:
            return "available"

        return "no_flight"
    except Exception as e:
        log.warning(f"אל על {dest} {date}: {e}")
        return "error"


# ============================================================
# סריקה ראשית
# ============================================================
async def scan():
    log.info("🔍 סריקה מתחילה...")
    state = load_state()
    alerts = []

    # בנה רשימת תאריכים
    start = datetime.strptime(DATE_FROM, "%Y-%m-%d")
    end   = datetime.strptime(DATE_TO,   "%Y-%m-%d")
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="he-IL",
        )

        airlines = [
            ("ארקיע ✈️",   "IZ", check_arkia),
            ("ישראיר 🛫",  "6H", check_israir),
            ("אל על 🌍",   "LY", check_elal),
        ]

        for date in dates:
            for airline_name, iata, check_fn in airlines:
                for dest in EUROPE_DESTINATIONS:
                    key = flight_key(iata, date, dest)
                    prev_status = state.get(key, "unknown")

                    page = await context.new_page()
                    try:
                        current_status = await check_fn(page, date, dest)
                    finally:
                        await page.close()

                    log.info(f"{airline_name} {dest} {date}: {prev_status} → {current_status}")

                    # עדכון מצב
                    state[key] = current_status

                    # התראה: הטיסה הייתה מלאה ועכשיו פנויה!
                    if prev_status == "sold_out" and current_status == "available":
                        alerts.append({
                            "airline": airline_name,
                            "iata": iata,
                            "date": date,
                            "dest": dest,
                        })

                    await asyncio.sleep(2)  # עיכוב בין בקשות

        await browser.close()

    save_state(state)

    if alerts:
        for a in alerts:
            msg = (
                f"🚨 *התפנה מקום!*\n"
                f"{'─' * 25}\n"
                f"🏢 {a['airline']}\n"
                f"🗓 {a['date']}\n"
                f"🛫 TLV → {a['dest']}\n"
                f"⚡ הטיסה הייתה מלאה ועכשיו פנויה!\n"
                f"היכנסי עכשיו להזמין! ⏰"
            )
            send_whatsapp(msg)
            log.info(f"🚨 התראה נשלחה: {a['airline']} {a['dest']} {a['date']}")
    else:
        log.info("אין שינויים בזמינות.")


# ============================================================
# לולאה ראשית
# ============================================================
async def main():
    log.info("🚀 סוכן זמינות טיסות מופעל!")
    send_whatsapp(
        "🤖 *סוכן זמינות טיסות מופעל!*\n"
        "בודק ארקיע, ישראיר ואל על\n"
        "TLV → אירופה | 29.3–3.4\n"
        "אתריע כשטיסה מלאה תיפתח ✈️"
    )

    while True:
        await scan()
        log.info(f"⏱ ממתין {SCAN_INTERVAL_SEC} שניות...")
        await asyncio.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(main())

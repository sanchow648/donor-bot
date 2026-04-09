import time
import os
from playwright.sync_api import sync_playwright
import requests

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

LOGIN = os.getenv("LOGIN")
PASSWORD = os.getenv("PASSWORD")

def send_message(text):
    requests.get(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": text}
    )

def check_slots():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto("https://donor-mos.online/", timeout=60000)

            page.click("text=Войти")

            page.fill('input[type="text"]', LOGIN)
            page.fill('input[type="password"]', PASSWORD)

            page.click("button:has-text('Войти')")

            page.wait_for_timeout(5000)

            page.goto("https://donor-mos.online/booking/", timeout=60000)

            page.wait_for_timeout(5000)

            content = page.content().lower()

            browser.close()

            if "нет доступных" not in content:
                return True

            return False

        except:
            browser.close()
            return False


print("Бот запущен...")
send_message("✅ Бот запущен и работает!")

last_state = False

while True:
    current_state = check_slots()

    if current_state and not last_state:
        send_message("🔥 Появились слоты на тромбоциты под тебя!")

    last_state = current_state

    time.sleep(60)

import time
import os
import requests
from playwright.sync_api import sync_playwright


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOGIN = os.getenv("LOGIN")
PASSWORD = os.getenv("PASSWORD")


def send_message(text):
    requests.get(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": text},
        timeout=15
    )


def safe_close_popup(page):
    selectors = [
        'button:has-text("×")',
        'text=×',
        '[aria-label="Close"]',
        '[aria-label="Закрыть"]',
    ]

    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=1000)
            page.wait_for_timeout(500)
            return
        except:
            pass

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except:
        pass


def check_slots():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # Открываем сайт и логинимся
            page.goto("https://donor-mos.online/", timeout=60000)
            page.wait_for_timeout(2000)

            page.fill('input[type="text"]', LOGIN)
            page.fill('input[type="password"]', PASSWORD)
            page.click("button:has-text('Авторизоваться')")
            page.wait_for_timeout(5000)

            # Открываем личный кабинет
            page.goto("https://donor-mos.online/account/", timeout=60000)
            page.wait_for_timeout(4000)

            buttons = page.locator("text=Забронировать время")
            count = buttons.count()

            print(f"Найдено кнопок: {count}", flush=True)

            if count == 0:
                browser.close()
                return False

            for i in range(count):
                page.goto("https://donor-mos.online/account/", timeout=60000)
                page.wait_for_timeout(3000)

                buttons = page.locator("text=Забронировать время")
                current_count = buttons.count()

                if i >= current_count:
                    continue

                print(f"Проверяю кнопку #{i + 1}", flush=True)

                try:
                    buttons.nth(i).click(timeout=5000)
                except Exception as e:
                    print(f"Не удалось кликнуть по кнопке #{i + 1}: {e}", flush=True)
                    continue

                page.wait_for_timeout(3000)

                popup_text = page.content().lower()

                # Если явно показано, что свободных мест нет — идём дальше
                if "все свободные места для записи закончились" in popup_text:
                    print(f"Кнопка #{i + 1}: мест нет", flush=True)
                    safe_close_popup(page)
                    continue

                # Если текста про отсутствие мест нет — считаем, что слот появился
                print(f"Кнопка #{i + 1}: похоже, слот есть", flush=True)
                browser.close()
                return True

            browser.close()
            return False

        except Exception as e:
            print(f"Ошибка в check_slots: {e}", flush=True)
            try:
                browser.close()
            except:
                pass
            return False


print("БОТ ЗАПУЩЕН", flush=True)

last_state = False

while True:
    current_state = check_slots()

    if current_state and not last_state:
        send_message("🔥 Похоже, появился слот. Срочно зайди в donor-mos и попробуй записаться.")

    last_state = current_state

    time.sleep(60)

import time
import os
import re
import requests
from playwright.sync_api import sync_playwright


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOGIN = os.getenv("LOGIN")
PASSWORD = os.getenv("PASSWORD")

ACCOUNT_URL = "https://donor-mos.online/account/"


def log(message):
    print(message, flush=True)


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


def normalize_spaces(text):
    return re.sub(r"\s+", " ", text).strip()


def extract_dates_from_text(text):
    matches = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", text)
    return matches


def get_block_text_for_button(page, index):
    try:
        button = page.locator("text=Забронировать время").nth(index)
        # Берем более широкий контейнер вокруг кнопки
        block = button.locator("xpath=ancestor::div[1]")
        text = block.inner_text(timeout=2000)
        return normalize_spaces(text)
    except:
        return ""


def get_button_date(page, index):
    block_text = get_block_text_for_button(page, index)
    dates = extract_dates_from_text(block_text)

    if dates:
        return dates[0]

    return f"кнопка #{index + 1}"


def check_slots():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto("https://donor-mos.online/", timeout=60000)
            page.wait_for_timeout(2000)

            page.fill('input[type="text"]', LOGIN)
            page.fill('input[type="password"]', PASSWORD)
            page.click("button:has-text('Авторизоваться')")
            page.wait_for_timeout(5000)

            page.goto(ACCOUNT_URL, timeout=60000)
            page.wait_for_timeout(4000)

            buttons = page.locator("text=Забронировать время")
            count = buttons.count()

            log(f"Найдено кнопок: {count}")

            if count == 0:
                browser.close()
                return False, None

            for i in range(count):
                page.goto(ACCOUNT_URL, timeout=60000)
                page.wait_for_timeout(3000)

                buttons = page.locator("text=Забронировать время")
                current_count = buttons.count()

                if i >= current_count:
                    continue

                button_date = get_button_date(page, i)
                log(f"Проверяю {button_date}")

                try:
                    buttons.nth(i).click(timeout=5000)
                except Exception as e:
                    log(f"Не удалось кликнуть по {button_date}: {e}")
                    continue

                page.wait_for_timeout(3000)

                popup_text = page.content().lower()

                if "все свободные места для записи закончились" in popup_text:
                    log(f"{button_date}: мест нет")
                    safe_close_popup(page)
                    continue

                log(f"{button_date}: похоже, слот есть")
                browser.close()
                return True, button_date

            browser.close()
            return False, None

        except Exception as e:
            log(f"Ошибка в check_slots: {e}")
            try:
                browser.close()
            except:
                pass
            return False, None


log("БОТ ЗАПУЩЕН")

last_state = False
last_slot_label = None

while True:
    current_state, slot_label = check_slots()

    if current_state and not last_state:
        if slot_label:
            send_message(
                f"🔥 Похоже, появился слот на дату {slot_label}.\n"
                f"Срочно зайди в donor-mos и попробуй записаться."
            )
        else:
            send_message(
                "🔥 Похоже, появился слот.\n"
                "Срочно зайди в donor-mos и попробуй записаться."
            )

    last_state = current_state
    last_slot_label = slot_label

    log("Жду 30 секунд до следующей проверки...")
    time.sleep(30)

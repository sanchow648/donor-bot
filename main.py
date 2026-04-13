import time
import os
import json
import re
import requests
import multiprocessing
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOGIN = os.getenv("LOGIN")
PASSWORD = os.getenv("PASSWORD")
STORAGE_STATE_JSON = os.getenv("STORAGE_STATE_JSON")

ACCOUNT_URL = "https://donor-mos.online/account/"
LOGIN_URL = "https://donor-mos.online/"
RUNTIME_STATE_FILE = "/tmp/donor_runtime_state.json"

CHECK_TIMEOUT_SECONDS = 180
SLEEP_BETWEEN_CHECKS_SECONDS = 30

MOSCOW_TZ = timezone(timedelta(hours=3))
HEARTBEAT_HOURS = {11, 23}
ERROR_STREAK_FOR_ALERT = 3
RESTART_AFTER_TIMEOUT_STREAK = 3


def now_moscow():
    return datetime.now(MOSCOW_TZ)


def log(message):
    print(message, flush=True)


def send_message(text):
    try:
        requests.get(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            params={"chat_id": CHAT_ID, "text": text},
            timeout=15
        )
    except Exception as e:
        log(f"Ошибка отправки в Telegram: {e}")


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
            page.wait_for_timeout(300)
            return
        except Exception:
            pass

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass


def page_shows_login_form(page):
    html = page.content().lower()
    return "авторизоваться" in html and "пароль" in html


def save_context_state(context):
    try:
        context.storage_state(path=RUNTIME_STATE_FILE)
    except Exception:
        pass


def seed_runtime_state_from_env():
    if os.path.exists(RUNTIME_STATE_FILE):
        return

    if STORAGE_STATE_JSON:
        try:
            state_data = json.loads(STORAGE_STATE_JSON)
            with open(RUNTIME_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state_data, f)
        except Exception:
            pass


def login_and_refresh_session(context):
    if not LOGIN or not PASSWORD:
        raise RuntimeError("Нет LOGIN/PASSWORD")

    page = context.new_page()
    page.set_default_timeout(20000)

    page.goto(LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    page.fill('input[type="text"]', LOGIN)
    page.fill('input[type="password"]', PASSWORD)
    page.click("button:has-text('Авторизоваться')")
    page.wait_for_timeout(5000)

    page.goto(ACCOUNT_URL, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    if page_shows_login_form(page):
        raise RuntimeError("Логин не удался")

    save_context_state(context)
    return page


def open_account_page(browser):
    if os.path.exists(RUNTIME_STATE_FILE):
        context = browser.new_context(storage_state=RUNTIME_STATE_FILE)
    else:
        context = browser.new_context()

    page = context.new_page()
    page.set_default_timeout(20000)

    page.goto(ACCOUNT_URL, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    if page_shows_login_form(page):
        context.close()
        context = browser.new_context()
        page = login_and_refresh_session(context)

    return context, page


def get_booking_buttons(page):
    return page.locator("text=Забронировать время")


def extract_date(button, i):
    try:
        text = button.locator("xpath=ancestor::*[1]").inner_text(timeout=3000)
        match = re.findall(r"\d{2}/\d{2}/\d{4}", text)
        if match:
            return match[-1]
    except Exception:
        pass
    return f"кнопка #{i + 1}"


def _check_worker(queue):
    dates = []

    try:
        log("▶️ старт воркера")
        seed_runtime_state_from_env()

        with sync_playwright() as p:
            log("🚀 запускаю браузер")
            browser = p.chromium.launch(
                channel="chromium",
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            context, page = open_account_page(browser)

            buttons = get_booking_buttons(page)
            count = buttons.count()

            for i in range(count):
                page.goto(ACCOUNT_URL, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)

                if page_shows_login_form(page):
                    context.close()
                    context = browser.new_context()
                    page = login_and_refresh_session(context)
                    page.goto(ACCOUNT_URL, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)

                buttons = get_booking_buttons(page)

                if i >= buttons.count():
                    continue

                button = buttons.nth(i)
                date = extract_date(button, i)

                try:
                    button.click(timeout=5000)
                except Exception:
                    continue

                page.wait_for_timeout(1500)

                html = page.content().lower()

                if "все свободные места для записи закончились" not in html:
                    dates.append(date)

                safe_close_popup(page)

            save_context_state(context)
            browser.close()

            queue.put({"ok": True, "dates": dates})

    except Exception as e:
        queue.put({"ok": False, "error": str(e), "dates": []})


def run_check():
    queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_check_worker, args=(queue,))
    process.start()
    process.join(CHECK_TIMEOUT_SECONDS)

    if process.is_alive():
        process.terminate()
        process.join()
        return {"ok": False, "timeout": True, "dates": []}

    if queue.empty():
        return {"ok": False, "error": "Нет результата", "dates": []}

    return queue.get()


def build_alert(dates):
    unique_dates = sorted(set(dates))
    if len(unique_dates) == 1:
        return (
            "🔥 Похоже, появился слот.\n\n"
            f"Дата: {unique_dates[0]}\n\n"
            "Срочно зайди в donor-mos и попробуй записаться."
        )

    return (
        "🔥 Похоже, появились слоты на несколько дат.\n\n"
        + "\n".join(f"• {d}" for d in unique_dates)
        + "\n\nСрочно зайди в donor-mos и попробуй записаться."
    )


def build_heartbeat(last_time, error_streak, recovered_since_last_heartbeat):
    if error_streak >= ERROR_STREAK_FOR_ALERT:
        status = "🚨 Бот с ошибкой"
    elif recovered_since_last_heartbeat:
        status = "⚠️ Бот восстановился после сбоев"
    else:
        status = "✅ Бот работает"

    return f"{status}\n\nПоследняя проверка: {last_time}"


def classify_state(result):
    if result.get("timeout"):
        return "timeout", "Проверка зависла → убита watchdog-таймаутом"

    if not result.get("ok"):
        error_text = result.get("error", "неизвестная ошибка")
        return f"error:{error_text}", f"Ошибка: {error_text}"

    return "ok", "Проверка успешна"


if __name__ == "__main__":
    log("БОТ 4.2 ЗАПУЩЕН")

    last_alert = None
    last_heartbeat_key = None

    error_streak = 0
    had_errors_since_last_heartbeat = False
    last_time = ""

    last_state_key = None
    timeout_streak = 0

    while True:
        now = now_moscow()
        log(f"Старт проверки: {now.strftime('%H:%M:%S')}")

        result = run_check()
        last_time = now_moscow().strftime("%H:%M:%S")

        state_key, state_text = classify_state(result)
        ok = state_key == "ok"
        dates = result.get("dates", [])

        if ok:
            if last_state_key != "ok":
                log("Проверка снова успешна")
            error_streak = 0
            timeout_streak = 0
        else:
            error_streak += 1
            had_errors_since_last_heartbeat = True

            if result.get("timeout"):
                timeout_streak += 1

                if state_key != last_state_key:
                    log(f"{state_text} ({timeout_streak}/{RESTART_AFTER_TIMEOUT_STREAK})")
                else:
                    log(f"Повторное зависание ({timeout_streak}/{RESTART_AFTER_TIMEOUT_STREAK})")

                if timeout_streak >= RESTART_AFTER_TIMEOUT_STREAK:
                    send_message("♻️ Бот уходит в самоперезапуск: несколько проверок подряд зависли.")
                    log("♻️ Слишком много подряд зависаний, завершаю процесс для рестарта Railway")
                    os._exit(1)
            else:
                timeout_streak = 0
                if state_key != last_state_key:
                    log(state_text)

        last_state_key = state_key

        if dates:
            sig = "|".join(sorted(set(dates)))
            if sig != last_alert:
                send_message(build_alert(dates))
                last_alert = sig
                log("Отправлено уведомление о слотах")
        else:
            if last_state_key == "ok":
                log("Слотов нет")
            last_alert = None

        hour = now.hour
        day = now.strftime("%Y-%m-%d")
        key = f"{day}-{hour}"

        if hour in HEARTBEAT_HOURS and key != last_heartbeat_key:
            recovered_since_last_heartbeat = had_errors_since_last_heartbeat and error_streak == 0
            send_message(build_heartbeat(last_time, error_streak, recovered_since_last_heartbeat))
            last_heartbeat_key = key
            had_errors_since_last_heartbeat = False
            log("Heartbeat отправлен")

        log(f"Жду {SLEEP_BETWEEN_CHECKS_SECONDS} сек\n")
        time.sleep(SLEEP_BETWEEN_CHECKS_SECONDS)

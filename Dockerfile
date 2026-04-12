FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 🔥 ВАЖНО: ставим браузер ОДИН РАЗ при сборке
RUN playwright install chromium

CMD ["python", "main.py"]

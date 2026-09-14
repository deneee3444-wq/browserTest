from flask import Flask
from playwright.sync_api import sync_playwright

app = Flask(__name__)


@app.get("/")
def home():
    return "Tarayiciyi denemek icin adresin sonuna /test ekle."


@app.get("/test")
def test():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage"],
        )
        try:
            page = browser.new_page()
            page.goto(
                "https://www.wikipedia.org",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            return {
                "baslik": page.title(),
                "adres": page.url,
            }
        finally:
            browser.close()

import logging
import time
from urllib.parse import urlparse

from flask import Flask, jsonify, request
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

app = Flask(__name__)
app.json.ensure_ascii = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


@app.get("/")
def home():
    return """
    <!doctype html>
    <html lang="tr">
    <head>
        <meta charset="utf-8">
        <meta name="viewport"
              content="width=device-width, initial-scale=1">
        <title>Sayfa Testi</title>
        <style>
            body {
                max-width: 700px;
                margin: 60px auto;
                padding: 20px;
                font-family: Arial, sans-serif;
                background: #f5f5f5;
                color: #222;
            }
            form {
                background: white;
                padding: 24px;
                border-radius: 12px;
            }
            input, button {
                box-sizing: border-box;
                width: 100%;
                padding: 14px;
                font-size: 16px;
                border-radius: 8px;
            }
            input {
                border: 1px solid #ccc;
            }
            button {
                margin-top: 14px;
                background: #2563eb;
                color: white;
                border: none;
                cursor: pointer;
            }
            p {
                line-height: 1.5;
            }
        </style>
    </head>
    <body>
        <h1>Sayfa Testi</h1>
        <p>Test edeceğin adresi yapıştır.</p>

        <form action="/test" method="get">
            <input
                type="text"
                inputmode="url"
                name="url"
                placeholder="https://www.wikipedia.org"
                aria-label="Test edilecek URL"
                autocomplete="off"
                required
            >
            <button type="submit">Test et</button>
        </form>

        <p>Sonuçlar JSON olarak gösterilir.
           İşlem biraz zaman alabilir.</p>
    </body>
    </html>
    """


@app.get("/test")
def test():
    url = request.args.get("url", "").strip()

    if not url:
        return jsonify({"hata": "Lütfen bir URL gir."}), 400

    # https:// yazılmamışsa otomatik ekle.
    if "://" not in url:
        url = "https://" + url

    try:
        parsed = urlparse(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Geçersiz URL")

        # Geçersiz port değerlerini de yakala.
        _ = parsed.port
    except ValueError:
        return jsonify({
            "hata": "Geçerli bir http:// veya https:// adresi gir."
        }), 400

    start = time.monotonic()
    logger.info("Test baslatildi | hedef=%s", parsed.hostname)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage"],
            )

            try:
                page = browser.new_page()
                js_errors = []
                failed_requests = []
                navigation_responses = []

                def on_page_error(error):
                    if len(js_errors) < 20:
                        js_errors.append(str(error)[:2000])

                def on_request_failed(req):
                    if len(failed_requests) < 20:
                        failed_requests.append({
                            "url": req.url,
                            "hata": req.failure,
                        })

                def on_response(response):
                    # Ana sayfanın yönlendirmelerini ve yanıtlarını kaydet.
                    req = response.request
                    if (
                        req.is_navigation_request()
                        and req.frame == page.main_frame
                    ):
                        navigation_responses.append(response)
                        logger.info(
                            "Ana sayfa yaniti | status=%s",
                            response.status,
                        )

                page.on("pageerror", on_page_error)
                page.on("requestfailed", on_request_failed)
                page.on("response", on_response)

                goto_response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                # Dinamik içerik için kısa bekleme.
                # Sayfanın tamamen yüklendiğini garanti etmez.
                page.wait_for_timeout(2000)

                html = page.content()
                lines = html.splitlines()

                # Bekleme sırasında başka navigasyon olduysa
                # son ana sayfa yanıtını kullan.
                response = (
                    navigation_responses[-1]
                    if navigation_responses
                    else goto_response
                )
                headers = response.headers if response else {}

                # Tek satırlık büyük HTML'in çıktıyı şişirmesini önle.
                preview = "\n".join(lines[:100])
                max_preview_chars = 100000

                result = {
                    "istenen_url": url,
                    "son_url": page.url,
                    "status_code": (
                        response.status if response else None
                    ),
                    "http_basarili": (
                        response.ok if response else None
                    ),
                    "baslik": page.title(),
                    "sure_saniye": round(
                        time.monotonic() - start, 2
                    ),
                    "content_type": headers.get("content-type"),
                    "server": headers.get("server"),
                    "html_boyutu_bayt": len(html.encode("utf-8")),
                    "html_toplam_satir": len(lines),
                    "html_ilk_100_satir": (
                        preview[:max_preview_chars].splitlines()
                    ),
                    "html_onizleme_karakter_siniri": max_preview_chars,
                    "html_onizleme_karakterden_kesildi": (
                        len(preview) > max_preview_chars
                    ),
                    "ana_sayfa_yanitlari": [
                        {
                            "url": item.url,
                            "status_code": item.status,
                        }
                        for item in navigation_responses[:20]
                    ],
                    "javascript_hatalari": js_errors,
                    "basarisiz_istekler": failed_requests,
                }

                logger.info(
                    "Test tamamlandi | status=%s | "
                    "sure=%ss | html=%s bayt | "
                    "js_hatasi=%s | basarisiz_istek=%s",
                    result["status_code"],
                    result["sure_saniye"],
                    result["html_boyutu_bayt"],
                    len(js_errors),
                    len(failed_requests),
                )

                return jsonify(result)

            finally:
                browser.close()

    except PlaywrightTimeoutError:
        logger.warning(
            "Zaman asimi | hedef=%s", parsed.hostname
        )
        return jsonify({
            "hata": "Sayfa işlemi zaman aşımına uğradı.",
            "url": url,
            "sure_saniye": round(time.monotonic() - start, 2),
        }), 504

    except Exception:
        logger.exception("Sayfa testi sirasinda hata olustu")
        return jsonify({
            "hata": (
                "Test tamamlanamadı. "
                "Ayrıntı için Render loglarına bak."
            )
        }), 500

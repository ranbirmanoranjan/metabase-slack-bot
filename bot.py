from playwright.sync_api import sync_playwright
from slack_sdk import WebClient
import os

DASHBOARD_URL = os.environ["DASHBOARD_URL"]
SLACK_TOKEN = os.environ["SLACK_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})

        # Open dashboard
        page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=90000)

        # Strong wait for charts/data
        page.wait_for_timeout(30000)   # 30 sec wait

        # Scroll to force lazy loading
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(5000)

        # Take screenshot
        page.screenshot(path="dashboard.png", full_page=True)

        browser.close()

    client = WebClient(token=SLACK_TOKEN)

    client.files_upload_v2(
        channel=CHANNEL_ID,
        file="dashboard.png",
        title="📊 Dashboard Update",
        initial_comment="Automated dashboard snapshot (fully loaded)"
    )

if __name__ == "__main__":
    main()

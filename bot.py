from playwright.sync_api import sync_playwright
from slack_sdk import WebClient
import os

DASHBOARD_URL = os.environ["DASHBOARD_URL"]
SLACK_TOKEN = os.environ["SLACK_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        
        # ✅ NO viewport restriction
        page = browser.new_page()

        page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=90000)

        page.wait_for_timeout(30000)

        # ✅ SIMPLE screenshot (no full_page, no scroll)
        page.screenshot(path="dashboard.png")

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

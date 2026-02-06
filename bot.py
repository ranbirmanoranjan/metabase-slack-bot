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

        page.goto(DASHBOARD_URL, timeout=60000)

        # Wait till dashboard widgets load
        page.wait_for_selector("div.Card, div.Dashboard", timeout=60000)

        # Extra safety wait
        page.wait_for_timeout(15000)

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

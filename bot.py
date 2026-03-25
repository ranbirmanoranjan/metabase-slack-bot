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

        # Wait for full load (important for Metabase)
        page.wait_for_timeout(30000)

        # 🔥 STEP 1: increase viewport height
        page.set_viewport_size({"width": 1920, "height": 2000})

        # 🔥 STEP 2: scroll to top first
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(2000)

        # 🔥 STEP 3: scroll to bottom (force lazy load)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(5000)

        # 🔥 STEP 4: take clean full screenshot
        page.screenshot(path="dashboard.png", full_page=True)

        browser.close()

    # Upload to Slack
    client = WebClient(token=SLACK_TOKEN)

    client.files_upload_v2(
        channel=CHANNEL_ID,
        file="dashboard.png",
        title="📊 Dashboard Update",
        initial_comment="Automated dashboard snapshot (fully loaded)"
    )

if __name__ == "__main__":
    main()

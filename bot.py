from playwright.sync_api import sync_playwright
from slack_sdk import WebClient
import os

DASHBOARD_URL = os.environ["DASHBOARD_URL"]
SLACK_TOKEN = os.environ.get("SLACK_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

PADDING = 24

# Bounding box of the title + table header + data cells, so the screenshot
# stops at the last row instead of including the empty space below it
CONTENT_BOX_JS = """() => {
    const els = document.querySelectorAll(
        '[data-testid=embed-frame-header], [data-testid=header-cell], [data-testid=cell-data]');
    if (!els.length) return null;
    let top = Infinity, left = Infinity, bottom = 0, right = 0;
    for (const el of els) {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        top = Math.min(top, r.top); left = Math.min(left, r.left);
        bottom = Math.max(bottom, r.bottom); right = Math.max(right, r.right);
    }
    return {top, left, bottom, right, width: window.innerWidth};
}"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # Narrower viewport keeps columns close together; scale factor 2 renders sharp text.
        # bypass_csp lets us inject the larger-font style below (Metabase blocks inline styles)
        page = browser.new_page(viewport={"width": 1100, "height": 1200}, device_scale_factor=2, bypass_csp=True)

        # Open dashboard
        page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=90000)

        # Wait for the table to render
        page.wait_for_selector("[data-testid=cell-data]", timeout=60000)
        page.wait_for_timeout(5000)

        # Bigger text
        page.add_style_tag(content="[data-testid=table-root] { font-size: 16px !important; }"
                                   " [data-testid=cell-data], [data-testid=header-cell] { font-size: 16px !important; }")
        page.wait_for_timeout(1000)

        box = page.evaluate(CONTENT_BOX_JS)
        if box:
            clip = {
                "x": 0,
                "y": max(box["top"] - PADDING, 0),
                "width": box["width"],
                "height": box["bottom"] - box["top"] + 2 * PADDING,
            }
            page.screenshot(path="dashboard.png", clip=clip)
        else:
            page.screenshot(path="dashboard.png", full_page=True)

        browser.close()

    if not SLACK_TOKEN:
        print("SLACK_TOKEN not set; saved dashboard.png without posting")
        return

    client = WebClient(token=SLACK_TOKEN)

    client.files_upload_v2(
        channel=CHANNEL_ID,
        file="dashboard.png",
        title="📊 Dashboard Update",
        initial_comment="Automated dashboard snapshot (fully loaded)"
    )

if __name__ == "__main__":
    main()

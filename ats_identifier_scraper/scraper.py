import logging
import os
import re
import json
import asyncio
from pyppeteer import launch
from serpapi import GoogleSearch

logger = logging.getLogger(__name__)

def get_career_page_url(company_name):
    """
    Performs a Google search to find the career page URL for a given company.

    Requires the SERPAPI_API_KEY environment variable. Returns None without
    making any request if the key is missing or blank.
    """
    api_key = (os.getenv("SERPAPI_API_KEY") or "").strip()
    if not api_key:
        logger.error(
            "SERPAPI_API_KEY is not set; cannot search for %s. "
            "Set it in your environment or .env file.",
            company_name,
        )
        return None

    try:
        search = GoogleSearch({
            "q": f"{company_name} careers",
            "api_key": api_key,
        })
        result = search.get_dict()
        if "organic_results" in result and len(result["organic_results"]) > 0:
            return result["organic_results"][0]["link"]
    except Exception as e:
        print(f"Error searching for {company_name}: {e}")
    return None

async def scrape_career_page(browser, url):
    """
    Scrapes a career page to find the ATS platform and identifier.
    """
    page = None
    try:
        page = await browser.newPage()
        await page.goto(url, timeout=60000)
        content = await page.content()

        # Look for links to ATS platforms
        for match in re.finditer(r'href="([^"]+)"', content):
            href = match.group(1)
            if "greenhouse.io" in href:
                match = re.search(r"boards.greenhouse.io/([a-zA-Z0-9_-]+)", href)
                if match:
                    return "greenhouse", match.group(1)
            elif "ashbyhq.com" in href:
                match = re.search(r"jobs.ashbyhq.com/([a-zA-Z0-9_-]+)", href)
                if match:
                    return "ashby", match.group(1)
            elif "lever.co" in href:
                match = re.search(r"jobs.lever.co/([a-zA-Z0-9_-]+)", href)
                if match:
                    return "lever", match.group(1)
    except Exception as e:
        print(f"Error scraping {url}: {e}")
    finally:
        if page:
            await page.close()
    return None, None

async def main():
    """
    Main function to scrape company career pages for ATS identifiers.
    """
    with open("ats_identifier_scraper/companies.txt", "r") as f:
        company_names = [line.strip() for line in f.readlines()]

    results = {"greenhouse": [], "ashby": [], "lever": []}

    browser = await launch(
        headless=True,
        args=[
            '--no-sandbox',
            '--disable-setuid-sandbox',
        ]
    )
    try:
        for company_name in company_names:
            print(f"Scraping {company_name}...")
            career_page_url = get_career_page_url(company_name)
            if career_page_url:
                ats_platform, identifier = await scrape_career_page(browser, career_page_url)
                if ats_platform and identifier:
                    print(f"  Found: {ats_platform} - {identifier}")
                    results[ats_platform].append({"identifier": identifier, "name": company_name, "enabled": True})
                else:
                    print("  ATS not found.")
            else:
                print("  Careers page not found.")
    finally:
        await browser.close()

    # Update the JSON registry files
    for ats_platform, companies in results.items():
        if companies:
            file_path = f"data/registries/{ats_platform}_companies.json"
            if os.path.exists(file_path):
                with open(file_path, "r+") as f:
                    data = json.load(f)
                    existing_identifiers = {c["identifier"] for c in data["companies"]}
                    new_companies = [c for c in companies if c["identifier"] not in existing_identifiers]
                    if new_companies:
                        data["companies"].extend(new_companies)
                        f.seek(0)
                        json.dump(data, f, indent=2)
                        print(f"Updated {file_path} with {len(new_companies)} new companies.")
            else:
                with open(file_path, "w") as f:
                    json.dump({"version": "1.0", "updated_at": "2025-11-13T00:00:00Z", "companies": companies}, f, indent=2)
                    print(f"Created {file_path} with {len(companies)} new companies.")


if __name__ == "__main__":
    asyncio.run(main())
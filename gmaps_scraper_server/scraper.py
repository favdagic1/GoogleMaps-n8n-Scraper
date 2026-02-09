import json
import asyncio # Changed from time
import re
import random
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError # Changed to async
# Note: playwright-stealth 2.0 has API changes, so we rely on manual anti-detection
from urllib.parse import urlencode

# Import the extraction functions from our helper module
from . import extractor

# --- Constants ---
BASE_URL = "https://www.google.com/maps/search/"
DEFAULT_TIMEOUT = 30000  # 30 seconds for navigation and selectors
SCROLL_PAUSE_TIME = 2.0  # Pause between scrolls (increased for more human-like behavior)
MAX_SCROLL_ATTEMPTS_WITHOUT_NEW_LINKS = 15 # Stop scrolling if no new links found after this many scrolls
MIN_DELAY = 0.8  # Minimum delay between page loads
MAX_DELAY = 1.5  # Maximum delay between page loads

# Smart delay settings for human-like behavior
READING_TIME_MIN = 2.0  # Minimum "reading" time (seconds)
READING_TIME_MAX = 5.0  # Maximum "reading" time (seconds)
BREAK_INTERVAL = 10  # Take a break every N places
BREAK_MIN = 20.0  # Minimum break duration (seconds)
BREAK_MAX = 45.0  # Maximum break duration (seconds)
OCCASIONAL_LONG_BREAK_CHANCE = 0.1  # 10% chance for a longer break

# --- Helper Functions ---

def calculate_fatigue_delay(place_count):
    """
    Simulates human fatigue - slower responses after prolonged use.

    Args:
        place_count (int): Number of places processed so far

    Returns:
        float: Multiplier for delay (1.0 = normal, 2.0 = 2x slower)
    """
    if place_count < 20:
        return 1.0  # Normal speed
    elif place_count < 50:
        return 1.2  # 20% slower
    elif place_count < 100:
        return 1.5  # 50% slower
    elif place_count < 200:
        return 1.8  # 80% slower
    else:
        return 2.2  # 2.2x slower (significant fatigue)

async def natural_scroll(page, feed_selector):
    """
    Scrolls like a real human - gradually with pauses and variations.

    Args:
        page: Playwright page object
        feed_selector: CSS selector for the scrollable feed

    Returns:
        int: Final scroll height
    """
    try:
        total_height = await page.evaluate(f'document.querySelector("{feed_selector}").scrollHeight')
        current_position = 0

        while current_position < total_height:
            # Random scroll increment (100-400px at a time)
            scroll_increment = random.randint(100, 400)
            current_position += scroll_increment

            # Scroll to new position
            await page.evaluate(f'''
                document.querySelector("{feed_selector}").scrollTop = {current_position}
            ''')

            # Random micro-pause between scrolls (0.15-0.6s)
            await asyncio.sleep(random.uniform(0.15, 0.6))

            # 15% chance to pause and "read" something
            if random.random() < 0.15:
                read_pause = random.uniform(1.0, 3.0)
                await asyncio.sleep(read_pause)

            # Update total height (content might load dynamically)
            new_total_height = await page.evaluate(f'document.querySelector("{feed_selector}").scrollHeight')
            if new_total_height > total_height:
                total_height = new_total_height

        return current_position
    except Exception as e:
        print(f"Natural scroll error: {e}")
        return 0

async def smart_delay(place_count):
    """
    Implements human-like delays with variation.

    Args:
        place_count (int): Current place number being processed

    Returns:
        float: Delay duration in seconds
    """
    # Base delay: Random between 1-3 seconds
    base_delay = random.uniform(MIN_DELAY, MAX_DELAY) + random.uniform(0.5, 1.5)

    # Add "reading time" - simulate user reading the page
    reading_time = random.uniform(READING_TIME_MIN, READING_TIME_MAX)

    # Add some variance using different distributions
    if random.random() < 0.3:  # 30% chance for extra variation
        variance = random.gauss(1.0, 0.5)  # Gaussian distribution
        variance = max(0.5, min(2.0, variance))  # Clamp between 0.5-2.0
    else:
        variance = 1.0

    total_delay = (base_delay + reading_time) * variance

    # Apply fatigue multiplier - humans get slower over time
    fatigue_multiplier = calculate_fatigue_delay(place_count)
    if fatigue_multiplier > 1.0:
        print(f"  [FATIGUE] Applying {fatigue_multiplier}x slowdown (place #{place_count})")
    total_delay = total_delay * fatigue_multiplier

    # Periodic breaks - simulate user taking a break
    if place_count > 0 and place_count % BREAK_INTERVAL == 0:
        # Regular break every BREAK_INTERVAL places
        break_time = random.uniform(BREAK_MIN, BREAK_MAX)
        print(f"\n[BREAK] Taking a human-like break for {break_time:.1f} seconds...")
        await asyncio.sleep(break_time)

        # Small chance for an even longer break (simulate coffee/bathroom break)
        if random.random() < OCCASIONAL_LONG_BREAK_CHANCE:
            extra_break = random.uniform(30.0, 60.0)
            print(f"[EXTENDED BREAK] Extended break for {extra_break:.1f} more seconds...")
            await asyncio.sleep(extra_break)

    return total_delay
def create_search_url(query, lang="en", geo_coordinates=None, zoom=None):
    """Creates a Google Maps search URL."""
    params = {'q': query, 'hl': lang}
    # Note: geo_coordinates and zoom might require different URL structure (/maps/@lat,lng,zoom)
    # For simplicity, starting with basic query search
    return BASE_URL + "?" + urlencode(params)

async def try_load_website(page, website_url, timeout=10000):
    """
    Attempts to load a business website and returns status.

    Args:
        page: Playwright page object
        website_url: The website URL to load
        timeout: Timeout in milliseconds (default 10 seconds)

    Returns:
        str: Status - 'accessible', 'forbidden', 'timeout', or 'error'
    """
    try:
        response = await page.goto(website_url, wait_until='domcontentloaded', timeout=timeout)

        if response:
            status_code = response.status

            # Check for forbidden/access denied errors
            if status_code in [403, 401, 451]:  # Forbidden, Unauthorized, Unavailable For Legal Reasons
                return "forbidden"
            elif status_code >= 400:  # Other client/server errors
                return "error"
            else:
                return "accessible"
        else:
            # No response received
            return "error"

    except PlaywrightTimeoutError:
        # Timeout while loading website
        return "timeout"
    except Exception as e:
        # Check if error message contains forbidden/permission keywords
        error_str = str(e).lower()
        if any(keyword in error_str for keyword in ['forbidden', 'permission', 'access denied', 'unauthorized']):
            return "forbidden"
        else:
            return "error"

# --- Main Scraping Logic ---
async def scrape_google_maps(query, max_places=None, lang="en", headless=True): # Added async
    """
    Scrapes Google Maps for places based on a query.

    Args:
        query (str): The search query (e.g., "restaurants in New York").
        max_places (int, optional): Maximum number of places to scrape. Defaults to None (scrape all found).
        lang (str, optional): Language code for Google Maps (e.g., 'en', 'es'). Defaults to "en".
        headless (bool, optional): Whether to run the browser in headless mode. Defaults to True.

    Returns:
        list: A list of dictionaries, each containing details for a scraped place.
              Returns an empty list if no places are found or an error occurs.
    """
    results = []
    place_links = set()
    scroll_attempts_no_new = 0
    browser = None

    async with async_playwright() as p: # Changed to async
        try:
            browser = await p.chromium.launch(
                headless=headless,
                args=[
                    '--disable-dev-shm-usage',  # Use /tmp instead of /dev/shm for shared memory
                    '--no-sandbox',  # Required for running in Docker
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled',  # Hide automation
                ]
            ) # Added await
            context = await browser.new_context( # Added await
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                java_script_enabled=True,
                accept_downloads=False,
                locale=lang,
                timezone_id='Europe/Sarajevo',
                geolocation={'latitude': 43.8563, 'longitude': 18.4131},  # Sarajevo coordinates
                permissions=['geolocation'],
                extra_http_headers={
                    'Accept-Language': f'{lang},en-US;q=0.9,en;q=0.8',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                }
            )
            page = await context.new_page() # Added await

            # Note: Manual anti-detection methods applied via browser args,
            # context settings, and JavaScript injections below
            if not page:
                await browser.close() # Close browser before raising
                raise Exception("Failed to create a new browser page (context.new_page() returned None).")

            # Additional anti-detection: Override navigator properties
            await page.add_init_script("""
                // Override the navigator.webdriver property
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => false,
                });

                // Override the plugins array
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });

                // Override the languages property
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });

                // Pass the Chrome Test
                window.chrome = {
                    runtime: {},
                };

                // Pass the Permissions Test
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // Canvas Fingerprinting Protection
                const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
                const originalToBlob = HTMLCanvasElement.prototype.toBlob;
                const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;

                // Add noise to canvas to make fingerprint unique each time
                const addNoise = (canvas, context) => {
                    const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
                    for (let i = 0; i < imageData.data.length; i += 4) {
                        imageData.data[i] += Math.floor(Math.random() * 3) - 1;     // R
                        imageData.data[i+1] += Math.floor(Math.random() * 3) - 1;   // G
                        imageData.data[i+2] += Math.floor(Math.random() * 3) - 1;   // B
                    }
                    context.putImageData(imageData, 0, 0);
                };

                HTMLCanvasElement.prototype.toDataURL = function() {
                    addNoise(this, this.getContext('2d'));
                    return originalToDataURL.apply(this, arguments);
                };

                // WebGL Fingerprinting Protection
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    // Randomize GPU vendor and renderer
                    if (parameter === 37445) { // UNMASKED_VENDOR_WEBGL
                        const vendors = ['Intel Inc.', 'Google Inc.', 'Mozilla', 'Apple Inc.'];
                        return vendors[Math.floor(Math.random() * vendors.length)];
                    }
                    if (parameter === 37446) { // UNMASKED_RENDERER_WEBGL
                        const renderers = [
                            'Intel Iris OpenGL Engine',
                            'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11)',
                            'Mesa DRI Intel(R) HD Graphics',
                            'Apple M1'
                        ];
                        return renderers[Math.floor(Math.random() * renderers.length)];
                    }
                    return getParameter.apply(this, arguments);
                };
            """)

            # Removed problematic: await page.set_default_timeout(DEFAULT_TIMEOUT)
            # Removed associated debug prints

            search_url = create_search_url(query, lang)
            print(f"Navigating to search URL: {search_url}")
            await page.goto(search_url, wait_until='domcontentloaded') # Added await
            await asyncio.sleep(random.uniform(2.0, 3.5)) # More human-like initial delay

            # --- Handle potential consent forms ---
            # This is a common pattern, might need adjustment based on specific consent popups
            try:
                consent_button_xpath = "//button[.//span[contains(text(), 'Accept all') or contains(text(), 'Reject all')]]"
                # Wait briefly for the button to potentially appear
                await page.wait_for_selector(consent_button_xpath, state='visible', timeout=5000) # Added await
                # Click the "Accept all" or equivalent button if found
                # Example: Prioritize "Accept all"
                accept_button = await page.query_selector("//button[.//span[contains(text(), 'Accept all')]]") # Added await
                if accept_button:
                    print("Accepting consent form...")
                    await accept_button.click() # Added await
                else:
                    # Fallback to clicking the first consent button found (might be reject)
                    print("Clicking first available consent button...")
                    await page.locator(consent_button_xpath).first.click() # Added await
                # Wait for navigation/popup closure
                await page.wait_for_load_state('networkidle', timeout=5000) # Added await
            except PlaywrightTimeoutError:
                print("No consent form detected or timed out waiting.")
            except Exception as e:
                print(f"Error handling consent form: {e}")


            # --- Scrolling and Link Extraction ---
            print("Scrolling to load places...")
            feed_selector = '[role="feed"]'
            try:
                await page.wait_for_selector(feed_selector, state='visible', timeout=25000) # Added await
            except PlaywrightTimeoutError:
                 # Check if it's a single result page (maps/place/)
                if "/maps/place/" in page.url:
                    print("Detected single place page.")
                    place_links.add(page.url)
                else:
                    print(f"Error: Feed element '{feed_selector}' not found. Maybe no results or page structure changed.")
                    await browser.close() # Added await
                    return [] # No results or page structure changed

            if await page.locator(feed_selector).count() > 0: # Added await
                last_height = await page.evaluate(f'document.querySelector(\'{feed_selector}\').scrollHeight') # Added await
                scroll_iteration = 0
                while True:
                    scroll_iteration += 1
                    # Natural scroll - simulate human scrolling behavior
                    # Scroll in small increments instead of jumping to bottom
                    current_scroll = await page.evaluate(f'document.querySelector(\'{feed_selector}\').scrollTop')
                    scroll_increment = random.randint(300, 800)  # Random scroll amount
                    new_scroll_position = current_scroll + scroll_increment

                    await page.evaluate(f'document.querySelector(\'{feed_selector}\').scrollTop = {new_scroll_position}')

                    # Variable pause between scrolls (more human-like)
                    scroll_pause = random.uniform(0.8, 2.5)
                    await asyncio.sleep(scroll_pause)

                    # Occasionally pause longer (15% chance) as if reading something
                    if random.random() < 0.15:
                        read_pause = random.uniform(1.5, 3.5)
                        await asyncio.sleep(read_pause)

                    # Extract links after scroll
                    current_links_list = await page.locator(f'{feed_selector} a[href*="/maps/place/"]').evaluate_all('elements => elements.map(a => a.href)') # Added await
                    current_links = set(current_links_list)
                    new_links_found = len(current_links - place_links) > 0
                    place_links.update(current_links)
                    print(f"Found {len(place_links)} unique place links so far...")

                    if max_places is not None and len(place_links) >= max_places:
                        print(f"Reached max_places limit ({max_places}).")
                        place_links = set(list(place_links)[:max_places]) # Trim excess links
                        break

                    # Check if scroll height has changed
                    new_height = await page.evaluate(f'document.querySelector(\'{feed_selector}\').scrollHeight') # Added await
                    if new_height == last_height:
                        # Check for the "end of results" marker
                        end_marker_xpath = "//span[contains(text(), \"You've reached the end of the list.\")]"
                        if await page.locator(end_marker_xpath).count() > 0: # Added await
                            print("Reached the end of the results list.")
                            break
                        else:
                            # If height didn't change but end marker isn't there, maybe loading issue?
                            # Increment no-new-links counter
                            if not new_links_found:
                                scroll_attempts_no_new += 1
                                print(f"Scroll height unchanged and no new links. Attempt {scroll_attempts_no_new}/{MAX_SCROLL_ATTEMPTS_WITHOUT_NEW_LINKS}")
                                if scroll_attempts_no_new >= MAX_SCROLL_ATTEMPTS_WITHOUT_NEW_LINKS:
                                    print("Stopping scroll due to lack of new links.")
                                    break
                            else:
                                scroll_attempts_no_new = 0 # Reset if new links were found this cycle
                    else:
                        last_height = new_height
                        scroll_attempts_no_new = 0 # Reset if scroll height changed

                    # Optional: Add a hard limit on scrolls to prevent infinite loops
                    # if scroll_count > MAX_SCROLLS: break

            # --- Scraping Individual Places ---
            print(f"\nScraping details for {len(place_links)} places...")
            count = 0
            for link in place_links:
                count += 1
                print(f"Processing link {count}/{len(place_links)}: {link}") # Keep sync print

                # Use smart delay algorithm for human-like behavior
                delay = await smart_delay(count)
                print(f"  >> Waiting {delay:.1f}s before loading...")
                await asyncio.sleep(delay)

                try:
                    await page.goto(link, wait_until='domcontentloaded') # Added await
                    # Wait a bit for dynamic content if needed, or wait for a specific element
                    # await page.wait_for_load_state('networkidle', timeout=10000) # Or networkidle if needed

                    # Additional small random delay after page load
                    await asyncio.sleep(random.uniform(0.5, 1.0))

                    html_content = await page.content() # Added await
                    place_data = extractor.extract_place_data(html_content)

                    if place_data:
                        place_data['link'] = link # Add the source link

                        # Try to load the business website if it exists
                        website_url = place_data.get('website', '')
                        if website_url and website_url not in ['', 'N/A', None]:
                            print(f"  >> Attempting to access website: {website_url}")
                            try:
                                website_status = await try_load_website(page, website_url)

                                if website_status == "accessible":
                                    print(f"  [OK] Website accessible: {website_url}")
                                elif website_status == "forbidden":
                                    print(f"  [FORBIDDEN] Website requires access - marking as 'Potreban pristup'")
                                    place_data['website'] = 'Potreban pristup'
                                    place_data['original_website'] = website_url
                                elif website_status == "timeout":
                                    print(f"  [TIMEOUT] Website timeout")
                                    place_data['website'] = 'Timeout'
                                    place_data['original_website'] = website_url
                                elif website_status == "error":
                                    print(f"  [ERROR] Website error")
                                    place_data['website'] = 'Greška pri učitavanju'
                                    place_data['original_website'] = website_url
                            except Exception as e:
                                # Catch ALL exceptions including ForbiddenException
                                # This ensures the workflow never stops due to website checking
                                print(f"  [WARNING] Website check exception (continuing): {type(e).__name__}: {str(e)}")
                                place_data['website'] = 'Greška pri provjeri'
                                place_data['original_website'] = website_url
                                # Continue scraping - don't let website errors stop the workflow

                        results.append(place_data)
                        # print(json.dumps(place_data, indent=2)) # Optional: print data as it's scraped
                    else:
                        print(f"  - Failed to extract data for: {link}")
                        # Optionally save the HTML for debugging
                        # with open(f"error_page_{count}.html", "w", encoding="utf-8") as f:
                        #     f.write(html_content)

                except PlaywrightTimeoutError:
                    print(f"  - Timeout navigating to or processing: {link}")
                    # Even on timeout, continue to next place - don't lose all data!
                except Exception as e:
                    print(f"  - Error processing {link}: {e}")
                    # Even on error, continue to next place - don't lose all data!

            await browser.close() # Added await

        except PlaywrightTimeoutError:
            print(f"Timeout error during scraping process.")
        except Exception as e:
            print(f"An error occurred during scraping: {e}")
            import traceback
            traceback.print_exc() # Print detailed traceback for debugging
        finally:
            # Ensure browser is closed if an error occurred mid-process
            if browser and browser.is_connected(): # Check if browser exists and is connected
                await browser.close() # Added await

    print(f"\nScraping finished. Found details for {len(results)} places.")
    return results

# --- Example Usage ---
# (Example usage block removed as this script is now intended to be imported as a module)
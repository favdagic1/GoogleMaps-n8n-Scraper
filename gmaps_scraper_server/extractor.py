import json
import re


def _extract_from_app_init_state(html_content):
    """
    Extracts place_id, coordinates, and name from window.APP_INITIALIZATION_STATE.
    Google still embeds minimal place info here even though full details moved to rendered HTML.
    """
    result = {}
    try:
        match = re.search(
            r';window\.APP_INITIALIZATION_STATE\s*=\s*(.*?);window\.APP_FLAGS',
            html_content, re.DOTALL
        )
        if not match:
            return result

        json_str = match.group(1).strip()
        if not json_str.startswith(('[', '{')):
            return result

        initial_data = json.loads(json_str)

        # New structure (2025+): place data at [5][3][2]
        if (isinstance(initial_data, list) and len(initial_data) > 5
                and isinstance(initial_data[5], list) and len(initial_data[5]) > 3
                and isinstance(initial_data[5][3], list) and len(initial_data[5][3]) > 2
                and isinstance(initial_data[5][3][2], list)):
            data = initial_data[5][3][2]
            # [0] = hex id, [1] = name, [7] = [null, null, lat, lng], [15] = path, [18] = place CID
            if len(data) > 1 and isinstance(data[0], str):
                result['place_id'] = data[0]
            if len(data) > 7 and isinstance(data[7], list) and len(data[7]) > 3:
                lat = data[7][2]
                lng = data[7][3]
                if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                    result['coordinates'] = {"latitude": lat, "longitude": lng}

        # Legacy structure: place data at [3][6]
        if not result and isinstance(initial_data, list) and len(initial_data) > 3:
            if isinstance(initial_data[3], list) and len(initial_data[3]) > 6:
                blob = initial_data[3][6]
                if isinstance(blob, list):
                    _extract_legacy_blob(blob, result)
                elif isinstance(blob, str):
                    _try_parse_legacy_string(blob, result)

    except (json.JSONDecodeError, Exception) as e:
        print(f"  Note: APP_INITIALIZATION_STATE parse issue: {e}")

    return result


def _extract_legacy_blob(blob, result):
    """Try to extract data from the legacy [3][6] list structure."""
    try:
        if len(blob) > 11 and blob[11]:
            result.setdefault('name', blob[11])
        if len(blob) > 10 and blob[10]:
            result.setdefault('place_id', blob[10])
        if len(blob) > 9 and isinstance(blob[9], list) and len(blob[9]) > 3:
            lat, lng = blob[9][2], blob[9][3]
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                result.setdefault('coordinates', {"latitude": lat, "longitude": lng})
        if len(blob) > 4 and isinstance(blob[4], list):
            if len(blob[4]) > 7 and blob[4][7]:
                result.setdefault('rating', blob[4][7])
            if len(blob[4]) > 8 and blob[4][8]:
                result.setdefault('reviews_count', blob[4][8])
        if len(blob) > 7 and isinstance(blob[7], list) and blob[7]:
            result.setdefault('website', blob[7][0])
        if len(blob) > 2 and isinstance(blob[2], list):
            addr = ", ".join(filter(None, blob[2]))
            if addr:
                result.setdefault('address', addr)
        if len(blob) > 13 and blob[13]:
            result.setdefault('categories', blob[13])
    except Exception:
        pass


def _try_parse_legacy_string(blob_str, result):
    """Try to parse the legacy string format at [3][6]."""
    try:
        inner_str = blob_str
        # Strip )]}' prefix if present
        if inner_str.startswith(")]}'"):
            inner_str = inner_str.split(")'", 1)[1] if ")'" in inner_str else inner_str[4:]
            inner_str = inner_str.lstrip('\n')

        # Try to parse as JSON
        inner_str = inner_str.strip()
        if inner_str.startswith(('[', '{')):
            parsed = json.loads(inner_str)
            if isinstance(parsed, list) and len(parsed) > 6 and isinstance(parsed[6], list):
                _extract_legacy_blob(parsed[6], result)
    except (json.JSONDecodeError, Exception):
        pass


def _extract_name(html_content):
    """Extracts place name from the h1 tag."""
    match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.DOTALL)
    if match:
        # Strip inner HTML tags
        name = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        if name:
            return name
    return None


def _extract_rating(html_content):
    """Extracts the average star rating from the rendered HTML."""
    match = re.search(r'class="fontDisplayMedium">([\d.]+)</div>', html_content)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def _extract_reviews_count(html_content):
    """Extracts the total number of reviews."""
    # Pattern: rating followed by review count in nearby HTML
    match = re.search(
        r'fontDisplayMedium">[\d.]+<.*?>([\d,]+)\s+reviews?',
        html_content, re.DOTALL
    )
    if match:
        try:
            return int(match.group(1).replace(',', '').replace('.', ''))
        except ValueError:
            pass
    return None


def _extract_address(html_content):
    """Extracts the address from aria-label or data-item-id."""
    # Try aria-label with Address:
    match = re.search(r'aria-label="Address:\s*(.*?)\s*"', html_content)
    if match:
        return match.group(1).strip()

    # Try data-item-id="address" nearby aria-label
    match = re.search(r'data-item-id="address"[^>]*aria-label="(.*?)"', html_content)
    if match:
        addr = match.group(1).strip()
        if addr.startswith("Address:"):
            addr = addr[8:].strip()
        return addr

    return None


def _extract_website(html_content):
    """Extracts the website URL."""
    # From aria-label with Website:
    match = re.search(r'aria-label="Website:\s*(.*?)\s*"', html_content)
    if match:
        website = match.group(1).strip()
        if website:
            if not website.startswith(('http://', 'https://')):
                website = 'https://' + website
            return website

    # From data-item-id="authority"
    match = re.search(r'data-item-id="authority"[^>]*aria-label="[^"]*?([\w][\w.-]+\.\w{2,})', html_content)
    if match:
        website = match.group(1)
        if not website.startswith(('http://', 'https://')):
            website = 'https://' + website
        return website

    return None


def _extract_phone(html_content):
    """Extracts the phone number."""
    # From data-item-id="phone:tel:XXXXX"
    match = re.search(r'data-item-id="phone:tel:(\d+)"', html_content)
    if match:
        return match.group(1)

    # From aria-label with Phone:
    match = re.search(r'aria-label="Phone:\s*(.*?)\s*"', html_content)
    if match:
        phone = re.sub(r'\D', '', match.group(1))
        if phone:
            return phone

    return None


def _extract_categories(html_content):
    """Extracts the place categories/type."""
    categories = []

    # Look for category text near the h1 (name) element
    h1_match = re.search(r'<h1[^>]*>.*?</h1>', html_content, re.DOTALL)
    if h1_match:
        h1_end = h1_match.end()
        # Search in the area after h1 (within ~3000 chars)
        area = html_content[h1_end:h1_end + 3000]

        # Pattern: category appears as a span text like "4-star hotel", "Hotel", "Restaurant"
        # Usually one of the first meaningful text spans after the rating area
        spans = re.findall(r'<span[^>]*>([^<]{2,50})</span>', area)
        for span_text in spans:
            text = span_text.strip()
            # Skip ratings, review counts, and noise
            if re.match(r'^[\d.,()]+$', text):
                continue
            if 'review' in text.lower():
                continue
            if text in ('·', '$$', '$$$', '$$$$', '$') or text == '\u00b7':
                continue
            if len(text) == 1 and not text.isalpha():
                continue
            # This is likely the category
            if len(text) > 1 and (text[0].isalpha() or text[0].isdigit()):
                categories.append(text)
                break  # Usually just one main category line

    return categories if categories else None


def extract_place_data(html_content):
    """
    Extracts place data from Google Maps HTML content.
    Uses HTML DOM parsing (aria-labels, data-item-ids, rendered elements)
    with fallback to APP_INITIALIZATION_STATE JSON for coordinates/place_id.
    """
    if not html_content:
        return None

    # Extract from rendered HTML (primary source - works with current Google Maps)
    name = _extract_name(html_content)
    rating = _extract_rating(html_content)
    reviews_count = _extract_reviews_count(html_content)
    address = _extract_address(html_content)
    website = _extract_website(html_content)
    phone = _extract_phone(html_content)
    categories = _extract_categories(html_content)

    # Extract coordinates and place_id from APP_INITIALIZATION_STATE (still embedded there)
    init_data = _extract_from_app_init_state(html_content)

    place_id = init_data.get('place_id')
    coordinates = init_data.get('coordinates')

    # Use legacy data as fallback for any missing fields
    if not name:
        name = init_data.get('name')
    if not rating:
        rating = init_data.get('rating')
    if not reviews_count:
        reviews_count = init_data.get('reviews_count')
    if not address:
        address = init_data.get('address')
    if not website:
        website = init_data.get('website')
    if not categories:
        categories = init_data.get('categories')

    # Build result, filtering None values
    place_details = {}
    if name:
        place_details['name'] = name
    if place_id:
        place_details['place_id'] = place_id
    if coordinates:
        place_details['coordinates'] = coordinates
    if address:
        place_details['address'] = address
    if rating is not None:
        place_details['rating'] = rating
    if reviews_count is not None:
        place_details['reviews_count'] = reviews_count
    if categories:
        place_details['categories'] = categories
    if website:
        place_details['website'] = website
    if phone:
        place_details['phone'] = phone

    return place_details if place_details else None


if __name__ == '__main__':
    try:
        with open('debug_place_page.html', 'r', encoding='utf-8') as f:
            sample_html = f.read()

        extracted_info = extract_place_data(sample_html)

        if extracted_info:
            print("Extracted Place Data:")
            print(json.dumps(extracted_info, indent=2, ensure_ascii=False))
        else:
            print("Could not extract data from the HTML.")

    except FileNotFoundError:
        print("debug_place_page.html not found.")
    except Exception as e:
        print(f"Error: {e}")

# GoogleMaps-n8n-Scraper

## Overview

Automated Google Maps data scraping workflow that combines an n8n automation pipeline with a Python-based scraper.

The n8n workflow takes a city, category, and count as input, then uses a Groq LLM to generate a list of neighborhoods/areas for that city. It loops through each location, sends a request to the scraper, filters results for businesses that have a website, scrapes their websites for contact info (email, social media), processes the data, and exports everything to Google Sheets.
## Credits

The Python scraper (`gmaps_scraper_server/`) is based on [google-maps-scraper](https://github.com/conor-is-my-name/google-maps-scraper) by [conor-is-my-name](https://github.com/conor-is-my-name).
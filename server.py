import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

SERPAPI_BASE_URL = "https://serpapi.com/search"
SERPAPI_KEY = os.environ.get("SERPER_API_KEY", "")

mcp = FastMCP("Google Maps Reviews")

http_client = httpx.AsyncClient(timeout=30.0)


async def _serpapi_request(params: dict) -> dict:
    """Make a request to SerpAPI and return the JSON response."""
    if not SERPAPI_KEY:
        raise ValueError("SERPER_API_KEY environment variable is not set")
    params["api_key"] = SERPAPI_KEY
    params["output"] = "json"
    response = await http_client.get(SERPAPI_BASE_URL, params=params)
    response.raise_for_status()
    return response.json()


@mcp.tool
async def search_google_maps(
    query: str,
    location: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    zoom: int = 14,
    language: str = "en",
    country: Optional[str] = None,
    start: int = 0,
) -> dict:
    """Search Google Maps for businesses and places.

    Args:
        query: Search query (e.g. "pizza", "dentist", "hotels")
        location: Named location (e.g. "New York, NY", "London, UK")
        latitude: GPS latitude for coordinate-based search
        longitude: GPS longitude for coordinate-based search
        zoom: Map zoom level 3-30 (default 14)
        language: Language code (default "en")
        country: Country code for localized results (e.g. "us", "uk")
        start: Pagination offset (increment by 20 for next page)
    """
    params = {"engine": "google_maps", "q": query, "hl": language, "start": start}

    if latitude is not None and longitude is not None:
        params["ll"] = f"@{latitude},{longitude},{zoom}z"
    elif location:
        params["location"] = location

    if country:
        params["gl"] = country

    data = await _serpapi_request(params)

    results = data.get("local_results", [])
    return {
        "query": query,
        "location": location or f"@{latitude},{longitude}" if latitude else None,
        "total_results": len(results),
        "results": results,
        "has_next_page": "serpapi_pagination" in data,
        "next_start": start + 20 if "serpapi_pagination" in data else None,
    }


@mcp.tool
async def get_place_details(
    place_id: str,
    language: str = "en",
) -> dict:
    """Get detailed information about a specific place on Google Maps.

    Args:
        place_id: The Google Maps place_id (from search results)
        language: Language code (default "en")
    """
    params = {
        "engine": "google_maps",
        "type": "place",
        "place_id": place_id,
        "hl": language,
    }
    data = await _serpapi_request(params)
    place_results = data.get("place_results", data)
    return place_results


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)

import asyncio
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.responses import JSONResponse

load_dotenv()

SERPAPI_BASE_URL = "https://serpapi.com/search"
SERPAPI_KEY = os.environ.get("SERPER_API_KEY", "")

mcp = FastMCP("Google Maps Reviews")

http_client = httpx.AsyncClient(timeout=30.0)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})


async def _serpapi_request(params: dict) -> dict:
    """Make a request to SerpAPI and return the JSON response."""
    if not SERPAPI_KEY:
        raise ValueError("SERPER_API_KEY environment variable is not set")
    params["api_key"] = SERPAPI_KEY
    params["output"] = "json"
    response = await http_client.get(SERPAPI_BASE_URL, params=params)
    response.raise_for_status()
    return response.json()


async def _geocode_location(location: str) -> tuple[float, float] | None:
    """Resolve a location name to GPS coordinates using SerpAPI locations endpoint."""
    response = await http_client.get(
        "https://serpapi.com/locations.json",
        params={"q": location, "limit": 1},
    )
    results = response.json()
    if results:
        gps = results[0].get("gps")
        if gps:
            return gps[1], gps[0]  # SerpAPI returns [lng, lat], we need (lat, lng)
    return None


@mcp.tool
async def search_google_maps(
    query: str,
    location: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    zoom: int = 14,
    language: str = "en",
    start: int = 0,
) -> dict:
    """Search Google Maps for businesses and places.

    Args:
        query: Search query (e.g. "pizza", "dentist", "hotels")
        location: Named location (e.g. "New York, NY", "Rabat, Morocco"). Auto-geocoded to coordinates.
        latitude: GPS latitude for coordinate-based search (overrides location)
        longitude: GPS longitude for coordinate-based search (overrides location)
        zoom: Map zoom level 3-30 (default 14)
        language: Language code (default "en")
        start: Pagination offset (increment by 20 for next page)
    """
    params = {"engine": "google_maps", "q": query, "hl": language, "start": start}

    if latitude is not None and longitude is not None:
        params["ll"] = f"@{latitude},{longitude},{zoom}z"
    elif location:
        coords = await _geocode_location(location)
        if coords:
            params["ll"] = f"@{coords[0]},{coords[1]},{zoom}z"

    data = await _serpapi_request(params)

    results = data.get("local_results", [])
    return {
        "query": query,
        "location": location or (f"@{latitude},{longitude}" if latitude else None),
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


async def _fetch_reviews_page(
    place_id: str,
    sort_by: str = "qualityScore",
    language: str = "en",
    num: int = 20,
    next_page_token: Optional[str] = None,
    topic_id: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    """Fetch a single page of reviews for a place."""
    params = {
        "engine": "google_maps_reviews",
        "place_id": place_id,
        "sort_by": sort_by,
        "hl": language,
    }
    # SerpAPI: `num` must NOT be sent on the first page unless
    # next_page_token, topic_id, or query is also set.
    if next_page_token:
        params["next_page_token"] = next_page_token
        params["num"] = num
    if topic_id:
        params["topic_id"] = topic_id
        params["num"] = num
    if query:
        params["query"] = query
        params["num"] = num
    return await _serpapi_request(params)


async def _fetch_reviews_auto_paginate(
    place_id: str,
    max_reviews: int = 20,
    sort_by: str = "qualityScore",
    language: str = "en",
    topic_id: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    """Fetch reviews with auto-pagination up to max_reviews (max 100)."""
    max_reviews = min(max_reviews, 100)
    all_reviews = []
    place_info = None
    topics = None
    next_token = None

    while len(all_reviews) < max_reviews:
        remaining = max_reviews - len(all_reviews)
        page_size = min(remaining, 20)

        data = await _fetch_reviews_page(
            place_id=place_id,
            sort_by=sort_by,
            language=language,
            num=page_size,
            next_page_token=next_token,
            topic_id=topic_id,
            query=query,
        )

        if place_info is None:
            place_info = data.get("place_info", {})
        if topics is None:
            topics = data.get("topics", [])

        reviews = data.get("reviews", [])
        if not reviews:
            break

        all_reviews.extend(reviews)

        pagination = data.get("serpapi_pagination", {})
        next_token = pagination.get("next_page_token")
        if not next_token:
            break

    return {
        "place_info": place_info,
        "topics": topics,
        "reviews": all_reviews[:max_reviews],
        "total_fetched": len(all_reviews[:max_reviews]),
    }


@mcp.tool
async def get_place_reviews(
    place_id: str,
    max_reviews: int = 20,
    sort_by: str = "qualityScore",
    language: str = "en",
    topic_id: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    """Get reviews for a specific place on Google Maps with auto-pagination.

    Args:
        place_id: The Google Maps place_id (e.g. "ChIJ...") from search results
        max_reviews: Maximum number of reviews to fetch (default 20, max 100)
        sort_by: Sort order - "qualityScore", "newestFirst", "ratingHigh", "ratingLow"
        language: Language code (default "en")
        topic_id: Filter reviews by topic ID
        query: Filter reviews by text search
    """
    return await _fetch_reviews_auto_paginate(
        place_id=place_id,
        max_reviews=max_reviews,
        sort_by=sort_by,
        language=language,
        topic_id=topic_id,
        query=query,
    )


@mcp.tool
async def search_and_review(
    query: str,
    location: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    max_reviews: int = 20,
    sort_by: str = "qualityScore",
    language: str = "en",
) -> dict:
    """Search for a place by name and location, then fetch its reviews in one call.

    Finds the top matching place and returns its reviews.

    Args:
        query: Business name or search query (e.g. "Starbucks Times Square")
        location: Named location (e.g. "New York, NY")
        latitude: GPS latitude for coordinate-based search
        longitude: GPS longitude for coordinate-based search
        max_reviews: Maximum number of reviews to fetch (default 20, max 100)
        sort_by: Sort order - "qualityScore", "newestFirst", "ratingHigh", "ratingLow"
        language: Language code (default "en")
    """
    search_result = await search_google_maps(
        query=query,
        location=location,
        latitude=latitude,
        longitude=longitude,
        language=language,
    )

    results = search_result.get("results", [])
    if not results:
        return {"error": f"No places found for query '{query}'", "results": []}

    top_place = results[0]
    pid = top_place.get("place_id")
    if not pid:
        return {"error": "Top result has no place_id", "place": top_place}

    reviews_data = await _fetch_reviews_auto_paginate(
        place_id=pid,
        max_reviews=max_reviews,
        sort_by=sort_by,
        language=language,
    )

    return {
        "place": top_place,
        "reviews": reviews_data,
    }


@mcp.tool
async def bulk_fetch_reviews(
    place_ids: list[str],
    max_reviews_per_place: int = 100,
    sort_by: str = "newestFirst",
    language: str = "en",
) -> dict:
    """Fetch reviews for multiple places concurrently.

    Runs all locations in parallel, each auto-paginating up to max_reviews_per_place.
    Individual failures don't crash the batch — partial results are returned.

    Args:
        place_ids: List of Google Maps place_id strings (e.g. "ChIJ...") from search results
        max_reviews_per_place: Max reviews per place (default 100, max 100)
        sort_by: Sort order - "qualityScore", "newestFirst", "ratingHigh", "ratingLow"
        language: Language code (default "en")
    """

    async def _fetch_one(pid: str) -> dict:
        try:
            return await _fetch_reviews_auto_paginate(
                place_id=pid,
                max_reviews=max_reviews_per_place,
                sort_by=sort_by,
                language=language,
            )
        except Exception as e:
            return {"error": str(e), "place_id": pid}

    results = await asyncio.gather(*[_fetch_one(pid) for pid in place_ids])

    return {
        "total_places": len(place_ids),
        "results": {pid: result for pid, result in zip(place_ids, results)},
    }


@mcp.tool
async def search_and_bulk_review(
    query: str,
    location: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    max_reviews_per_place: int = 100,
    sort_by: str = "newestFirst",
    language: str = "en",
) -> dict:
    """Search for places and fetch reviews for ALL matching results concurrently.

    Combines search + bulk review in one call. Useful for monitoring all businesses
    matching a query in a region.

    Args:
        query: Search query (e.g. "pizza", "dentist")
        location: Named location (e.g. "New York, NY")
        latitude: GPS latitude for coordinate-based search
        longitude: GPS longitude for coordinate-based search
        max_reviews_per_place: Max reviews per place (default 100, max 100)
        sort_by: Sort order - "qualityScore", "newestFirst", "ratingHigh", "ratingLow"
        language: Language code (default "en")
    """
    search_result = await search_google_maps(
        query=query,
        location=location,
        latitude=latitude,
        longitude=longitude,
        language=language,
    )

    places = search_result.get("results", [])
    if not places:
        return {"error": f"No places found for query '{query}'", "results": {}}

    place_ids = [p["place_id"] for p in places if p.get("place_id")]
    if not place_ids:
        return {"error": "No place_ids found in search results", "places": places}

    reviews_result = await bulk_fetch_reviews(
        place_ids=place_ids,
        max_reviews_per_place=max_reviews_per_place,
        sort_by=sort_by,
        language=language,
    )

    # Merge place info with reviews
    place_lookup = {p.get("place_id"): p for p in places if p.get("place_id")}
    enriched = {}
    for pid, review_data in reviews_result["results"].items():
        enriched[pid] = {
            "place": place_lookup.get(pid, {}),
            "reviews": review_data,
        }

    return {
        "query": query,
        "total_places": len(place_ids),
        "results": enriched,
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)

# fantasyai/aurora/data_collector.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List
import logging
from datetime import date, timedelta
import os

import requests

from ..config import AURORA_CONFIG

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
COINGECKO_MARKET_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
NASA_APOD_URL = "https://api.nasa.gov/planetary/apod"


@dataclass
class RawDataset:
    source: str         # e.g., "open_meteo", "coingecko", "nasa_apod"
    domain: str         # "climate", "market", "astronomy"
    payload: Any        # arbitrary structure
    meta: Dict[str, Any]


# ---------- CLIMATE (Open-Meteo) ----------

def fetch_open_meteo_temp_daily(
    latitude: float,
    longitude: float,
    days_back: int = 30,
    location_name: str | None = None,
) -> RawDataset:
    """
    Fetch recent daily max temperature from Open-Meteo.
    No API key required.
    """
    end = date.today()
    start = end - timedelta(days=days_back)

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "temperature_2m_max",
        "timezone": "auto",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }

    logger.info("Requesting Open-Meteo climate data: %s", params)
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    times = daily.get("time", [])
    temps = daily.get("temperature_2m_max", [])

    payload = {
        "time": times,
        "temperature_2m_max": temps,
    }

    meta = {
        "latitude": latitude,
        "longitude": longitude,
        "days_back": days_back,
        "location_name": location_name or "unknown",
    }

    return RawDataset(
        source="open_meteo",
        domain="climate",
        payload=payload,
        meta=meta,
    )


# ---------- MARKETS (CoinGecko) ----------

def fetch_coingecko_market_timeseries(
    coin_id: str,
    vs_currency: str = "usd",
    days: int = 30,
) -> RawDataset:
    """
    Fetch recent price series from CoinGecko.
    No API key needed but subject to rate limits.
    """
    url = COINGECKO_MARKET_URL.format(coin_id=coin_id)
    params = {
        "vs_currency": vs_currency,
        "days": days,
        "interval": "daily",
    }
    logger.info("Requesting CoinGecko market data: %s %s", coin_id, params)
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    # "prices": [[timestamp_ms, price], ...]
    prices = data.get("prices", [])
    times: List[str] = []
    values: List[float] = []

    for ts, price in prices:
        # ts is in ms; convert to ISO-ish string
        # we don't need perfect ISO; it's just an ID
        times.append(str(ts))
        values.append(float(price))

    payload = {
        "time": times,
        "price": values,
    }

    meta = {
        "coin_id": coin_id,
        "vs_currency": vs_currency,
        "days": days,
    }

    return RawDataset(
        source="coingecko",
        domain="market",
        payload=payload,
        meta=meta,
    )


# ---------- ASTRONOMY (NASA APOD) ----------

def fetch_nasa_apod_recent(days_back: int = 10) -> RawDataset | None:
    """
    Fetch recent NASA APOD metadata for the last N days.
    Requires NASA_API_KEY environment variable.
    We'll treat 'video' entries etc. as 'weird events' for now.
    """
    api_key = os.getenv("NASA_API_KEY")
    if not api_key:
        logger.warning("NASA_API_KEY not set; skipping astronomy/APOD data.")
        return None

    end = date.today()
    start = end - timedelta(days=days_back)
    params = {
        "api_key": api_key,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }

    logger.info("Requesting NASA APOD data: %s", params)
    resp = requests.get(NASA_APOD_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    # APOD returns a list of daily entries when date range used
    if not isinstance(data, list):
        data = [data]

    payload = {
        "entries": data,   # each entry has title, date, media_type, explanation, url, etc.
    }

    meta = {
        "days_back": days_back,
        "source": "nasa_apod",
    }

    return RawDataset(
        source="nasa_apod",
        domain="astronomy",
        payload=payload,
        meta=meta,
    )


# ---------- AGGREGATION ----------

def collect_all_sources() -> List[RawDataset]:
    datasets: List[RawDataset] = []

    # Climate
    if "open_meteo" in AURORA_CONFIG.climate_sources:
        key_locations = [
            {"name": "Nashville", "lat": 36.1627, "lon": -86.7816},
            {"name": "Yosemite", "lat": 37.8651, "lon": -119.5383},
        ]
        for loc in key_locations:
            ds = fetch_open_meteo_temp_daily(
                loc["lat"],
                loc["lon"],
                days_back=30,
                location_name=loc["name"],
            )
            datasets.append(ds)

    # Markets
    for src in AURORA_CONFIG.market_sources:
        if src == "coingecko_btcusd":
            datasets.append(fetch_coingecko_market_timeseries("bitcoin", "usd", days=60))
        elif src == "coingecko_ethusd":
            datasets.append(fetch_coingecko_market_timeseries("ethereum", "usd", days=60))
        # easy to add more tickers later

    # Astronomy
    if "nasa_apod" in AURORA_CONFIG.astronomy_sources:
        apod_ds = fetch_nasa_apod_recent(days_back=14)
        if apod_ds is not None:
            datasets.append(apod_ds)

    logger.info("Aurora collected %d datasets across domains.", len(datasets))
    return datasets


def data_collector_stage(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pipeline stage wrapper.
    """
    ctx["raw_datasets"] = collect_all_sources()
    return ctx

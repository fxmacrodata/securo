import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app.core.config import get_settings
from app.providers.base import FxRateProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://api.fxmacrodata.com/v1"

# Currencies FXMacroData publishes USD rates for. Anything else in
# `supported_currencies` is skipped rather than requested.
FXMACRODATA_CURRENCIES = frozenset(
    {
        "AUD", "BRL", "CAD", "CHF", "CNH", "CNY", "DKK", "EUR", "GBP", "HUF", "ILS",
        "JPY", "KRW", "MYR", "NGN", "NOK", "NZD", "PEN", "SEK", "THB", "TWD",
    }
)

# Reference rates are only published on business days, and some publishers
# pause for longer holidays. Rows come back most recent first, so a year-wide
# window with limit=1 returns the latest rate on or before the requested date
# in one request. The window has to be explicit: without start_date the API
# defaults to the last 365 days counted from today, not from end_date.
LOOKBACK_DAYS = 366


class FxMacroDataProvider(FxRateProvider):
    """FX rate provider using FXMacroData's central-bank reference rates.

    The API serves one pair per request, so each supported currency is fetched
    as its own USD/<code> series and the latest row on or before the requested
    date is used.
    """

    @property
    def name(self) -> str:
        return "fxmacrodata"

    def _api_key(self) -> str:
        return get_settings().fxmacrodata_api_key

    def _quote_currencies(self) -> list[str]:
        supported = get_settings().supported_currencies.split(",")
        return [code for code in supported if code in FXMACRODATA_CURRENCIES]

    async def fetch_latest(self) -> dict[str, Decimal]:
        return await self._fetch_rates({"limit": 1})

    async def fetch_historical(self, target_date: date) -> dict[str, Decimal]:
        params = {
            "start_date": (target_date - timedelta(days=LOOKBACK_DAYS)).isoformat(),
            "end_date": target_date.isoformat(),
            "limit": 1,
        }
        return await self._fetch_rates(params)

    async def _fetch_rates(self, params: dict) -> dict[str, Decimal]:
        api_key = self._api_key()
        if not api_key:
            raise ValueError("fxmacrodata_api_key not configured")
        currencies = self._quote_currencies()
        headers = {"X-API-Key": api_key}
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            results = await asyncio.gather(
                *(self._fetch_one(client, code, params) for code in currencies),
                return_exceptions=True,
            )
        errors = [result for result in results if isinstance(result, Exception)]
        if errors and len(errors) == len(results):
            raise errors[0]
        rates: dict[str, Decimal] = {}
        for code, result in zip(currencies, results):
            # One failing pair should not discard the rates that did come back.
            if isinstance(result, Exception):
                logger.warning("FXMacroData USD/%s request failed: %s", code, result)
            elif result is not None:
                rates[code] = result
        return rates

    async def _fetch_one(
        self, client: httpx.AsyncClient, code: str, params: dict
    ) -> Decimal | None:
        resp = await client.get(f"{BASE_URL}/forex/usd/{code.lower()}", params=params)
        resp.raise_for_status()
        rows = resp.json().get("data") or []
        # Rows come back most recent first.
        for row in rows:
            if row.get("val") is not None:
                return Decimal(str(row["val"]))
        logger.info("FXMacroData returned no USD/%s rate for %s", code, params)
        return None

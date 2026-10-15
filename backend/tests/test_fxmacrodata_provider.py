"""Tests for the FXMacroData FX rate provider (mocked HTTP calls)."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.fxmacrodata import FxMacroDataProvider


def _response(rows: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"base": "USD", "data": rows}
    resp.raise_for_status = MagicMock()
    return resp


def _mock_client(rows_by_quote: dict[str, list[dict]]) -> MagicMock:
    async def _get(url, params=None):
        return _response(rows_by_quote[url.rsplit("/", 1)[-1]])

    client = MagicMock()
    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _settings(mock_settings: MagicMock, supported: str, api_key: str = "test-key") -> None:
    mock_settings.return_value.fxmacrodata_api_key = api_key
    mock_settings.return_value.supported_currencies = supported


@pytest.mark.asyncio
async def test_fetch_latest():
    client = _mock_client({
        "brl": [{"date": "2026-10-14", "val": 5.4321}],
        "eur": [{"date": "2026-10-14", "val": 0.8612}],
    })

    with patch("app.providers.fxmacrodata.get_settings") as mock_settings, \
         patch("app.providers.fxmacrodata.httpx.AsyncClient", return_value=client) as client_cls:
        _settings(mock_settings, "USD,BRL,EUR")
        rates = await FxMacroDataProvider().fetch_latest()

    assert rates == {"BRL": Decimal("5.4321"), "EUR": Decimal("0.8612")}
    assert client_cls.call_args.kwargs["headers"] == {"X-API-Key": "test-key"}
    urls = sorted(call.args[0] for call in client.get.call_args_list)
    assert urls[0].endswith("/forex/usd/brl")
    assert urls[1].endswith("/forex/usd/eur")
    assert client.get.call_args.kwargs["params"] == {"limit": 1}


@pytest.mark.asyncio
async def test_skips_currencies_without_coverage():
    client = _mock_client({"gbp": [{"date": "2026-10-14", "val": 0.7468}]})

    with patch("app.providers.fxmacrodata.get_settings") as mock_settings, \
         patch("app.providers.fxmacrodata.httpx.AsyncClient", return_value=client):
        _settings(mock_settings, "USD,GBP,ARS,MXN")
        rates = await FxMacroDataProvider().fetch_latest()

    assert rates == {"GBP": Decimal("0.7468")}
    assert client.get.call_count == 1


@pytest.mark.asyncio
async def test_fetch_historical_uses_latest_row_on_or_before_date():
    client = _mock_client({
        "jpy": [
            {"date": "2026-10-09", "val": 151.25},
            {"date": "2026-10-08", "val": 150.9},
        ],
    })

    with patch("app.providers.fxmacrodata.get_settings") as mock_settings, \
         patch("app.providers.fxmacrodata.httpx.AsyncClient", return_value=client):
        _settings(mock_settings, "USD,JPY")
        # A Sunday: the latest published rate is Friday's.
        rates = await FxMacroDataProvider().fetch_historical(date(2026, 10, 11))

    assert rates == {"JPY": Decimal("151.25")}
    assert client.get.call_args.kwargs["params"] == {
        "start_date": "2026-10-04",
        "end_date": "2026-10-11",
        "limit": 1,
    }


@pytest.mark.asyncio
async def test_currency_with_no_rows_is_omitted():
    client = _mock_client({
        "cad": [],
        "chf": [{"date": "2026-10-14", "val": 0.7981}],
    })

    with patch("app.providers.fxmacrodata.get_settings") as mock_settings, \
         patch("app.providers.fxmacrodata.httpx.AsyncClient", return_value=client):
        _settings(mock_settings, "CAD,CHF")
        rates = await FxMacroDataProvider().fetch_latest()

    assert rates == {"CHF": Decimal("0.7981")}


@pytest.mark.asyncio
async def test_no_api_key_raises():
    with patch("app.providers.fxmacrodata.get_settings") as mock_settings:
        _settings(mock_settings, "USD,EUR", api_key="")
        with pytest.raises(ValueError, match="not configured"):
            await FxMacroDataProvider().fetch_latest()


@pytest.mark.parametrize(
    "setting, expected",
    [("fxmacrodata", "fxmacrodata"), ("openexchangerates", "openexchangerates"),
     ("unknown", "openexchangerates")],
)
def test_service_selects_configured_provider(setting, expected):
    from app.services import fx_rate_service

    with patch("app.services.fx_rate_service.get_settings") as mock_settings:
        mock_settings.return_value.fx_rate_provider = setting
        assert fx_rate_service._build_provider().name == expected

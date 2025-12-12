"""
Tests for donation wallet monitoring script.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import responses

# Import the module under test
from scripts.check_donations import (
    BTC_DEFAULT_ADDRESS,
    COINGECKO_IDS,
    ETH_DEFAULT_ADDRESS,
    MAX_BTC_EVENTS_TO_SCAN,
    MAX_ETH_EVENTS_TO_SCAN,
    MAX_SEEN_BTC_TXIDS,
    MAX_SEEN_ETH_EVENTS,
    STABLE_USD_TOKENS,
    DonationEvent,
    _coingecko_price_usd,
    _format_amount,
    _load_state,
    _save_state,
    estimate_usd_value,
    fetch_btc_events,
    fetch_eth_events,
    main,
    send_umami_event,
)


class TestDonationEvent:
    """Test DonationEvent dataclass."""

    def test_dedupe_id_with_event_id(self):
        """Event ID takes precedence for deduplication."""
        event = DonationEvent(
            chain="eth",
            symbol="USDC",
            amount=100.0,
            txid="0xabc123",
            timestamp=1700000000,
            explorer_url="https://etherscan.io/tx/0xabc123",
            event_id="0xabc123:42",
        )
        assert event.dedupe_id() == "0xabc123:42"

    def test_dedupe_id_without_event_id(self):
        """Falls back to txid when no event_id."""
        event = DonationEvent(
            chain="btc",
            symbol="BTC",
            amount=0.001,
            txid="abc123def456",
            timestamp=1700000000,
            explorer_url="https://blockstream.info/tx/abc123def456",
        )
        assert event.dedupe_id() == "abc123def456"


class TestBtcEventParsing:
    """Test BTC transaction parsing from Blockstream API."""

    @responses.activate
    def test_fetch_btc_events_success(self):
        """Parse confirmed BTC transactions correctly."""
        address = BTC_DEFAULT_ADDRESS
        mock_response = [
            {
                "txid": "tx1",
                "status": {"confirmed": True, "block_time": 1700000000},
                "vout": [
                    {"value": 100000, "scriptpubkey_address": address},
                    {"value": 50000, "scriptpubkey_address": "other_addr"},
                ],
            },
            {
                "txid": "tx2",
                "status": {"confirmed": True, "block_time": 1699999000},
                "vout": [
                    {"value": 200000, "scriptpubkey_address": address},
                ],
            },
        ]
        responses.add(
            responses.GET,
            f"https://blockstream.info/api/address/{address}/txs",
            json=mock_response,
            status=200,
        )

        events = fetch_btc_events(address, max_events=10)

        assert len(events) == 2
        assert events[0].chain == "btc"
        assert events[0].symbol == "BTC"
        assert events[0].amount == 0.001  # 100000 sats
        assert events[0].txid == "tx1"
        assert events[1].amount == 0.002  # 200000 sats

    @responses.activate
    def test_fetch_btc_events_unconfirmed_skipped(self):
        """Unconfirmed transactions should be skipped."""
        address = BTC_DEFAULT_ADDRESS
        mock_response = [
            {
                "txid": "unconfirmed_tx",
                "status": {"confirmed": False},
                "vout": [{"value": 100000, "scriptpubkey_address": address}],
            },
        ]
        responses.add(
            responses.GET,
            f"https://blockstream.info/api/address/{address}/txs",
            json=mock_response,
            status=200,
        )

        events = fetch_btc_events(address)
        assert len(events) == 0

    @responses.activate
    def test_fetch_btc_events_zero_value_skipped(self):
        """Transactions with zero received value should be skipped."""
        address = BTC_DEFAULT_ADDRESS
        mock_response = [
            {
                "txid": "zero_value_tx",
                "status": {"confirmed": True, "block_time": 1700000000},
                "vout": [{"value": 0, "scriptpubkey_address": address}],
            },
        ]
        responses.add(
            responses.GET,
            f"https://blockstream.info/api/address/{address}/txs",
            json=mock_response,
            status=200,
        )

        events = fetch_btc_events(address)
        assert len(events) == 0

    @responses.activate
    def test_fetch_btc_events_empty_response(self):
        """Handle empty API response gracefully."""
        responses.add(
            responses.GET,
            f"https://blockstream.info/api/address/{BTC_DEFAULT_ADDRESS}/txs",
            json=[],
            status=200,
        )

        events = fetch_btc_events(BTC_DEFAULT_ADDRESS)
        assert events == []


class TestEthEventParsing:
    """Test ETH/ERC20 transaction parsing from Etherscan API."""

    @responses.activate
    def test_fetch_eth_native_transactions(self):
        """Parse native ETH transactions correctly."""
        address = ETH_DEFAULT_ADDRESS
        mock_native_txs = {
            "status": "1",
            "result": [
                {
                    "hash": "0xhash1",
                    "to": address.lower(),
                    "value": "1000000000000000000",  # 1 ETH
                    "timeStamp": "1700000000",
                    "from": "0xsender1",
                },
            ],
        }
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_native_txs,
            status=200,
        )

        events = fetch_eth_events(address, api_key="test", tokens_filter=None)

        assert len(events) == 1
        assert events[0].chain == "eth"
        assert events[0].symbol == "ETH"
        assert events[0].amount == 1.0
        assert events[0].txid == "0xhash1"
        assert events[0].from_address == "0xsender1"

    @responses.activate
    def test_fetch_eth_erc20_transactions(self):
        """Parse ERC20 token transfers with logIndex deduplication."""
        address = ETH_DEFAULT_ADDRESS
        # First call: native transactions (empty)
        mock_native = {"status": "1", "result": []}
        # Second call: token transactions
        mock_tokens = {
            "status": "1",
            "result": [
                {
                    "hash": "0xtokenhash",
                    "to": address.lower(),
                    "value": "100000000",  # 100 USDC (6 decimals)
                    "tokenSymbol": "USDC",
                    "tokenDecimal": "6",
                    "timeStamp": "1700000000",
                    "from": "0xsender",
                    "logIndex": "42",
                },
            ],
        }
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_native,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_tokens,
            status=200,
        )

        events = fetch_eth_events(address, api_key="test", tokens_filter=["USDC"])

        # Find the USDC event
        usdc_events = [e for e in events if e.symbol == "USDC"]
        assert len(usdc_events) == 1
        assert usdc_events[0].amount == 100.0
        assert usdc_events[0].event_id == "0xtokenhash:42"  # logIndex dedupe

    @responses.activate
    def test_fetch_eth_error_transactions_skipped(self):
        """Failed ETH transactions should be skipped."""
        address = ETH_DEFAULT_ADDRESS
        mock_response = {
            "status": "1",
            "result": [
                {
                    "hash": "0xfailed",
                    "to": address.lower(),
                    "value": "1000000000000000000",
                    "timeStamp": "1700000000",
                    "from": "0xsender",
                    "isError": "1",
                },
            ],
        }
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_response,
            status=200,
        )

        events = fetch_eth_events(address, api_key="test", tokens_filter=None)
        assert len(events) == 0

    @responses.activate
    def test_fetch_eth_case_insensitive_address(self):
        """Address matching should be case-insensitive."""
        address = ETH_DEFAULT_ADDRESS
        mock_response = {
            "status": "1",
            "result": [
                {
                    "hash": "0xhash",
                    "to": address.upper(),  # Different case
                    "value": "1000000000000000000",
                    "timeStamp": "1700000000",
                    "from": "0xsender",
                },
            ],
        }
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_response,
            status=200,
        )

        events = fetch_eth_events(address, api_key="test", tokens_filter=None)
        assert len(events) == 1


class TestTokenFilterParsing:
    """Test ETH_DONATION_TOKENS filter logic."""

    @responses.activate
    def test_tokens_filter_none_eth_only(self):
        """When tokens_filter is None, only native ETH is watched."""
        address = ETH_DEFAULT_ADDRESS
        mock_native = {
            "status": "1",
            "result": [
                {
                    "hash": "0xeth",
                    "to": address.lower(),
                    "value": "1000000000000000000",
                    "timeStamp": "1700000000",
                    "from": "0xsender",
                },
            ],
        }
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_native,
            status=200,
        )

        events = fetch_eth_events(address, api_key="test", tokens_filter=None)

        # Should only have native ETH, no token call made
        assert len(events) == 1
        assert events[0].symbol == "ETH"

    @responses.activate
    def test_tokens_filter_empty_list_accepts_all(self):
        """Empty tokens_filter [] means accept all ERC20 tokens."""
        address = ETH_DEFAULT_ADDRESS
        mock_native = {"status": "1", "result": []}
        mock_tokens = {
            "status": "1",
            "result": [
                {
                    "hash": "0xtoken1",
                    "to": address.lower(),
                    "value": "100000000",
                    "tokenSymbol": "RANDOM",
                    "tokenDecimal": "8",
                    "timeStamp": "1700000000",
                    "from": "0xsender",
                    "logIndex": "1",
                },
            ],
        }
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_native,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_tokens,
            status=200,
        )

        events = fetch_eth_events(address, api_key="test", tokens_filter=[])

        # Should accept any token
        random_events = [e for e in events if e.symbol == "RANDOM"]
        assert len(random_events) == 1

    @responses.activate
    def test_tokens_filter_specific_symbols(self):
        """Specific token filter only accepts listed symbols."""
        address = ETH_DEFAULT_ADDRESS
        mock_native = {"status": "1", "result": []}
        mock_tokens = {
            "status": "1",
            "result": [
                {
                    "hash": "0xusdc",
                    "to": address.lower(),
                    "value": "100000000",
                    "tokenSymbol": "USDC",
                    "tokenDecimal": "6",
                    "timeStamp": "1700000000",
                    "from": "0xsender",
                    "logIndex": "1",
                },
                {
                    "hash": "0xother",
                    "to": address.lower(),
                    "value": "100000000",
                    "tokenSymbol": "RANDOMTOKEN",
                    "tokenDecimal": "18",
                    "timeStamp": "1700000000",
                    "from": "0xsender",
                    "logIndex": "2",
                },
            ],
        }
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_native,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_tokens,
            status=200,
        )

        events = fetch_eth_events(address, api_key="test", tokens_filter=["USDC", "USDT"])

        # Only USDC should be included
        symbols = [e.symbol for e in events]
        assert "USDC" in symbols
        assert "RANDOMTOKEN" not in symbols


class TestUsdEstimation:
    """Test USD value estimation for donations."""

    def test_stablecoin_direct_value(self):
        """Stablecoins should return amount directly as USD."""
        for symbol in ["USDC", "USDT", "DAI"]:
            event = DonationEvent(
                chain="eth",
                symbol=symbol,
                amount=123.45,
                txid="0xtest",
                timestamp=1700000000,
                explorer_url="https://etherscan.io/tx/0xtest",
            )
            usd = estimate_usd_value(event)
            assert usd == 123.45

    def test_stablecoin_symbols_constant(self):
        """Verify STABLE_USD_TOKENS contains expected stablecoins."""
        expected = {"USDC", "USDT", "DAI", "TUSD", "BUSD", "USDP"}
        assert expected == STABLE_USD_TOKENS

    @responses.activate
    def test_btc_price_lookup(self):
        """BTC price lookup via CoinGecko."""
        mock_response = {
            "market_data": {
                "current_price": {"usd": 50000.0},
            },
        }
        responses.add(
            responses.GET,
            "https://api.coingecko.com/api/v3/coins/bitcoin/history",
            json=mock_response,
            status=200,
        )

        event = DonationEvent(
            chain="btc",
            symbol="BTC",
            amount=0.1,
            txid="test_tx",
            timestamp=1700000000,
            explorer_url="https://blockstream.info/tx/test_tx",
        )
        usd = estimate_usd_value(event)
        assert usd == 5000.0  # 0.1 BTC * $50000

    def test_unknown_symbol_returns_none(self):
        """Unknown symbols without CoinGecko ID return None."""
        event = DonationEvent(
            chain="eth",
            symbol="UNKNOWN_TOKEN",
            amount=100.0,
            txid="0xtest",
            timestamp=1700000000,
            explorer_url="https://etherscan.io/tx/0xtest",
        )
        # No API call needed since COINGECKO_IDS doesn't have this symbol
        usd = estimate_usd_value(event)
        assert usd is None


class TestStateDeduplication:
    """Test state persistence and deduplication logic."""

    def test_load_state_empty_file(self, tmp_path: Path):
        """Loading non-existent state returns default structure."""
        state_path = tmp_path / "state.json"
        state = _load_state(state_path)

        assert state["btc"]["seen_txids"] == []
        assert state["eth"]["seen_event_ids"] == []
        assert state["last_run"] is None

    def test_load_state_existing_file(self, tmp_path: Path):
        """Loading existing state file returns saved data."""
        state_path = tmp_path / "state.json"
        existing_state = {
            "btc": {"seen_txids": ["tx1", "tx2"]},
            "eth": {"seen_event_ids": ["ev1"]},
            "last_run": "2024-01-01T00:00:00Z",
        }
        state_path.write_text(json.dumps(existing_state))

        state = _load_state(state_path)

        assert state["btc"]["seen_txids"] == ["tx1", "tx2"]
        assert state["eth"]["seen_event_ids"] == ["ev1"]

    def test_save_state_updates_last_run(self, tmp_path: Path):
        """Saving state updates the last_run timestamp."""
        state_path = tmp_path / "state.json"
        state: Dict[str, Any] = {
            "btc": {"seen_txids": []},
            "eth": {"seen_event_ids": []},
            "last_run": None,
        }

        _save_state(state_path, state)

        assert state_path.exists()
        saved = json.loads(state_path.read_text())
        assert saved["last_run"] is not None
        assert "T" in saved["last_run"]  # ISO format check


class TestFormatAmount:
    """Test amount formatting."""

    def test_large_amounts(self):
        """Large amounts use general format."""
        assert _format_amount(1.5) == "1.5"
        assert _format_amount(10.0) == "10"
        assert _format_amount(123.456789) == "123.457"

    def test_small_amounts(self):
        """Small amounts preserve precision."""
        assert _format_amount(0.00001) == "0.00001"
        assert _format_amount(0.00000001) == "0.00000001"

    def test_trailing_zeros_stripped(self):
        """Trailing zeros should be stripped."""
        result = _format_amount(0.1)
        assert result == "0.1"


class TestConstants:
    """Test that constants are properly defined."""

    def test_default_addresses(self):
        """Default addresses should match sponsors.html."""
        assert BTC_DEFAULT_ADDRESS == "bc1qcxzuuykxx46g6u70fa9sytty53vv74eakch5hk"
        assert ETH_DEFAULT_ADDRESS == "0x107F501699EFb65562bf97FBE06144Cd431ECc9D"

    def test_scanning_limits(self):
        """Scanning limits should be reasonable."""
        assert MAX_BTC_EVENTS_TO_SCAN == 20
        assert MAX_ETH_EVENTS_TO_SCAN == 25
        assert MAX_SEEN_BTC_TXIDS == 200
        assert MAX_SEEN_ETH_EVENTS == 400

    def test_coingecko_ids(self):
        """CoinGecko IDs should be configured for BTC and ETH."""
        assert COINGECKO_IDS["BTC"] == "bitcoin"
        assert COINGECKO_IDS["ETH"] == "ethereum"


class TestMainFunction:
    """Test the main entry point."""

    @responses.activate
    @patch("scripts.check_donations.AlertManager")
    def test_main_no_new_donations(self, mock_alert_manager, tmp_path: Path):
        """Main should return 0 when no new donations detected."""
        state_path = tmp_path / "state.json"
        btc_addr = BTC_DEFAULT_ADDRESS

        # Mock empty responses
        responses.add(
            responses.GET,
            f"https://blockstream.info/api/address/{btc_addr}/txs",
            json=[],
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json={"status": "1", "result": []},
            status=200,
        )

        result = main(["--state-path", str(state_path), "--dry-run"])

        assert result == 0
        assert state_path.exists()

    @responses.activate
    @patch("scripts.check_donations.AlertManager")
    def test_main_with_dry_run(self, mock_alert_manager, tmp_path: Path):
        """Dry run should not send alerts."""
        state_path = tmp_path / "state.json"
        btc_addr = BTC_DEFAULT_ADDRESS

        # Mock a new BTC transaction
        mock_btc = [
            {
                "txid": "new_tx",
                "status": {"confirmed": True, "block_time": 1700000000},
                "vout": [{"value": 100000, "scriptpubkey_address": btc_addr}],
            },
        ]
        responses.add(
            responses.GET,
            f"https://blockstream.info/api/address/{btc_addr}/txs",
            json=mock_btc,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json={"status": "1", "result": []},
            status=200,
        )

        result = main(["--state-path", str(state_path), "--dry-run"])

        assert result == 0
        # Alert manager should not have been called
        mock_alert_manager.return_value.alert.assert_not_called()

    @patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": ""}, clear=False)
    @patch("scripts.check_donations.log")
    def test_main_warns_missing_webhook(self, mock_log, tmp_path: Path):
        """Should warn when DISCORD_WEBHOOK_URL is not set."""
        state_path = tmp_path / "state.json"

        # We need to mock the HTTP calls to avoid real network requests
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"https://blockstream.info/api/address/{BTC_DEFAULT_ADDRESS}/txs",
                json=[],
                status=200,
            )
            rsps.add(
                responses.GET,
                "https://api.etherscan.io/api",
                json={"status": "1", "result": []},
                status=200,
            )

            main(["--state-path", str(state_path), "--dry-run"])

        # Check that warning was logged
        mock_log.assert_any_call("WARNING: DISCORD_WEBHOOK_URL not set, alerts disabled")


class TestEdgeCases:
    """Test edge cases and malformed data handling."""

    @responses.activate
    def test_btc_missing_vout(self):
        """Handle BTC transactions with missing vout gracefully."""
        address = BTC_DEFAULT_ADDRESS
        mock_response = [
            {
                "txid": "no_vout",
                "status": {"confirmed": True, "block_time": 1700000000},
                # Missing "vout" key
            },
        ]
        responses.add(
            responses.GET,
            f"https://blockstream.info/api/address/{address}/txs",
            json=mock_response,
            status=200,
        )

        events = fetch_btc_events(address)
        assert len(events) == 0

    @responses.activate
    def test_eth_malformed_value(self):
        """Handle ETH transactions with malformed value gracefully."""
        address = ETH_DEFAULT_ADDRESS
        mock_response = {
            "status": "1",
            "result": [
                {
                    "hash": "0xmalformed",
                    "to": address.lower(),
                    "value": "not_a_number",  # Invalid
                    "timeStamp": "1700000000",
                    "from": "0xsender",
                },
            ],
        }
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_response,
            status=200,
        )

        events = fetch_eth_events(address, api_key="test", tokens_filter=None)
        assert len(events) == 0

    @responses.activate
    def test_etherscan_api_error_status(self):
        """Handle Etherscan API error status gracefully."""
        address = ETH_DEFAULT_ADDRESS
        mock_response = {
            "status": "0",  # Error status
            "message": "NOTOK",
            "result": "Error message",
        }
        responses.add(
            responses.GET,
            "https://api.etherscan.io/api",
            json=mock_response,
            status=200,
        )

        events = fetch_eth_events(address, api_key="test", tokens_filter=None)
        assert events == []

    @responses.activate
    def test_coingecko_rate_limit_returns_none(self):
        """CoinGecko rate limit returns None (logged and handled gracefully)."""
        # HTTP 429 errors are caught internally and logged, returning None
        responses.add(
            responses.GET,
            "https://api.coingecko.com/api/v3/coins/bitcoin/history",
            status=429,  # Rate limited
        )

        # Clear any cached prices
        from scripts.check_donations import _price_cache
        _price_cache.clear()

        price = _coingecko_price_usd("BTC", 1700000000)
        # Rate limit results in None (graceful degradation)
        assert price is None
        assert len(responses.calls) == 1

    @responses.activate
    def test_coingecko_success_caches_price(self):
        """CoinGecko successful lookup is cached."""
        responses.add(
            responses.GET,
            "https://api.coingecko.com/api/v3/coins/bitcoin/history",
            json={"market_data": {"current_price": {"usd": 50000.0}}},
            status=200,
        )

        # Clear any cached prices
        from scripts.check_donations import _price_cache
        _price_cache.clear()

        price1 = _coingecko_price_usd("BTC", 1700000000)
        assert price1 == 50000.0

        # Second call should use cache (no new request)
        price2 = _coingecko_price_usd("BTC", 1700000000)
        assert price2 == 50000.0
        assert len(responses.calls) == 1  # Only one API call made


class TestUmamiIntegration:
    """Test Umami event sending."""

    @responses.activate
    @patch.dict(
        os.environ,
        {
            "UMAMI_WEBSITE_ID": "test-website-id",
            "UMAMI_HOST_URL": "https://analytics.example.com",
        },
        clear=False,
    )
    def test_send_umami_event_success(self):
        """Successfully send Umami event."""
        responses.add(
            responses.POST,
            "https://analytics.example.com/api/send",
            json={"ok": True},
            status=200,
        )

        event = DonationEvent(
            chain="btc",
            symbol="BTC",
            amount=0.001,
            txid="test_tx",
            timestamp=1700000000,
            explorer_url="https://blockstream.info/tx/test_tx",
        )
        result = send_umami_event(event, usd_value=50.0)

        assert result is True
        assert len(responses.calls) == 1

        # Verify payload structure
        body = json.loads(responses.calls[0].request.body)
        assert body["type"] == "event"
        assert body["payload"]["name"] == "donation-received"
        assert body["payload"]["data"]["chain"] == "btc"
        assert body["payload"]["data"]["usd"] == 50.0

    @patch.dict(os.environ, {}, clear=True)
    def test_send_umami_event_missing_config(self):
        """Return False when Umami is not configured."""
        event = DonationEvent(
            chain="btc",
            symbol="BTC",
            amount=0.001,
            txid="test_tx",
            timestamp=1700000000,
            explorer_url="https://blockstream.info/tx/test_tx",
        )
        result = send_umami_event(event)

        assert result is False

#!/usr/bin/env python3
"""
Poll donation wallets and send Discord/Umami alerts.

This is intentionally simple and cloud-friendly:
- Runs in a scheduled GitHub Action.
- Polls BTC (Blockstream) and ETH/ERC20 (Etherscan) address activity.
- Dedupes via a small persisted JSON state file.
- Sends a Discord webhook alert per new donation.
- Optionally mirrors donations into Umami as custom events.

Environment variables (all optional):
  BTC_DONATION_ADDRESS: BTC bech32 address to watch.
  ETH_DONATION_ADDRESS: ETH address to watch.
  ETHERSCAN_API_KEY: Etherscan API key (recommended but optional).
  ETH_DONATION_TOKENS: Comma-separated ERC20 symbols to watch. If empty/unset, ERC20
    transfers are ignored (native ETH only). Set to "ALL" to accept any token.
  DONATIONS_STATE_PATH: Path to persisted state (default: data/donations_state.json).

Umami (optional):
  UMAMI_WEBSITE_ID: Website UUID (same as frontend).
  UMAMI_SCRIPT_URL: Tracker script URL (used to infer host).
  UMAMI_HOST_URL: Explicit host URL (overrides inference).
  UMAMI_COLLECT_ENDPOINT: Tracker endpoint path (default: /api/send).
  UMAMI_HOSTNAME: Hostname to report (default: chinarxiv.org).
  UMAMI_EVENT_URL: URL to report (default: /donation-watch).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

# Ensure repo root is on sys.path when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.alerts import AlertManager  # noqa: E402
from src.file_service import read_json, write_json  # noqa: E402
from src.logging_utils import log  # noqa: E402


BTC_DEFAULT_ADDRESS = "bc1qcxzuuykxx46g6u70fa9sytty53vv74eakch5hk"
ETH_DEFAULT_ADDRESS = "0x107F501699EFb65562bf97FBE06144Cd431ECc9D"

# Pricing helpers (minor gold-plating).
COINGECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum"}
STABLE_USD_TOKENS = {"USDC", "USDT", "DAI", "TUSD", "BUSD", "USDP"}
_price_cache: Dict[Tuple[str, str], float] = {}


@dataclass(frozen=True)
class DonationEvent:
    chain: str  # "btc" | "eth"
    symbol: str  # "BTC" | "ETH" | token symbol
    amount: float
    txid: str
    timestamp: int
    explorer_url: str
    from_address: Optional[str] = None
    event_id: Optional[str] = None  # for ERC20 logIndex dedupe

    def dedupe_id(self) -> str:
        return self.event_id or self.txid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state(state_path: Path) -> Dict[str, Any]:
    if state_path.exists():
        try:
            return read_json(str(state_path))
        except Exception as e:
            log(f"Failed to read state {state_path}: {e}")
    return {
        "btc": {"seen_txids": []},
        "eth": {"seen_event_ids": []},
        "last_run": None,
    }


def _save_state(state_path: Path, state: Dict[str, Any]) -> None:
    state["last_run"] = _now_iso()
    write_json(str(state_path), state)


def _chunk(seq: Sequence[Any], n: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def fetch_btc_events(address: str, max_events: int = 20) -> List[DonationEvent]:
    url = f"https://blockstream.info/api/address/{address}/txs"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    txs = resp.json()

    events: List[DonationEvent] = []
    for tx in txs[:max_events]:
        status = tx.get("status", {})
        if not status.get("confirmed"):
            continue

        vouts = tx.get("vout", []) or []
        received_sats = sum(
            int(v.get("value", 0))
            for v in vouts
            if v.get("scriptpubkey_address") == address
        )
        if received_sats <= 0:
            continue

        events.append(
            DonationEvent(
                chain="btc",
                symbol="BTC",
                amount=received_sats / 1e8,
                txid=tx.get("txid", ""),
                timestamp=int(status.get("block_time") or 0),
                explorer_url=f"https://blockstream.info/tx/{tx.get('txid','')}",
            )
        )

    return events


def _etherscan_get(
    action: str,
    address: str,
    api_key: str,
    extra_params: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, str] = {
        "module": "account",
        "action": action,
        "address": address,
        "sort": "desc",
        "apikey": api_key or "YourApiKeyToken",
    }
    if extra_params:
        params.update(extra_params)

    resp = requests.get("https://api.etherscan.io/api", params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "1":
        return []
    result = data.get("result", [])
    if isinstance(result, list):
        return result
    return []


def fetch_eth_events(
    address: str,
    api_key: str,
    tokens_filter: Optional[Sequence[str]] = None,
    max_events: int = 50,
) -> List[DonationEvent]:
    addr_lower = address.lower()

    native_txs = _etherscan_get(
        "txlist",
        address,
        api_key,
        extra_params={"page": "1", "offset": str(max_events)},
    )
    events: List[DonationEvent] = []
    for tx in native_txs:
        to_addr = (tx.get("to") or "").lower()
        if to_addr != addr_lower:
            continue
        if tx.get("isError") not in (None, "0", 0):
            continue
        try:
            value_wei = int(tx.get("value") or 0)
        except (TypeError, ValueError):
            continue
        if value_wei <= 0:
            continue

        tx_hash = tx.get("hash") or ""
        ts = int(tx.get("timeStamp") or 0)
        events.append(
            DonationEvent(
                chain="eth",
                symbol="ETH",
                amount=value_wei / 1e18,
                txid=tx_hash,
                timestamp=ts,
                from_address=tx.get("from"),
                explorer_url=f"https://etherscan.io/tx/{tx_hash}",
            )
        )

    # If no token filter is provided, only watch native ETH.
    if tokens_filter is None:
        return events

    token_txs = _etherscan_get(
        "tokentx",
        address,
        api_key,
        extra_params={"page": "1", "offset": str(max_events), "sort": "desc"},
    )

    # tokens_filter == [] means accept all ERC20s; otherwise filter to the provided symbols.
    allowed_tokens = set(t.strip().upper() for t in tokens_filter) if tokens_filter else None

    for tx in token_txs:
        to_addr = (tx.get("to") or "").lower()
        if to_addr != addr_lower:
            continue
        symbol = (tx.get("tokenSymbol") or "").upper()
        if allowed_tokens is not None and symbol not in allowed_tokens:
            continue

        try:
            decimals = int(tx.get("tokenDecimal") or 0)
            raw_value = int(tx.get("value") or 0)
        except (TypeError, ValueError):
            continue
        if raw_value <= 0:
            continue

        tx_hash = tx.get("hash") or ""
        log_index = tx.get("logIndex")
        event_id = f"{tx_hash}:{log_index}" if log_index is not None else tx_hash
        ts = int(tx.get("timeStamp") or 0)
        amount = raw_value / (10**decimals) if decimals else float(raw_value)

        events.append(
            DonationEvent(
                chain="eth",
                symbol=symbol or "ERC20",
                amount=amount,
                txid=tx_hash,
                timestamp=ts,
                from_address=tx.get("from"),
                explorer_url=f"https://etherscan.io/tx/{tx_hash}",
                event_id=event_id,
            )
        )

    return events


def send_umami_event(event: DonationEvent, usd_value: Optional[float] = None) -> bool:
    website_id = os.getenv("UMAMI_WEBSITE_ID")
    script_url = os.getenv("UMAMI_SCRIPT_URL")
    host_url = os.getenv("UMAMI_HOST_URL")
    if not website_id or not (host_url or script_url):
        return False

    if not host_url and script_url:
        host_url = script_url.rsplit("/", 1)[0]

    collect_endpoint = os.getenv("UMAMI_COLLECT_ENDPOINT", "/api/send")
    endpoint = host_url.rstrip("/") + collect_endpoint

    payload = {
        "website": website_id,
        "hostname": os.getenv("UMAMI_HOSTNAME", "chinarxiv.org"),
        "url": os.getenv("UMAMI_EVENT_URL", "/donation-watch"),
        "title": "Donation Watch",
        "language": "en",
        "screen": "0x0",
        "referrer": "",
        "tag": "server",
        "name": "donation-received",
        "data": {
            "chain": event.chain,
            "symbol": event.symbol,
            "amount": round(event.amount, 8),
            "txid": event.txid,
        },
    }
    if usd_value is not None:
        payload["data"]["usd"] = round(usd_value, 2)

    try:
        resp = requests.post(
            endpoint,
            json={"type": "event", "payload": payload},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log(f"Umami event send failed: {e}")
        return False


def _format_amount(amount: float) -> str:
    if amount >= 1:
        return f"{amount:.6g}"
    return f"{amount:.8f}".rstrip("0").rstrip(".")


def _coingecko_price_usd(symbol: str, timestamp: int) -> Optional[float]:
    """Get approximate USD price at the given UTC date."""
    coin_id = COINGECKO_IDS.get(symbol.upper())
    if not coin_id or not timestamp:
        return None

    date_str = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%d-%m-%Y")
    cache_key = (coin_id, date_str)
    if cache_key in _price_cache:
        return _price_cache[cache_key]

    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/history"
    try:
        resp = requests.get(
            url,
            params={"date": date_str, "localization": "false"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        price = (
            data.get("market_data", {})
            .get("current_price", {})
            .get("usd")
        )
        if isinstance(price, (int, float)) and price > 0:
            _price_cache[cache_key] = float(price)
            return float(price)
    except requests.RequestException as e:
        log(f"CoinGecko price lookup failed for {symbol} {date_str}: {e}")
    return None


def estimate_usd_value(event: DonationEvent) -> Optional[float]:
    sym = event.symbol.upper()
    if sym in STABLE_USD_TOKENS:
        return float(event.amount)
    price = _coingecko_price_usd(sym, event.timestamp)
    if price is None:
        return None
    return float(event.amount) * price


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Poll donation wallets for new deposits.")
    parser.add_argument(
        "--state-path",
        default=os.getenv("DONATIONS_STATE_PATH", "data/donations_state.json"),
        help="Path to persisted donation state JSON.",
    )
    parser.add_argument(
        "--btc-address",
        default=os.getenv("BTC_DONATION_ADDRESS", BTC_DEFAULT_ADDRESS),
        help="BTC address to watch.",
    )
    parser.add_argument(
        "--eth-address",
        default=os.getenv("ETH_DONATION_ADDRESS", ETH_DEFAULT_ADDRESS),
        help="ETH address to watch.",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=20,
        help="Max recent events to scan per chain.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not send alerts.")

    args = parser.parse_args(argv)

    state_path = Path(args.state_path)
    state = _load_state(state_path)
    seen_btc: List[str] = list(state.get("btc", {}).get("seen_txids", []))
    seen_eth: List[str] = list(state.get("eth", {}).get("seen_event_ids", []))

    new_events: List[DonationEvent] = []

    # BTC
    try:
        btc_events = fetch_btc_events(args.btc_address, max_events=args.max_events)
        for ev in btc_events:
            if ev.txid and ev.txid not in seen_btc:
                new_events.append(ev)
    except Exception as e:
        log(f"BTC poll failed: {e}")

    # ETH + ERC20
    try:
        api_key = os.getenv("ETHERSCAN_API_KEY", "")
        raw_tokens = os.getenv("ETH_DONATION_TOKENS")
        tokens_filter: Optional[List[str]] = None
        if raw_tokens is not None:
            cleaned = [t.strip().upper() for t in raw_tokens.split(",") if t.strip()]
            if not cleaned:
                tokens_filter = None
            elif len(cleaned) == 1 and cleaned[0] in {"ALL", "*"}:
                tokens_filter = []
            else:
                tokens_filter = cleaned
        eth_events = fetch_eth_events(
            args.eth_address,
            api_key=api_key,
            tokens_filter=tokens_filter,
            max_events=max(args.max_events, 25),
        )
        for ev in eth_events:
            dedupe_id = ev.dedupe_id()
            if dedupe_id and dedupe_id not in seen_eth:
                new_events.append(ev)
    except Exception as e:
        log(f"ETH poll failed: {e}")

    if not new_events:
        log("No new donations detected.")
        _save_state(state_path, state)
        return 0

    alerts = AlertManager()
    for ev in sorted(new_events, key=lambda e: e.timestamp):
        amount_str = _format_amount(ev.amount)
        msg = f"{amount_str} {ev.symbol} received on {ev.chain.upper()} wallet."

        usd_value = estimate_usd_value(ev)
        if usd_value is not None:
            msg += f"\n≈ ${usd_value:,.2f} USD (at time)"
        if ev.explorer_url:
            msg += f"\n{ev.explorer_url}"

        if not args.dry_run:
            alerts.alert(
                level="success",
                title="New donation received",
                message=msg,
                immediate=True,
                chain=ev.chain.upper(),
                symbol=ev.symbol,
                amount=amount_str,
                usd=f"{usd_value:,.2f}" if usd_value is not None else "n/a",
                txid=ev.txid,
                from_address=ev.from_address or "n/a",
            )
            send_umami_event(ev, usd_value=usd_value)

        if ev.chain == "btc":
            seen_btc.append(ev.txid)
        else:
            seen_eth.append(ev.dedupe_id())

    # Trim seen lists to avoid unbounded growth.
    seen_btc = list(dict.fromkeys(seen_btc))[-200:]
    seen_eth = list(dict.fromkeys(seen_eth))[-400:]

    state["btc"] = {"seen_txids": seen_btc}
    state["eth"] = {"seen_event_ids": seen_eth}
    _save_state(state_path, state)
    log(f"Recorded {len(new_events)} new donation(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

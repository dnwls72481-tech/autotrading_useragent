"""
Agent 전용 거래소 클라이언트 (standalone)
ccxt 기반 통합 거래소 클라이언트 - app 의존성 없는 독립 버전
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Dict, List

import ccxt.async_support as ccxt

logger = logging.getLogger("exchange_client")


class ExchangeError(Exception):
    """거래소 관련 오류"""
    pass


@dataclass
class Position:
    """포지션 정보"""
    symbol: str
    side: str       # "LONG" or "SHORT"
    qty: Decimal
    entry_price: Decimal
    leverage: int
    unrealized_pnl: Decimal = Decimal("0")
    stop_loss: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None


class AgentExchangeClient:
    """ccxt 기반 통합 거래소 클라이언트 (Agent 전용)"""

    def __init__(self, exchange_id: str, api_key: str, api_secret: str, api_passphrase: str = ""):
        self.exchange_id = exchange_id.lower()

        exchange_class = getattr(ccxt, self.exchange_id)
        exchange_config = {
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "future"},
        }
        if api_passphrase:
            exchange_config["password"] = api_passphrase
        self.exchange: ccxt.Exchange = exchange_class(exchange_config)

        self._markets_loaded = False
        self._instrument_cache: Dict[str, Dict] = {}

    # 거래소별 심볼 별칭
    _EXCHANGE_SYMBOL_MAP: Dict[str, Dict[str, str]] = {
        "okx": {"XAUTUSDT": "XAUUSDT"},
        "binance": {"XAUTUSDT": "XAUUSDT"},
        "bitget": {"XAUTUSDT": "XAUUSDT"},
    }

    def _normalize_symbol(self, symbol: str) -> str:
        return self._EXCHANGE_SYMBOL_MAP.get(self.exchange_id, {}).get(symbol, symbol)

    def _to_ccxt_symbol(self, symbol: str) -> str:
        """'BTCUSDT' → 'BTC/USDT:USDT'"""
        symbol = self._normalize_symbol(symbol)
        if "/" in symbol:
            return symbol
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT"
        return symbol

    async def _ensure_markets(self):
        if not self._markets_loaded:
            await self.exchange.load_markets()
            self._markets_loaded = True

    def get_instrument_info(self, symbol: str) -> Dict:
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]
        if self._markets_loaded:
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            try:
                market = self.exchange.market(ccxt_symbol)
                precision = market.get("precision", {})
                limits = market.get("limits", {})
                amount_limits = limits.get("amount", {})
                cost_limits = limits.get("cost", {})

                qty_step_raw = precision.get("amount")
                tick_size_raw = precision.get("price")

                qty_step = Decimal(str(qty_step_raw)) if qty_step_raw else Decimal("0.001")
                tick_size = Decimal(str(tick_size_raw)) if tick_size_raw else Decimal("0.01")
                min_qty = Decimal(str(amount_limits.get("min") or "0.001"))
                min_amt = Decimal(str(cost_limits.get("min") or "5"))

                info = {
                    "qtyStep": qty_step,
                    "tickSize": tick_size,
                    "minOrderQty": min_qty,
                    "minOrderAmt": min_amt,
                }
                self._instrument_cache[symbol] = info
                return info
            except Exception as e:
                logger.warning(f"Failed to get market info for {symbol}: {e}")
        return {
            "qtyStep": Decimal("0.001"),
            "tickSize": Decimal("0.01"),
            "minOrderQty": Decimal("0.001"),
            "minOrderAmt": Decimal("5"),
        }

    def round_quantity(self, symbol: str, qty: Decimal) -> Decimal:
        info = self.get_instrument_info(symbol)
        qty_step = info["qtyStep"]
        return (qty / qty_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * qty_step

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        info = self.get_instrument_info(symbol)
        tick = info["tickSize"]
        return (price / tick).quantize(Decimal("1"), rounding=ROUND_DOWN) * tick

    # ===== 잔고 조회 =====

    async def get_balance(self) -> Optional[Decimal]:
        try:
            await self._ensure_markets()
            balance = await self.exchange.fetch_balance()
            usdt = balance.get("USDT", {})
            free = usdt.get("free", 0) or 0
            return Decimal(str(free))
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None

    # ===== 현재가 조회 =====

    async def get_current_price(self, symbol: str) -> Optional[Decimal]:
        try:
            await self._ensure_markets()
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            ticker = await self.exchange.fetch_ticker(ccxt_symbol)
            last = ticker.get("last")
            return Decimal(str(last)) if last else None
        except Exception as e:
            logger.error(f"Failed to get current price for {symbol}: {e}")
            return None

    # ===== 포지션 조회 =====

    async def get_position(self, symbol: str) -> Optional[Position]:
        try:
            await self._ensure_markets()
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            positions = await self.exchange.fetch_positions([ccxt_symbol])
            for pos in positions:
                contracts = Decimal(str(pos.get("contracts") or 0))
                if contracts <= 0:
                    continue
                side = "LONG" if pos["side"] == "long" else "SHORT"
                sl_raw = pos.get("stopLossPrice")
                mark_raw = pos.get("markPrice")
                return Position(
                    symbol=symbol,
                    side=side,
                    qty=contracts,
                    entry_price=Decimal(str(pos["entryPrice"])),
                    leverage=int(pos.get("leverage") or 1),
                    unrealized_pnl=Decimal(str(pos.get("unrealizedPnl") or 0)),
                    stop_loss=Decimal(str(sl_raw)) if sl_raw else None,
                    mark_price=Decimal(str(mark_raw)) if mark_raw else None,
                )
            return None
        except Exception as e:
            logger.error(f"Failed to get position: {e}")
            raise ExchangeError(f"get_position failed for {symbol}: {e}")

    async def get_all_positions(self) -> List[Dict]:
        try:
            await self._ensure_markets()
            positions = await self.exchange.fetch_positions()
            return [p for p in positions if Decimal(str(p.get("contracts") or 0)) > 0]
        except Exception as e:
            logger.error(f"Failed to get all positions: {e}")
            return []

    # ===== 포지션 모드 / 레버리지 =====

    async def switch_to_one_way_mode(self, symbol: str) -> bool:
        try:
            await self._ensure_markets()
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            if self.exchange_id == "bybit":
                await self.exchange.set_position_mode(False, ccxt_symbol)
            elif hasattr(self.exchange, "set_position_mode"):
                await self.exchange.set_position_mode(False, ccxt_symbol)
            logger.info(f"One-way mode set for {symbol}")
            return True
        except Exception as e:
            if "not modified" in str(e).lower() or "already" in str(e).lower():
                return True
            logger.error(f"Failed to switch to one-way mode: {e}")
            return False

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        try:
            await self._ensure_markets()
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            await self.exchange.set_leverage(leverage, ccxt_symbol)
            logger.info(f"Leverage set: {leverage}x for {symbol}")
            return True
        except Exception as e:
            if "110043" in str(e):
                return True
            logger.error(f"Failed to set leverage: {e}")
            return False

    # ===== 주문 실행 =====

    async def place_market_order(self, symbol: str, side: str, qty: Decimal) -> Optional[str]:
        try:
            await self._ensure_markets()
            rounded_qty = self.round_quantity(symbol, qty)
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            ccxt_side = side.lower()
            order = await self.exchange.create_order(
                ccxt_symbol, "market", ccxt_side, float(rounded_qty)
            )
            order_id = order["id"]
            logger.info(f"Market order placed: {side} {rounded_qty} {symbol} (ID: {order_id})")
            return order_id
        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
            raise ExchangeError(f"place_market_order failed for {symbol}: {e}")

    async def place_limit_order(self, symbol: str, side: str, qty: Decimal, price: Decimal) -> Optional[str]:
        try:
            await self._ensure_markets()
            rounded_qty = self.round_quantity(symbol, qty)
            rounded_price = self.round_price(symbol, price)
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            order = await self.exchange.create_order(
                ccxt_symbol, "limit", side.lower(), float(rounded_qty), float(rounded_price)
            )
            order_id = order["id"]
            logger.info(f"Limit order placed: {side} {rounded_qty} {symbol} @ {rounded_price} (ID: {order_id})")
            return order_id
        except Exception as e:
            logger.error(f"Failed to place limit order: {e}")
            raise ExchangeError(f"place_limit_order failed for {symbol}: {e}")

    async def place_tp_order(self, symbol: str, side: str, qty: Decimal, price: Decimal) -> Optional[str]:
        try:
            await self._ensure_markets()
            rounded_qty = self.round_quantity(symbol, qty)
            rounded_price = self.round_price(symbol, price)
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            order = await self.exchange.create_order(
                ccxt_symbol, "limit", side.lower(), float(rounded_qty), float(rounded_price),
                {"reduceOnly": True, "timeInForce": "GTC"}
            )
            order_id = order["id"]
            logger.info(f"TP order placed: {side} {rounded_qty} {symbol} @ {rounded_price} (ID: {order_id})")
            return order_id
        except Exception as e:
            logger.error(f"Failed to place TP order: {e}")
            raise ExchangeError(f"place_tp_order failed for {symbol}: {e}")

    async def set_stop_loss(self, symbol: str, stop_loss_price: Decimal) -> bool:
        try:
            await self._ensure_markets()
            rounded_sl = self.round_price(symbol, stop_loss_price)
            if self.exchange_id == "bybit":
                await self.exchange.private_post_v5_position_trading_stop({
                    "category": "linear",
                    "symbol": symbol,
                    "stopLoss": str(rounded_sl),
                    "slTriggerBy": "LastPrice",
                    "positionIdx": 0,
                })
            else:
                ccxt_symbol = self._to_ccxt_symbol(symbol)
                position = await self.get_position(symbol)
                if not position:
                    logger.warning(f"No position found to set SL for {symbol}")
                    return False
                close_side = "sell" if position.side == "LONG" else "buy"
                await self.exchange.create_order(
                    ccxt_symbol, "stop_market", close_side, float(position.qty), None,
                    {"stopPrice": float(rounded_sl), "reduceOnly": True}
                )
            logger.info(f"Stop loss set: {symbol} @ {rounded_sl}")
            return True
        except Exception as e:
            err_str = str(e)
            if "34040" in err_str or "not modified" in err_str.lower():
                logger.info(f"Stop loss already set at same price for {symbol} @ {stop_loss_price}")
                return True
            logger.error(f"Failed to set stop loss for {symbol}: {e}")
            raise ExchangeError(f"set_stop_loss failed for {symbol}: {e}")

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            await self._ensure_markets()
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            await self.exchange.cancel_order(order_id, ccxt_symbol)
            logger.info(f"Order {order_id} cancelled")
            return True
        except Exception as e:
            if "110001" in str(e):
                return True
            logger.error(f"Failed to cancel order {order_id}: {e}")
            raise ExchangeError(f"cancel_order failed for {order_id}: {e}")

    async def cancel_all_orders(self, symbol: str) -> bool:
        try:
            await self._ensure_markets()
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            await self.exchange.cancel_all_orders(ccxt_symbol)
            logger.info(f"All open orders cancelled for {symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel all orders for {symbol}: {e}")
            return False

    async def close_position(self, symbol: str, side: str) -> bool:
        try:
            await self._ensure_markets()
            ccxt_symbol = self._to_ccxt_symbol(symbol)
            close_side = "sell" if side == "LONG" else "buy"
            position = await self.get_position(symbol)
            if not position:
                logger.warning(f"No position to close for {symbol}")
                return False
            order = await self.exchange.create_order(
                ccxt_symbol, "market", close_side, float(position.qty),
                None, {"reduceOnly": True}
            )
            logger.info(f"Position closed: {side} {symbol} (ID: {order['id']})")
            return True
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            raise ExchangeError(f"close_position failed for {symbol}: {e}")

    async def close(self):
        try:
            await self.exchange.close()
        except Exception:
            pass
        finally:
            try:
                self.exchange.apiKey = ""
                self.exchange.secret = ""
                self.exchange.password = ""
            except Exception:
                pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

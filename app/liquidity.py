"""Independent 15m liquidity-sweep scanner and fixed-margin demo ledger."""
from __future__ import annotations
import json, logging, math, sqlite3, time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo
from app.strategy import Candle

log = logging.getLogger(__name__)
BAR_MS = 15 * 60 * 1000

@dataclass
class LiquidityConfig:
    ema_length: int = 50
    use_ema_filter: bool = True
    use_session_filter: bool = False
    pivot_length: int = 5
    stored_levels: int = 20
    min_sweep_distance_atr: float = 0.10
    reclaim_rule: str = "Close Back Inside"
    min_wick_percent: float = 0.35
    max_body_percent: float = 0.65
    min_candle_range_atr: float = 0.20
    require_bullish_body: bool = True
    require_bearish_body: bool = True
    confirmation_window: int = 2
    require_midline_break: bool = True
    signal_cooldown_bars: int = 10
    atr_length: int = 14
    stop_loss_atr_multiplier: float = 1.50
    take_profit_rr: float = 3.0
    block_signals_in_trade: bool = True

    def __post_init__(self):
        if min(self.ema_length, self.pivot_length, self.atr_length) < 1:
            raise ValueError("EMA, pivot and ATR lengths must be positive")
        if self.stored_levels < 5 or not 0 <= self.confirmation_window <= 10:
            raise ValueError("Invalid liquidity storage or confirmation window")
        if self.signal_cooldown_bars < 0 or self.min_sweep_distance_atr < 0:
            raise ValueError("Invalid cooldown or sweep distance")
        if self.take_profit_rr not in {1.5, 2.0, 3.0, 4.0, 5.0, 6.0} or self.stop_loss_atr_multiplier <= 0:
            raise ValueError("Invalid ATR stop or take-profit RR")

@dataclass(frozen=True)
class LiquiditySignal:
    symbol: str
    side: str
    timestamp: int
    entry: float
    stop: float
    target: float
    risk_reward: float
    score: float
    ema: float
    atr: float

@dataclass
class _Pending:
    index: int
    high: float
    low: float
    mid: float
    level: float
    score: float

class LiquiditySweepStrategy:
    """Closed-bar port of the supplied Pine v6 liquidity-sweep indicator."""
    def __init__(self, symbol: str, config: LiquidityConfig | None = None):
        self.symbol, self.config = symbol, config or LiquidityConfig()
        self.bars: list[Candle] = []
        self.high_levels: list[tuple[float, int]] = []
        self.low_levels: list[tuple[float, int]] = []
        self.pending_bull: _Pending | None = None
        self.pending_bear: _Pending | None = None
        self.last_signal_bar: int | None = None
        self.ema: float | None = None
        self.atr: float | None = None
        self._trs: list[float] = []

    @staticmethod
    def _session_allowed(ts: int) -> bool:
        utc = datetime.fromtimestamp(ts / 1000, timezone.utc)
        for zone, start, end in (("Asia/Tokyo", 9, 18), ("Europe/London", 8, 17),
                                 ("America/New_York", 8, 17)):
            local = utc.astimezone(ZoneInfo(zone))
            if local.weekday() < 5 and (start, 0) <= (local.hour, local.minute) < (end, 0):
                return True
        return False

    @staticmethod
    def _clamp(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

    def _indicators(self, bar: Candle):
        prev = self.bars[-2].close if len(self.bars) > 1 else bar.close
        tr = max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev))
        self._trs.append(tr)
        alpha = 2.0 / (self.config.ema_length + 1.0)
        self.ema = bar.close if self.ema is None else self.ema + alpha * (bar.close - self.ema)
        if self.atr is None:
            if len(self._trs) >= self.config.atr_length:
                self.atr = sum(self._trs[-self.config.atr_length:]) / self.config.atr_length
        else:
            self.atr += (tr - self.atr) / self.config.atr_length
        rng = bar.high - bar.low
        body = abs(bar.close - bar.open)
        up = bar.high - max(bar.open, bar.close)
        down = min(bar.open, bar.close) - bar.low
        return (self.ema, self.atr, body / rng if rng else 0.0,
                up / rng if rng else 0.0, down / rng if rng else 0.0,
                rng / self.atr if self.atr else 0.0)

    def _pivot(self, index: int):
        n = self.config.pivot_length
        center = index - n
        if center < n:
            return
        window = self.bars[center - n:center + n + 1]
        highs, lows = [b.high for b in window], [b.low for b in window]
        if highs.count(max(highs)) == 1 and self.bars[center].high == max(highs):
            self.high_levels.append((self.bars[center].high, center))
            self.high_levels = self.high_levels[-self.config.stored_levels:]
        if lows.count(min(lows)) == 1 and self.bars[center].low == min(lows):
            self.low_levels.append((self.bars[center].low, center))
            self.low_levels = self.low_levels[-self.config.stored_levels:]

    def _score(self, wick, body, range_atr, ema_ok, reclaim, mid_ok):
        c = self.config
        w = self._clamp(wick / c.min_wick_percent * 32, 0, 32) if c.min_wick_percent else 32
        b = self._clamp((c.max_body_percent - body) / c.max_body_percent * 24, 0, 24) if c.max_body_percent else 24
        r = self._clamp(range_atr / c.min_candle_range_atr * 18, 0, 18) if c.min_candle_range_atr else 18
        return self._clamp(w + b + r + (10 if ema_ok else 0) + (10 if reclaim else 0) + (6 if mid_ok else 0), 0, 100)

    def on_bar(self, bar: Candle) -> LiquiditySignal | None:
        if self.bars and bar.timestamp <= self.bars[-1].timestamp:
            return None
        self.bars.append(bar)
        i = len(self.bars) - 1
        ema, atr, body_pct, upper_pct, lower_pct, range_atr = self._indicators(bar)
        self._pivot(i)
        if atr is None or atr <= 0:
            return None
        c = self.config
        min_sweep, min_range = c.min_sweep_distance_atr * atr, c.min_candle_range_atr * atr
        high_hit = next(((p, ix) for p, ix in reversed(self.high_levels)
                         if ix < i and bar.high > p and bar.high - p >= min_sweep), None)
        low_hit = next(((p, ix) for p, ix in reversed(self.low_levels)
                        if ix < i and bar.low < p and p - bar.low >= min_sweep), None)
        mid = (bar.high + bar.low) / 2
        bear_reclaim = bool(high_hit and bar.close < high_hit[0] and
                            (c.reclaim_rule != "Strong Reclaim" or bar.close < mid))
        bull_reclaim = bool(low_hit and bar.close > low_hit[0] and
                            (c.reclaim_rule != "Strong Reclaim" or bar.close > mid))
        valid_sell = bool(high_hit and bear_reclaim and upper_pct >= c.min_wick_percent and
                          body_pct <= c.max_body_percent and bar.high - bar.low >= min_range)
        valid_buy = bool(low_hit and bull_reclaim and lower_pct >= c.min_wick_percent and
                         body_pct <= c.max_body_percent and bar.high - bar.low >= min_range)
        bull_ema = not c.use_ema_filter or bar.close > ema
        bear_ema = not c.use_ema_filter or bar.close < ema
        bull_mid_now = not c.require_midline_break or bar.close > mid
        bear_mid_now = not c.require_midline_break or bar.close < mid
        if valid_buy:
            self.pending_bull = _Pending(i, bar.high, bar.low, mid, low_hit[0],
                self._score(lower_pct, body_pct, range_atr, bull_ema, bull_reclaim, bull_mid_now))
        if valid_sell:
            self.pending_bear = _Pending(i, bar.high, bar.low, mid, high_hit[0],
                self._score(upper_pct, body_pct, range_atr, bear_ema, bear_reclaim, bear_mid_now))
        bull_open = self.pending_bull is not None and i - self.pending_bull.index <= c.confirmation_window
        bear_open = self.pending_bear is not None and i - self.pending_bear.index <= c.confirmation_window
        long_ok = not c.use_ema_filter or bar.close > ema
        short_ok = not c.use_ema_filter or bar.close < ema
        bull_body = not c.require_bullish_body or bar.close > bar.open
        bear_body = not c.require_bearish_body or bar.close < bar.open
        bull_mid = not c.require_midline_break or (self.pending_bull is not None and bar.close > self.pending_bull.mid)
        bear_mid = not c.require_midline_break or (self.pending_bear is not None and bar.close < self.pending_bear.mid)
        cooldown = self.last_signal_bar is None or i - self.last_signal_bar > c.signal_cooldown_bars
        session = not c.use_session_filter or self._session_allowed(bar.timestamp)
        buy = bull_open and bull_body and long_ok and bull_mid and cooldown and session
        sell = bear_open and bear_body and short_ok and bear_mid and cooldown and session and not buy
        signal = None
        if buy or sell:
            pending = self.pending_bull if buy else self.pending_bear
            self.last_signal_bar = i
            distance = atr * c.stop_loss_atr_multiplier
            direction = 1 if buy else -1
            side = "BUY" if buy else "SELL"
            signal = LiquiditySignal(self.symbol, side, bar.timestamp, bar.close,
                bar.close - direction * distance, bar.close + direction * distance * c.take_profit_rr,
                c.take_profit_rr, pending.score if pending else 0.0, ema, atr)
            if buy: self.pending_bull = None
            else: self.pending_bear = None
        if self.pending_bull is not None and i - self.pending_bull.index > c.confirmation_window:
            self.pending_bull = None
        if self.pending_bear is not None and i - self.pending_bear.index > c.confirmation_window:
            self.pending_bear = None
        return signal

@dataclass
class LiquidityPosition:
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    quantity: float
    margin: float
    notional: float
    opened_at: str
    entry_fee: float
    closed: bool = False
    exit_price: float | None = None
    exit_type: str | None = None
    realized_pnl: float = 0.0
    closed_at: str | None = None

@dataclass
class LiquidityDemoAccount:
    initial_balance: float = 100.0
    margin_per_trade: float = 5.0
    leverage: float = 5.0
    fee_rate: float = 0.0005
    balance: float | None = None
    positions: dict[str, LiquidityPosition] = field(default_factory=dict)
    history: list[LiquidityPosition] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if self.balance is None: self.balance = self.initial_balance
        if min(self.initial_balance, self.margin_per_trade, self.leverage) <= 0 or self.fee_rate < 0:
            raise ValueError("Invalid liquidity demo account configuration")
    @property
    def notional(self): return self.margin_per_trade * self.leverage
    @property
    def open_positions(self): return [p for p in self.positions.values() if not p.closed]
    @property
    def margin_used(self): return sum(p.margin for p in self.open_positions)
    @property
    def free_margin(self): return max(0.0, float(self.balance or 0) - self.margin_used)
    @property
    def realized_pnl(self):
        return sum(p.realized_pnl for p in self.history) + sum(p.realized_pnl for p in self.positions.values() if p.closed)

    def open_position(self, signal: LiquiditySignal):
        old = self.positions.get(signal.symbol)
        if old is not None and not old.closed: raise ValueError("Position already open for this symbol")
        if self.free_margin + 1e-9 < self.margin_per_trade: raise ValueError("Insufficient free liquidity demo margin")
        if signal.side == "BUY" and not signal.stop < signal.entry < signal.target: raise ValueError("Invalid BUY levels")
        if signal.side == "SELL" and not signal.target < signal.entry < signal.stop: raise ValueError("Invalid SELL levels")
        if old is not None: self.history.append(old)
        qty = self.notional / signal.entry
        fee = self.notional * self.fee_rate
        self.balance = float(self.balance or 0) - fee
        opened = datetime.fromtimestamp((signal.timestamp + BAR_MS) / 1000, timezone.utc).isoformat()
        pos = LiquidityPosition(signal.symbol, signal.side, signal.entry, signal.stop, signal.target,
                                qty, self.margin_per_trade, self.notional, opened, fee)
        self.positions[signal.symbol] = pos
        self.events.append({"type":"ENTRY","symbol":signal.symbol,"side":signal.side,"price":signal.entry,"time":opened})
        return pos

    def process_candle(self, symbol: str, high: float, low: float, timestamp: int):
        pos = self.positions.get(symbol)
        if pos is None or pos.closed: return None
        if high < low: raise ValueError("Candle high cannot be below low")
        long = pos.side == "BUY"
        stop_hit = low <= pos.stop if long else high >= pos.stop
        tp_hit = high >= pos.target if long else low <= pos.target
        if stop_hit: price, kind = pos.stop, "STOP"
        elif tp_hit: price, kind = pos.target, "TP"
        else: return None
        gross = (price - pos.entry) * pos.quantity * (1 if long else -1)
        exit_fee = pos.quantity * price * self.fee_rate
        net = gross - pos.entry_fee - exit_fee
        self.balance = float(self.balance or 0) + gross - exit_fee
        pos.closed, pos.exit_price, pos.exit_type, pos.realized_pnl = True, price, kind, net
        pos.closed_at = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()
        event = {"type":kind,"symbol":symbol,"side":pos.side,"price":price,"realized_pnl":net,"time":pos.closed_at}
        self.events.append(event)
        return event

class LiquidityStore:
    """Persist liquidity data under its own SQLite key; main demo tables remain untouched."""
    def __init__(self, database_path: str):
        self.database_path = database_path
        if database_path != ":memory:": Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path, timeout=30) as db:
            db.execute("CREATE TABLE IF NOT EXISTS liquidity_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    def load(self):
        with sqlite3.connect(self.database_path, timeout=30) as db:
            rows = dict(db.execute("SELECT key,value FROM liquidity_state"))
        data = json.loads(rows["account"]) if "account" in rows else None
        if data:
            positions = {k:LiquidityPosition(**v) for k,v in data.get("positions",{}).items()}
            account = LiquidityDemoAccount(data.get("initial_balance",100),data.get("margin_per_trade",5),
                data.get("leverage",5),data.get("fee_rate",.0005),data.get("balance",100),positions,
                [LiquidityPosition(**x) for x in data.get("history",[])],data.get("events",[]))
        else: account = LiquidityDemoAccount()
        cfg_data = json.loads(rows["config"]) if "config" in rows else {}
        allowed = set(LiquidityConfig.__dataclass_fields__)
        config = LiquidityConfig(**{k:v for k,v in cfg_data.items() if k in allowed})
        cursors = {str(k):int(v) for k,v in json.loads(rows.get("cursors","{}")).items()}
        return account, config, rows.get("enabled","true")=="true", cursors
    def save(self, account, config, enabled, cursors):
        data = {"initial_balance":account.initial_balance,"margin_per_trade":account.margin_per_trade,
            "leverage":account.leverage,"fee_rate":account.fee_rate,"balance":account.balance,
            "positions":{k:asdict(v) for k,v in account.positions.items()},
            "history":[asdict(v) for v in account.history],"events":account.events[-500:]}
        values = {"account":json.dumps(data,separators=(",",":")),
            "config":json.dumps(asdict(config),separators=(",",":")),
            "enabled":"true" if enabled else "false","cursors":json.dumps(cursors,separators=(",",":"))}
        with sqlite3.connect(self.database_path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany("INSERT INTO liquidity_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",list(values.items()))

def _to_candles(rows):
    out=[]
    for row in rows:
        if len(row)<6: continue
        try:
            ts=int(row[0]); o,h,l,c,v=map(float,row[1:6])
            if not all(math.isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c)<=0 or v<0: continue
            out.append(Candle(ts,o,h,l,c,v))
        except (TypeError,ValueError,OverflowError): continue
    return sorted({x.timestamp:x for x in out}.values(),key=lambda x:x.timestamp)

class LiquidityScanner:
    """Scans the manual WEEX list using 15m candles only."""
    def __init__(self, market_data: Any, account: LiquidityDemoAccount, config: LiquidityConfig,
                 store: LiquidityStore, enabled=True, cursors=None,
                 on_signal: Callable | None=None, on_trade_event: Callable | None=None):
        self.market_data,self.account,self.config,self.store=market_data,account,config,store
        self.enabled=enabled; self.cursors=cursors or {}; self.on_signal=on_signal; self.on_trade_event=on_trade_event
    def scan_once(self, symbols: list[str]):
        if not self.enabled: return {"symbols":0,"signals":0,"events":0,"errors":0}
        signals=events=errors=scanned=0; now=int(time.time()*1000)
        requested=list(dict.fromkeys([*symbols,*(p.symbol for p in self.account.open_positions)]))
        for raw in requested:
            try:
                market=self.market_data.resolve_manual_symbol(raw); symbol=market.symbol
                rows=self.market_data.fetch_ohlcv(symbol,"15m",500)
                bars=[b for b in _to_candles(rows) if b.timestamp+BAR_MS<=now]
                if len(bars)<max(self.config.atr_length,self.config.pivot_length*2+2): continue
                engine=LiquiditySweepStrategy(symbol,self.config); generated={}
                for bar in bars:
                    sig=engine.on_bar(bar)
                    if sig is not None: generated[bar.timestamp]=sig
                cursor=self.cursors.get(symbol)
                if cursor is None: cursor=bars[-2].timestamp if len(bars)>1 else bars[-1].timestamp-BAR_MS
                new_bars=[b for b in bars if b.timestamp>cursor]
                for bar in new_bars:
                    was_open=symbol in self.account.positions and not self.account.positions[symbol].closed
                    if was_open:
                        event=self.account.process_candle(symbol,bar.high,bar.low,bar.timestamp+BAR_MS)
                        if event:
                            events+=1
                            if self.on_trade_event:self.on_trade_event(event)
                    sig=generated.get(bar.timestamp)
                    if sig is not None and (not was_open or not self.config.block_signals_in_trade):
                        position=None
                        try: position=self.account.open_position(sig)
                        except ValueError as exc: log.info("Liquidity demo entry skipped for %s: %s",symbol,exc)
                        signals+=1
                        if self.on_signal:self.on_signal(sig,position)
                    self.cursors[symbol]=bar.timestamp
                    self.store.save(self.account,self.config,self.enabled,self.cursors)
                scanned+=1
            except Exception:
                errors+=1; log.exception("Liquidity scanner failed for %s",raw)
        return {"symbols":scanned,"signals":signals,"events":events,"errors":errors}

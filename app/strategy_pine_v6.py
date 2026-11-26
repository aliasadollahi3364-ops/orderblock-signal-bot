"""Python port of WEEX_OrderBlock_Trendline_Improved_v6.pine.
Closed 4H/15m candles only; independent OB and trendline entry routes.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    def __post_init__(self):
        if self.high < max(self.open,self.close,self.low) or self.low > min(self.open,self.close,self.high):
            raise ValueError("Candle high/low is inconsistent with OHLC.")
        if min(self.open,self.high,self.low,self.close) <= 0 or self.volume < 0:
            raise ValueError("OHLC prices must be positive and volume non-negative.")

@dataclass
class StrategyConfig:
    pivot4:int=3; body_len:int=10; ob_search:int=12; max_stored_ob:int=200
    ob_atr_len:int=14; min_bos_body_atr:float=.65; min_bos_body_avg:float=1.20
    min_break_atr:float=.10; max_ob_candle_range_atr:float=2.50
    min_ob_score:int=70; ob_volume_length:int=20
    pivot15:int=3; max_wait:int=17; atr_len:int=7
    use_trend_filter:bool=True; require_ob_touch:bool=True; use_touch_freshness:bool=True
    max_bars_since_touch:int=14; use_choch_strength:bool=True; min_choch_body_atr:float=.85
    retest_atr:float=.15; use_confirm_strength:bool=True; min_confirm_body_atr:float=.20
    enable_trendline_signals:bool=True; use_sma200_trendline_filter:bool=True
    trendline_touch_tolerance_atr:float=.35; trendline_retest_atr:float=.15
    trendline_pullback_max_wait:int=12
    risk_percent:float=1.; risk_reward:float=2.5; sl_buffer_atr:float=2.
    use_ob_boundary_sl:bool=True; use_trailing_stop:bool=False; trailing_atr_mult:float=1.5
    sma_length:int=200; use_sma200_filter:bool=True; min_tick:float=1e-8
    enable_rsi_divergence:bool=True; rsi_length:int=14
    divergence_pivot_length:int=5; rsi_divergence_lookback:int=20
    def __post_init__(self):
        for key in ("pivot4","body_len","ob_search","max_stored_ob","ob_atr_len","ob_volume_length",
                    "pivot15","max_wait","atr_len","max_bars_since_touch","trendline_pullback_max_wait",
                    "sma_length","rsi_length","divergence_pivot_length","rsi_divergence_lookback"):
            if getattr(self,key)<1: raise ValueError(f"{key} must be positive")
        if self.min_tick<=0: raise ValueError("min_tick must be positive")

@dataclass
class OrderBlock:
    direction:int; top:float; bottom:float; origin_time:int; score:float
    active:bool=True; used:bool=False; touched:bool=False; touch_bar:Optional[int]=None
    has_choch:bool=False; choch_level:Optional[float]=None; choch_bar:Optional[int]=None
    retest_bar:Optional[int]=None; sweep_bar:Optional[int]=None
    sweep_extreme:Optional[float]=None; sweep_confirm_level:Optional[float]=None

@dataclass(frozen=True)
class EarlyWarning:
    symbol:str; side:str; timestamp:int; break_price:float
    trendline_level:Optional[float]; stop:float
    ob_top:Optional[float]; ob_bottom:Optional[float]
    kind:str="CONFIRMED_ENTRY"; risk_reward:float=2.5
    rsi_divergence:str="NONE"; rsi_value:Optional[float]=None

@dataclass(frozen=True)
class Signal:
    symbol:str; side:str; timestamp:int; entry:float; stop:float; atr:float
    ob_time:Optional[int]; ob_top:Optional[float]; ob_bottom:Optional[float]
    trend4h:int; sma200:Optional[float]; method:str="ORDER_BLOCK"
    risk_reward:float=2.5; volume_weighted_price:Optional[float]=None
    rsi_divergence:str="NONE"; rsi_value:Optional[float]=None; ob_score:Optional[float]=None
    @property
    def risk_distance(self): return abs(self.entry-self.stop)
    @property
    def target(self): return self.entry+self.risk_distance*self.risk_reward*(1 if self.side=="BUY" else -1)

def _sma(values:list[float],length:int)->Optional[float]:
    return sum(values[-length:])/length if len(values)>=length else None
def _tr(c:Candle,previous:Optional[float])->float:
    return c.high-c.low if previous is None else max(c.high-c.low,abs(c.high-previous),abs(c.low-previous))
def _atr(values:list[float],length:int)->Optional[float]:
    if len(values)<length:return None
    result=sum(values[:length])/length
    for value in values[length:]:result=(result*(length-1)+value)/length
    return result

class OrderBlockStrategy:
    """Confirmed-candle Python port of the user's improved Pine v6 strategy."""
    def __init__(self,symbol:str,config:Optional[StrategyConfig]=None):
        self.symbol=symbol; self.cfg=config or StrategyConfig()
        self.candles4:list[Candle]=[]; self.candles15:list[Candle]=[]
        self.order_blocks:list[OrderBlock]=[]; self.trend4h=0
        self.last_ph4=self.last_pl4=None
        self.last_ph15=self.last_pl15=self.prev_ph15=self.prev_pl15=None
        self.third_ph15=self.third_pl15=None
        self.last_ph15_bar=self.last_pl15_bar=self.prev_ph15_bar=self.prev_pl15_bar=None
        self.third_ph15_bar=self.third_pl15_bar=None
        self._last_4h_timestamp=self._last_15m_timestamp=None
        self._tr4=[]; self._tr15=[]; self._atr4_value=self._atr15_value=None
        self._body4:Deque[float]=deque(maxlen=self.cfg.body_len)
        self._sma4:Deque[float]=deque(maxlen=self.cfg.sma_length)
        self._volume4:Deque[float]=deque(maxlen=self.cfg.ob_volume_length)
        self.sma200_4h=None
        self._long_pending=self._short_pending=False
        self._long_level=self._short_level=self._long_sl=self._short_sl=None
        self._long_break_bar=self._short_break_bar=None
        self.last_early_warning=None
        self._rsi_previous_close=None; self._rsi_seed_gains=[]; self._rsi_seed_losses=[]
        self._rsi_avg_gain=self._rsi_avg_loss=None; self._rsi_values=[]
        self._previous_rsi_pivot_low=self._previous_rsi_pivot_high=None
        self._previous_divergence_price_low=self._previous_divergence_price_high=None
        self._bullish_divergence_bar=self._bearish_divergence_bar=None

    @staticmethod
    def _pivot_high(bars,candidate,left,right):
        if candidate-left<0 or candidate+right>=len(bars):return False
        return bars[candidate].high==max(b.high for b in bars[candidate-left:candidate+right+1])
    @staticmethod
    def _pivot_low(bars,candidate,left,right):
        if candidate-left<0 or candidate+right>=len(bars):return False
        return bars[candidate].low==min(b.low for b in bars[candidate-left:candidate+right+1])
    @staticmethod
    def _line_value(p1,b1,p2,b2,at):
        return p1+(p2-p1)*(at-b1)/(b2-b1)

    def on_4h_close(self,candle:Candle)->list[OrderBlock]:
        if self._last_4h_timestamp is not None and candle.timestamp<=self._last_4h_timestamp:
            raise ValueError("4H candles must be strictly chronological.")
        bars=self.candles4; previous=bars[-1].close if bars else None
        for ob in self.order_blocks:
            if ob.active and ((ob.direction==1 and candle.close<ob.bottom) or (ob.direction==-1 and candle.close>ob.top)):
                ob.active=False
        self._tr4.append(_tr(candle,previous))
        if len(self._tr4)==self.cfg.ob_atr_len:self._atr4_value=sum(self._tr4)/self.cfg.ob_atr_len
        elif len(self._tr4)>self.cfg.ob_atr_len and self._atr4_value is not None:
            self._atr4_value=(self._atr4_value*(self.cfg.ob_atr_len-1)+self._tr4[-1])/self.cfg.ob_atr_len
        atr4=self._atr4_value; bars.append(candle); idx=len(bars)-1
        candidate=idx-self.cfg.pivot4
        if candidate>=0:
            if self._pivot_high(bars,candidate,self.cfg.pivot4,self.cfg.pivot4):self.last_ph4=bars[candidate].high
            if self._pivot_low(bars,candidate,self.cfg.pivot4,self.cfg.pivot4):self.last_pl4=bars[candidate].low
        body=abs(candle.close-candle.open)
        avg_body=sum(self._body4)/len(self._body4) if len(self._body4)==self.cfg.body_len else None
        strong=avg_body is not None and atr4 is not None and body>=avg_body*self.cfg.min_bos_body_avg and body>=atr4*self.cfg.min_bos_body_atr
        bull_bos=self.last_ph4 is not None and previous is not None and candle.close>self.last_ph4 and previous<=self.last_ph4
        bear_bos=self.last_pl4 is not None and previous is not None and candle.close<self.last_pl4 and previous>=self.last_pl4
        avg_vol=_sma(list(self._volume4)+[candle.volume],self.cfg.ob_volume_length)
        vol_score=15. if avg_vol is not None and candle.volume>=avg_vol*1.5 else 10. if avg_vol is not None and candle.volume>=avg_vol*1.2 else 5. if avg_vol is not None and candle.volume>=avg_vol else 0.
        bull_fvg=idx>=2 and candle.low>bars[idx-2].high
        bear_fvg=idx>=2 and candle.high<bars[idx-2].low
        sma_now=_sma(list(self._sma4)+[candle.close],self.cfg.sma_length)
        made=[]
        if strong and atr4 is not None and bull_bos and candle.close>candle.open and candle.close-self.last_ph4>=atr4*self.cfg.min_break_atr:
            self.trend4h=1
            found=next((j for j in range(idx-1,max(-1,idx-self.cfg.ob_search-1),-1) if bars[j].close<bars[j].open),None)
            if found is not None:
                src=bars[found]; src_atr=_atr(self._tr4[:found+1],self.cfg.ob_atr_len)
                if src_atr is not None and 0<src.high-src.low<=src_atr*self.cfg.max_ob_candle_range_atr:
                    score=60.+vol_score+(10. if bull_fvg else 0.)+(15. if sma_now is not None and candle.close>sma_now else 0.)
                    made.append(OrderBlock(1,src.high,src.low,src.timestamp,score))
        if strong and atr4 is not None and bear_bos and candle.close<candle.open and self.last_pl4-candle.close>=atr4*self.cfg.min_break_atr:
            self.trend4h=-1
            found=next((j for j in range(idx-1,max(-1,idx-self.cfg.ob_search-1),-1) if bars[j].close>bars[j].open),None)
            if found is not None:
                src=bars[found]; src_atr=_atr(self._tr4[:found+1],self.cfg.ob_atr_len)
                if src_atr is not None and 0<src.high-src.low<=src_atr*self.cfg.max_ob_candle_range_atr:
                    score=60.+vol_score+(10. if bear_fvg else 0.)+(15. if sma_now is not None and candle.close<sma_now else 0.)
                    made.append(OrderBlock(-1,src.high,src.low,src.timestamp,score))
        self.order_blocks.extend(made)
        while len(self.order_blocks)>self.cfg.max_stored_ob:self.order_blocks.pop(0)
        self._body4.append(body); self._sma4.append(candle.close); self._volume4.append(candle.volume)
        self.sma200_4h=_sma(list(self._sma4),self.cfg.sma_length)
        self._last_4h_timestamp=candle.timestamp
        return made

    def _update_rsi(self,close):
        if self._rsi_previous_close is None:
            self._rsi_previous_close=close; self._rsi_values.append(None); return None
        change=close-self._rsi_previous_close; self._rsi_previous_close=close
        gain,loss=max(change,0.),max(-change,0.); n=self.cfg.rsi_length
        if self._rsi_avg_gain is None or self._rsi_avg_loss is None:
            self._rsi_seed_gains.append(gain); self._rsi_seed_losses.append(loss)
            if len(self._rsi_seed_gains)<n:self._rsi_values.append(None);return None
            self._rsi_avg_gain=sum(self._rsi_seed_gains)/n;self._rsi_avg_loss=sum(self._rsi_seed_losses)/n
        else:
            self._rsi_avg_gain=(self._rsi_avg_gain*(n-1)+gain)/n
            self._rsi_avg_loss=(self._rsi_avg_loss*(n-1)+loss)/n
        value=100. if self._rsi_avg_loss<=0 and self._rsi_avg_gain>0 else 50. if self._rsi_avg_loss<=0 else 100.-100./(1.+self._rsi_avg_gain/self._rsi_avg_loss)
        self._rsi_values.append(value);return value

    def _update_divergence(self,bars,idx):
        if not self.cfg.enable_rsi_divergence:return
        n=self.cfg.divergence_pivot_length; c=idx-n
        if c<0 or self._rsi_values[c] is None:return
        value=self._rsi_values[c]
        if self._pivot_low(bars,c,n,n):
            price=bars[c].low
            if self._previous_divergence_price_low is not None and self._previous_rsi_pivot_low is not None and price<self._previous_divergence_price_low and value>self._previous_rsi_pivot_low:self._bullish_divergence_bar=idx
            self._previous_divergence_price_low,self._previous_rsi_pivot_low=price,value
        if self._pivot_high(bars,c,n,n):
            price=bars[c].high
            if self._previous_divergence_price_high is not None and self._previous_rsi_pivot_high is not None and price>self._previous_divergence_price_high and value<self._previous_rsi_pivot_high:self._bearish_divergence_bar=idx
            self._previous_divergence_price_high,self._previous_rsi_pivot_high=price,value

    def _rsi_context(self,side,idx):
        if not self.cfg.enable_rsi_divergence:return "DISABLED",self._rsi_values[idx] if idx<len(self._rsi_values) else None
        bull=self._bullish_divergence_bar is not None and idx-self._bullish_divergence_bar<=self.cfg.rsi_divergence_lookback
        bear=self._bearish_divergence_bar is not None and idx-self._bearish_divergence_bar<=self.cfg.rsi_divergence_lookback
        state=("BULLISH" if bull else "OPPOSING_BEARISH" if bear else "NONE") if side=="BUY" else ("BEARISH" if bear else "OPPOSING_BULLISH" if bull else "NONE")
        return state,self._rsi_values[idx] if idx<len(self._rsi_values) else None

    def _signal(self,side,candle,atr,stop,method,idx,ob=None):
        state,rsi=self._rsi_context(side,idx)
        entry=candle.close+(2*self.cfg.min_tick if side=="BUY" else -2*self.cfg.min_tick)
        return Signal(self.symbol,side,candle.timestamp,entry,stop,atr,
            ob.origin_time if ob else None,ob.top if ob else None,ob.bottom if ob else None,
            self.trend4h,self.sma200_4h,method,self.cfg.risk_reward,None,state,rsi,ob.score if ob else None)

    def on_15m_close(self,candle:Candle)->Optional[Signal]:
        if self._last_15m_timestamp is not None and candle.timestamp<=self._last_15m_timestamp:
            raise ValueError("15m candles must be strictly chronological.")
        self.last_early_warning=None; bars=self.candles15; prev_close=bars[-1].close if bars else None
        bars.append(candle);idx=len(bars)-1;self._update_rsi(candle.close)
        self._tr15.append(_tr(candle,prev_close))
        if len(self._tr15)==self.cfg.atr_len:self._atr15_value=sum(self._tr15)/self.cfg.atr_len
        elif len(self._tr15)>self.cfg.atr_len and self._atr15_value is not None:self._atr15_value=(self._atr15_value*(self.cfg.atr_len-1)+self._tr15[-1])/self.cfg.atr_len
        atr=self._atr15_value;self._update_divergence(bars,idx)
        c=idx-self.cfg.pivot15
        if c>=0:
            if self._pivot_high(bars,c,self.cfg.pivot15,self.cfg.pivot15):
                self.third_ph15,self.third_ph15_bar=self.prev_ph15,self.prev_ph15_bar
                self.prev_ph15,self.prev_ph15_bar=self.last_ph15,self.last_ph15_bar
                self.last_ph15,self.last_ph15_bar=bars[c].high,c
            if self._pivot_low(bars,c,self.cfg.pivot15,self.cfg.pivot15):
                self.third_pl15,self.third_pl15_bar=self.prev_pl15,self.prev_pl15_bar
                self.prev_pl15,self.prev_pl15_bar=self.last_pl15,self.last_pl15_bar
                self.last_pl15,self.last_pl15_bar=bars[c].low,c
        self._last_15m_timestamp=candle.timestamp
        if atr is None or atr<=0:return None
        cfg=self.cfg;sma=self.sma200_4h
        # Trendline validity: third confirmed pivot aligns with line through last two.
        r_now=r_prev=s_now=s_prev=None
        r_valid=all(x is not None for x in (self.third_ph15,self.prev_ph15,self.last_ph15,self.third_ph15_bar,self.prev_ph15_bar,self.last_ph15_bar)) and self.last_ph15<self.prev_ph15 and self.last_ph15_bar!=self.prev_ph15_bar
        if r_valid:
            slope=(self.last_ph15-self.prev_ph15)/(self.last_ph15_bar-self.prev_ph15_bar)
            expected=self.prev_ph15+slope*(self.third_ph15_bar-self.prev_ph15_bar)
            r_valid=abs(self.third_ph15-expected)<=atr*cfg.trendline_touch_tolerance_atr
            if r_valid:
                r_now=self._line_value(self.prev_ph15,self.prev_ph15_bar,self.last_ph15,self.last_ph15_bar,idx)
                r_prev=self._line_value(self.prev_ph15,self.prev_ph15_bar,self.last_ph15,self.last_ph15_bar,idx-1)
        s_valid=all(x is not None for x in (self.third_pl15,self.prev_pl15,self.last_pl15,self.third_pl15_bar,self.prev_pl15_bar,self.last_pl15_bar)) and self.last_pl15>self.prev_pl15 and self.last_pl15_bar!=self.prev_pl15_bar
        if s_valid:
            slope=(self.last_pl15-self.prev_pl15)/(self.last_pl15_bar-self.prev_pl15_bar)
            expected=self.prev_pl15+slope*(self.third_pl15_bar-self.prev_pl15_bar)
            s_valid=abs(self.third_pl15-expected)<=atr*cfg.trendline_touch_tolerance_atr
            if s_valid:
                s_now=self._line_value(self.prev_pl15,self.prev_pl15_bar,self.last_pl15,self.last_pl15_bar,idx)
                s_prev=self._line_value(self.prev_pl15,self.prev_pl15_bar,self.last_pl15,self.last_pl15_bar,idx-1)
        break_up=cfg.enable_trendline_signals and r_valid and candle.close>r_now and prev_close is not None and prev_close<=r_prev
        break_down=cfg.enable_trendline_signals and s_valid and candle.close<s_now and prev_close is not None and prev_close>=s_prev
        if break_up:
            self._long_pending,self._long_level,self._long_break_bar=True,r_now,idx
            self._long_sl=self.last_pl15-atr*cfg.sl_buffer_atr if self.last_pl15 is not None else None
            self._short_pending,self._short_level,self._short_break_bar,self._short_sl=False,None,None,None
        if break_down:
            self._short_pending,self._short_level,self._short_break_bar=True,s_now,idx
            self._short_sl=self.last_ph15+atr*cfg.sl_buffer_atr if self.last_ph15 is not None else None
            self._long_pending,self._long_level,self._long_break_bar,self._long_sl=False,None,None,None
        tol=atr*cfg.trendline_retest_atr
        if self._long_pending and self._long_break_bar is not None and (idx-self._long_break_bar>cfg.trendline_pullback_max_wait or candle.close<self._long_level-tol):
            self._long_pending,self._long_level,self._long_break_bar,self._long_sl=False,None,None,None
        if self._short_pending and self._short_break_bar is not None and (idx-self._short_break_bar>cfg.trendline_pullback_max_wait or candle.close>self._short_level+tol):
            self._short_pending,self._short_level,self._short_break_bar,self._short_sl=False,None,None,None
        long_retest=self._long_pending and self._long_break_bar is not None and idx>self._long_break_bar and candle.low<=self._long_level+tol and candle.high>=self._long_level-tol
        short_retest=self._short_pending and self._short_break_bar is not None and idx>self._short_break_bar and candle.high>=self._short_level-tol and candle.low<=self._short_level+tol
        long_confirm=long_retest and candle.close>candle.open and candle.close>self._long_level
        short_confirm=short_retest and candle.close<candle.open and candle.close<self._short_level

        # OB route: score threshold -> initial touch -> CHoCH/BOS -> OB retest -> candle confirmation.
        ob_tol=atr*cfg.retest_atr
        for ob in self.order_blocks:
            if not ob.active or ob.used or ob.score<cfg.min_ob_score:continue
            d,top,bottom=ob.direction,ob.top,ob.bottom
            if cfg.use_trend_filter and not ((d==1 and self.trend4h==1) or (d==-1 and self.trend4h==-1)):continue
            touched=candle.low<=top+ob_tol and candle.high>=bottom-ob_tol
            if touched and not ob.touched:ob.touched,ob.touch_bar=True,idx
            fresh=ob.touched and (not cfg.use_touch_freshness or (ob.touch_bar is not None and idx-ob.touch_bar<=cfg.max_bars_since_touch))
            eligible=not cfg.require_ob_touch or fresh
            bull_cross=self.last_ph15 is not None and prev_close is not None and candle.close>self.last_ph15 and prev_close<=self.last_ph15 and candle.close>candle.open
            bear_cross=self.last_pl15 is not None and prev_close is not None and candle.close<self.last_pl15 and prev_close>=self.last_pl15 and candle.close<candle.open
            bull_strong=not cfg.use_choch_strength or candle.close-candle.open>=atr*cfg.min_choch_body_atr
            bear_strong=not cfg.use_choch_strength or candle.open-candle.close>=atr*cfg.min_choch_body_atr
            if not ob.has_choch and eligible and d==1 and bull_cross and bull_strong:ob.has_choch,ob.choch_level,ob.choch_bar=True,self.last_ph15,idx
            if not ob.has_choch and eligible and d==-1 and bear_cross and bear_strong:ob.has_choch,ob.choch_level,ob.choch_bar=True,self.last_pl15,idx
            if ob.has_choch and ob.choch_bar is not None and idx-ob.choch_bar>cfg.max_wait:
                ob.has_choch,ob.choch_level,ob.choch_bar=False,None,None
                ob.retest_bar=ob.sweep_bar=None;ob.sweep_extreme=ob.sweep_confirm_level=None
            after=ob.has_choch and ob.choch_bar is not None and idx>ob.choch_bar
            if after and touched and ob.retest_bar is None:ob.retest_bar=idx
            if after and touched and ob.sweep_bar is None:
                bull_sweep=d==1 and candle.low<bottom and candle.close>bottom and candle.close<=top
                bear_sweep=d==-1 and candle.high>top and candle.close<top and candle.close>=bottom
                if bull_sweep:ob.sweep_bar,ob.sweep_extreme,ob.sweep_confirm_level,ob.retest_bar=idx,candle.low,candle.high,idx
                elif bear_sweep:ob.sweep_bar,ob.sweep_extreme,ob.sweep_confirm_level,ob.retest_bar=idx,candle.high,candle.low,idx
            has_retest=ob.retest_bar is not None and idx>ob.retest_bar and idx-ob.retest_bar<=cfg.max_wait
            sweep_setup=ob.sweep_bar is not None and ob.sweep_confirm_level is not None and idx>ob.sweep_bar
            bull_normal=has_retest and ob.sweep_bar is None and d==1 and candle.close>candle.open and candle.close>top
            bear_normal=has_retest and ob.sweep_bar is None and d==-1 and candle.close<candle.open and candle.close<bottom
            bull_sweep=sweep_setup and d==1 and candle.close>candle.open and candle.close>ob.sweep_confirm_level
            bear_sweep=sweep_setup and d==-1 and candle.close<candle.open and candle.close<ob.sweep_confirm_level
            bull_power=not cfg.use_confirm_strength or candle.close-candle.open>=atr*cfg.min_confirm_body_atr
            bear_power=not cfg.use_confirm_strength or candle.open-candle.close>=atr*cfg.min_confirm_body_atr
            if d==1 and (bull_normal or bull_sweep) and bull_power and self.last_pl15 is not None:
                ref=min(self.last_pl15,bottom,ob.sweep_extreme) if ob.sweep_extreme is not None else (min(self.last_pl15,bottom) if cfg.use_ob_boundary_sl else self.last_pl15)
                stop=ref-atr*cfg.sl_buffer_atr
                if stop<candle.close and candle.close-stop>=cfg.min_tick and (not cfg.use_sma200_filter or (sma is not None and candle.close>sma)):
                    ob.used=True;return self._signal("BUY",candle,atr,stop,"ORDER_BLOCK_SWEEP" if bull_sweep else "ORDER_BLOCK",idx,ob)
            if d==-1 and (bear_normal or bear_sweep) and bear_power and self.last_ph15 is not None:
                ref=max(self.last_ph15,top,ob.sweep_extreme) if ob.sweep_extreme is not None else (max(self.last_ph15,top) if cfg.use_ob_boundary_sl else self.last_ph15)
                stop=ref+atr*cfg.sl_buffer_atr
                if stop>candle.close and stop-candle.close>=cfg.min_tick and (not cfg.use_sma200_filter or (sma is not None and candle.close<sma)):
                    ob.used=True;return self._signal("SELL",candle,atr,stop,"ORDER_BLOCK_SWEEP" if bear_sweep else "ORDER_BLOCK",idx,ob)

        # Independent trendline route: no OB is needed; only breakout, pullback, confirmation.
        if long_confirm and (not cfg.use_sma200_trendline_filter or (sma is not None and candle.close>sma)) and self._long_sl is not None and self._long_sl<candle.close and candle.close-self._long_sl>=cfg.min_tick:
            sig=self._signal("BUY",candle,atr,self._long_sl,"TRENDLINE",idx)
            self._long_pending,self._long_level,self._long_break_bar,self._long_sl=False,None,None,None
            return sig
        if short_confirm and (not cfg.use_sma200_trendline_filter or (sma is not None and candle.close<sma)) and self._short_sl is not None and self._short_sl>candle.close and self._short_sl-candle.close>=cfg.min_tick:
            sig=self._signal("SELL",candle,atr,self._short_sl,"TRENDLINE",idx)
            self._short_pending,self._short_level,self._short_break_bar,self._short_sl=False,None,None,None
            return sig
        return None

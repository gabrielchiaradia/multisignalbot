# src/strategy.py
"""
Estrategia Multi-Señal para el bot de trading.
Señales evaluadas en velas de 4h:
  1. RSI Extreme (25/75)     — peso 2
  2. Inside Bar Breakout     — peso 1
  3. Volume Breakout (2x)    — peso 1
  4. Donchian 20 Breakout    — peso 1

Retorna señales con score de confluencia.
Score >= MIN_SCORE para operar.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone
from src.config import SYMBOL, RR_RATIO, MIN_SCORE
from src.logger import logger


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega todos los indicadores necesarios al DataFrame de 4h."""
    df = df.copy()

    # EMAs (para contexto, no para señal directa)
    df['ema21'] = df['close'].ewm(span=21).mean()
    df['ema50'] = df['close'].ewm(span=50).mean()

    # RSI 14
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    # ATR 14
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = df['tr'].rolling(14).mean()

    # Volumen relativo
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_sma20'].replace(0, np.nan)

    # Body y rango
    df['body_pct'] = abs(df['close'] - df['open']) / df['close'] * 100
    df['candle_range'] = (df['high'] - df['low']) / df['close'] * 100
    df['is_green'] = (df['close'] > df['open']).astype(int)

    # Donchian 20
    df['dc_high_20'] = df['high'].rolling(20).max()
    df['dc_low_20'] = df['low'].rolling(20).min()

    return df


def evaluar_señales(df: pd.DataFrame) -> tuple:
    """
    Evalúa las últimas 3 velas del DataFrame de 4h.
    Retorna dict con señal si hay confluencia suficiente, o None.

    Retorno:
        {
            'signal': 'LONG' | 'SHORT',
            'sl': float,
            'score': int,
            'sources': str,       # ej: "RSI25+Donch20"
            'atr': float,
            'entry_ref': float,   # precio de referencia para la entrada
        }
    """
    if len(df) < 25:  # mínimo para indicadores
        return None, df

    df = add_indicators(df)

    row = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]

    if pd.isna(row['rsi']) or pd.isna(row['atr']) or row['atr'] <= 0:
        return None, df 

    score = 0
    sources = []
    direction = None
    sl = None

    # ══════════════════════════════════════════════════════
    # SEÑAL 1: RSI Extreme 25/75 (peso 2)
    # RSI sale de zona extrema = reversión de momentum
    # ══════════════════════════════════════════════════════
    if prev['rsi'] < 25 and row['rsi'] >= 25:
        score += 2
        sources.append('RSI25')
        direction = 'LONG'
        sl = row['low'] - row['atr'] * 0.5
    elif prev['rsi'] > 75 and row['rsi'] <= 75:
        score += 2
        sources.append('RSI75')
        direction = 'SHORT'
        sl = row['high'] + row['atr'] * 0.5

    # ══════════════════════════════════════════════════════
    # SEÑAL 2: Inside Bar Breakout (peso 1)
    # Compresión de volatilidad → explosión direccional
    # ══════════════════════════════════════════════════════
    if prev['high'] <= prev2['high'] and prev['low'] >= prev2['low']:
        # prev es inside bar — ¿rompió row?
        if row['close'] > prev2['high'] and row['close'] > row['open']:
            if direction is None or direction == 'LONG':
                score += 1
                sources.append('InsideBar')
                if direction is None:
                    direction = 'LONG'
                    sl = prev['low']
        elif row['close'] < prev2['low'] and row['close'] < row['open']:
            if direction is None or direction == 'SHORT':
                score += 1
                sources.append('InsideBar')
                if direction is None:
                    direction = 'SHORT'
                    sl = prev['high']

    # ══════════════════════════════════════════════════════
    # SEÑAL 3: Volume Breakout 2x (peso 1)
    # Volumen >2x promedio + vela con cuerpo fuerte
    # ══════════════════════════════════════════════════════
    if not pd.isna(row['vol_ratio']) and row['vol_ratio'] >= 2.0:
        if row['body_pct'] > row['candle_range'] * 0.5:
            if row['is_green'] and (direction is None or direction == 'LONG'):
                score += 1
                sources.append('VolBreak')
                if direction is None:
                    direction = 'LONG'
                    sl = row['low'] - row['atr'] * 0.3
            elif not row['is_green'] and (direction is None or direction == 'SHORT'):
                score += 1
                sources.append('VolBreak')
                if direction is None:
                    direction = 'SHORT'
                    sl = row['high'] + row['atr'] * 0.3

    # ══════════════════════════════════════════════════════
    # SEÑAL 4: Donchian 20 Breakout (peso 1)
    # Ruptura de máximo/mínimo de 20 períodos
    # ══════════════════════════════════════════════════════
    if not pd.isna(prev['dc_high_20']):
        if row['close'] > prev['dc_high_20'] and prev['close'] <= prev['dc_high_20']:
            if direction is None or direction == 'LONG':
                score += 1
                sources.append('Donch20')
                if direction is None:
                    direction = 'LONG'
                    sl = row['low'] - row['atr'] * 1.0
        elif row['close'] < prev['dc_low_20'] and prev['close'] >= prev['dc_low_20']:
            if direction is None or direction == 'SHORT':
                score += 1
                sources.append('Donch20')
                if direction is None:
                    direction = 'SHORT'
                    sl = row['high'] + row['atr'] * 1.0

    # ══════════════════════════════════════════════════════
    # EVALUACIÓN FINAL
    # ══════════════════════════════════════════════════════
    if direction is None or score < MIN_SCORE or sl is None:
        if score > 0:
            logger.debug("[%s] Señal descartada: %s score=%d (min=%d)",
                        SYMBOL, '+'.join(sources), score, MIN_SCORE)
        return None, df 

    # Validar distancia al SL mínima (0.1% para cubrir fees)
    entry_ref = row['close']
    dist = abs(entry_ref - sl)
    if dist / entry_ref < 0.001:
        logger.debug("[%s] Señal descartada: SL muy cerca (%.4f%%)", SYMBOL, dist/entry_ref*100)
        return None,df 

    signal = {
        'signal': direction,
        'sl': float(sl),
        'score': score,
        'sources': '+'.join(sources),
        'atr': float(row['atr']),
        'entry_ref': float(entry_ref),
    }

    logger.info("[%s] 🎯 SEÑAL: %s score=%d fuentes=%s ATR=%.2f",
                SYMBOL, direction, score, signal['sources'], signal['atr'])

    return signal, df


def calcular_tp(entry_price: float, sl_price: float, direction: str, rr: float = None) -> float:
    """Calcula el Take Profit basado en RR y distancia al SL."""
    if rr is None:
        rr = RR_RATIO
    dist = abs(entry_price - sl_price)
    if direction == 'LONG':
        return entry_price + dist * rr
    else:
        return entry_price - dist * rr


def cooldown_activo(cooldown_hours: int = 2) -> bool:
    """Verifica si el cooldown post-trade está activo."""
    try:
        from src.journal import _load
        from src.config import BOT_ID
        trades = _load()
        mis_trades = [t for t in trades if t.get('bot_id') == BOT_ID and t.get('status') == 'CLOSED']
        if mis_trades:
            ultimo = mis_trades[-1]
            if ultimo.get('close_time'):
                last_time = datetime.fromisoformat(ultimo['close_time'])
                horas = (datetime.now(timezone.utc) - last_time).total_seconds() / 3600.0
                if horas < cooldown_hours:
                    logger.info("Cooldown activo. Faltan %.1f horas.", cooldown_hours - horas)
                    return True
    except Exception:
        pass
    return False

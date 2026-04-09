# src/diagnostics.py
from src.logger import logger

def generar_reporte_no_signal(df, symbol):
    """
    Genera un diagnóstico visual cuando no hay señal, manteniendo el precio
    como dato principal junto al RSI y métricas de volatilidad.
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]
    
    # Lógica de señales (Criterios de confluencia)
    ib = prev['high'] <= prev2['high'] and prev['low'] >= prev2['low']
    vb = last['vol_ratio'] >= 2.0 and last['body_pct'] > last['candle_range'] * 0.5
    dc_up = last['close'] > prev['dc_high_20'] and prev['close'] <= prev['dc_high_20']
    dc_dn = last['close'] < prev['dc_low_20'] and prev['close'] >= prev['dc_low_20']
    
    # Formateo del reporte solicitado
    logger.info("-" * 55)
    logger.info(f"🔍 DIAGNÓSTICO {symbol} | Vela {df.index[-1]} → {df.index[-1] + pd.Timedelta(hours=4)}")
    logger.info(f"Precio: {last['close']:.2f}") # El precio siempre adelante
    logger.info(f"RSI: {last['rsi']:.1f} (prev: {prev['rsi']:.1f})")
    logger.info(f"ATR: {last['atr']:.2f}")
    logger.info(f"Vol ratio: {last['vol_ratio']:.2f}")
    logger.info(f"Rango DC: [{prev['dc_low_20']:.2f} - {prev['dc_high_20']:.2f}]")
    logger.info(f"Body%: {last['body_pct']:.2f} | Range%: {last['candle_range']:.2f}")
    logger.info(f"Green: {int(last['is_green'])}")
    
    logger.info("")
    logger.info("--- Chequeo señales individuales ---")
    logger.info(f"Inside Bar:    {'✅' if ib else '❌'} (prev H={prev['high']:.2f} L={prev['low']:.2f} vs prev2 H={prev2['high']:.2f} L={prev2['low']:.2f})")
    logger.info(f"Vol Breakout:  {'✅' if vb else '❌'} (ratio={last['vol_ratio']:.2f}, body={last['body_pct']:.2f}% vs 50% range={last['candle_range']*0.5:.2f}%)")
    logger.info(f"Donchian 20 UP: {'✅' if dc_up else '❌'} (close={last['close']:.2f} vs prev DC high={prev['dc_high_20']:.2f})")
    logger.info(f"Donchian 20 DN: {'✅' if dc_dn else '❌'} (close={last['close']:.2f} vs prev DC low={prev['dc_low_20']:.2f})")
    logger.info("-" * 55)
# main.py
# -*- coding: utf-8 -*-
"""
Multi-Signal Trading Bot
Análisis en 4h, ejecución en cierre de vela.
Señales: RSI Extreme, Inside Bar, Volume Breakout, Donchian 20.
"""
import time
import threading
import pandas as pd
from datetime import datetime, timezone
from src.config import (
    SYMBOL, BOT_ID, BOT_NAME, RR_RATIO, RISK_BASE, RISK_CONFLUENCIA,
    MIN_SCORE, LEVERAGE, MAX_HOLD_HOURS, COOLDOWN_HOURS
)
from src.logger import logger
from src.exchange import (
    get_client, get_account_status, get_open_position,
    set_leverage, cancel_all_open_orders, close_market_position,
    get_klines_rest
)
from src.strategy import evaluar_señales, calcular_tp, cooldown_activo
from src.execution import ejecutar_apertura_completa, gestionar_resguardo_posicion, sincronizar_realidad_vs_journal
from src.risk import calculate_position_size, check_drawdown_alert, can_trade
from src.live_writer import exportar_dashboard, exportar_status
from src.notifier import crear_notifier
from src.journal import _load
from src.news_filter import is_news_blocked
from src.diagnostics import generar_reporte_no_signal

client = None
cycle_count = 0
_last_4h_candle_time = None  # para detectar cierre de vela 4h nueva


def inicializar():
    """Configuración única al arrancar."""
    logger.info("=" * 55)
    logger.info(f"  Multi-Signal Bot {BOT_ID} — {SYMBOL}")
    logger.info(f"  RR: {RR_RATIO} | Risk: {RISK_BASE}%/{RISK_CONFLUENCIA}%")
    logger.info(f"  Min Score: {MIN_SCORE} | Max Hold: {MAX_HOLD_HOURS}h")
    logger.info(f"  Leverage: {LEVERAGE}x | Cooldown: {COOLDOWN_HOURS}h")
    logger.info("=" * 55)
    c = get_client()
    set_leverage(c, SYMBOL)
    cancel_all_open_orders(c, SYMBOL)
    balance = get_account_status(c)['wallet_balance']
    notifier = crear_notifier()
    notifier.alert_startup(SYMBOL, RISK_BASE, RR_RATIO, MIN_SCORE, balance)
    return c


def obtener_velas_4h(client) -> 'pd.DataFrame':
    """Descarga las últimas velas de 4h vía REST."""
    return get_klines_rest(client, SYMBOL, '4h', limite=100)


def _gestionar_timeout(client):
    """
    Chequea si hay un trade abierto que superó MAX_HOLD_HOURS.
    Si sí, cierra a mercado.
    """
    all_trades = _load()
    for t in all_trades:
        if t.get('symbol') != SYMBOL or t.get('status') != 'OPEN':
            continue
        if t.get('bot_id', BOT_ID) != BOT_ID:
            continue

        entry_time_str = t.get('entry_time', '')
        if not entry_time_str:
            continue

        entry_time = datetime.fromisoformat(entry_time_str)
        horas_abierto = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600.0

        if horas_abierto >= MAX_HOLD_HOURS:
            logger.warning(
                "[%s] ⏰ TIMEOUT: trade abierto hace %.1fh (límite: %dh) — cerrando.",
                SYMBOL, horas_abierto, MAX_HOLD_HOURS
            )
            try:
                cancel_all_open_orders(client, SYMBOL)
                close_market_position(client, SYMBOL)
                crear_notifier().alert_error(
                    "TIMEOUT",
                    f"Trade cerrado tras {horas_abierto:.1f}h (límite: {MAX_HOLD_HOURS}h)"
                )
            except Exception as e:
                logger.error("[%s] Error cerrando por timeout: %s", SYMBOL, e)
            return True
    return False


def _hay_nueva_vela_4h(df) -> bool:
    """Detecta si la última vela cerrada del DataFrame es nueva."""
    global _last_4h_candle_time
    if df is None or df.empty:
        return False

    ultima = df.index[-1]
    if _last_4h_candle_time is None or ultima > _last_4h_candle_time:
        _last_4h_candle_time = ultima
        return True
    return False


def ciclo(client):
    """
    Ciclo principal. Se ejecuta cada ~1 minuto.
    1. Sincronizar journal con Binance
    2. Gestionar posición abierta (timeout, SL/TP rescue)
    3. Si no hay posición, evaluar señales en 4h
    4. Dashboard
    """
    global cycle_count

    try:
        if cycle_count % 60 == 0:
            logger.info("=" * 55)
            logger.info(f"  {BOT_NAME} | {SYMBOL} | Ciclo {cycle_count}")
            logger.info("=" * 55)

        # ── Cuenta ──────────────────────────────────────────
        account = get_account_status(client)
        check_drawdown_alert(account['wallet_balance'], cycle_count)

        # ── Sincronizar journal ─────────────────────────────
        sincronizar_realidad_vs_journal(client, SYMBOL)

        # ── Posición abierta: timeout + resguardo ───────────
        pos = get_open_position(client, SYMBOL)
        if pos:
            cerro = _gestionar_timeout(client)
            if cerro:
                sincronizar_realidad_vs_journal(client, SYMBOL)
                pos = None
            else:
                gestionar_resguardo_posicion(client, SYMBOL)

        # ── Evaluar señales solo en cierre de vela 4h ───────
        if not pos:
            df_4h = obtener_velas_4h(client)
            nueva_vela = _hay_nueva_vela_4h(df_4h)
            
            # Elimino la ultima vela para que no use la actual como vela anterior
            df_4h = df_4h.iloc[:-1]
            
            if nueva_vela and df_4h is not None and not df_4h.empty:
                logger.info("[%s] Nueva vela 4h cerrada. Evaluando señales...", SYMBOL)

                # Filtros previos
                news_blocked, reason = is_news_blocked(SYMBOL)
                if news_blocked:
                    logger.info("[%s] Bloqueado por noticias: %s", SYMBOL, reason)
                elif cooldown_activo(COOLDOWN_HOURS):
                    logger.info("[%s] Cooldown activo.", SYMBOL)
                elif not can_trade(_load()):
                    logger.info("[%s] Cortacircuitos diario activo.", SYMBOL)
                else:
                    # Evaluar señales
                    signal, df_4h= evaluar_señales(df_4h)

                    if signal:
                        _ejecutar_señal(client, signal, account)
                    else:
                        logger.info("[%s] Sin señal en esta vela 4h.", SYMBOL)
                        generar_reporte_no_signal(df_4h, SYMBOL)

        # ── Dashboard ───────────────────────────────────────
        exportar_status(
            account['wallet_balance'], cycle_count,
            account['unrealized_pnl'], account['margin_balance'],
            account['available'], 1 if pos else 0
        )
        exportar_dashboard(client)

        # Heartbeat
        crear_notifier().heartbeat_si_corresponde(client, cycle_count)
        cycle_count += 1

    except Exception as e:
        logger.error(f"Error en ciclo: {e}", exc_info=True)


def _ejecutar_señal(client, signal, account):
    """Ejecuta una señal: calcula sizing, TP, y manda la orden."""
    direction = signal['signal']
    sl = signal['sl']
    score = signal['score']
    sources = signal['sources']

    # Risk escalonado por confluencia
    risk_pct = RISK_CONFLUENCIA if score >= 2 else RISK_BASE

    # Entry = precio actual (market via limit GTX)
    entry_price = account.get('_last_price', None)
    if not entry_price:
        # Obtener precio actual
        try:
            ticker = client.futures_mark_price(symbol=SYMBOL)
            entry_price = float(ticker['markPrice'])
        except Exception as e:
            logger.error("[%s] No se pudo obtener mark price: %s", SYMBOL, e)
            return

    # Calcular TP
    tp = calcular_tp(entry_price, sl, direction)

    # Validar distancia
    dist = abs(entry_price - sl)
    if dist / entry_price < 0.001:
        logger.warning("[%s] Distancia SL muy chica (%.4f%%), abortando.", SYMBOL, dist/entry_price*100)
        return

    # Position sizing
    qty = calculate_position_size(account['wallet_balance'], risk_pct, entry_price, sl)

    # Cap por margen
    notional = qty * entry_price
    max_notional = account['available'] * LEVERAGE * 0.8
    if notional > max_notional:
        qty_capped = round(max_notional / entry_price, 3)
        logger.warning("[%s] Qty capado: %.4f -> %.4f", SYMBOL, qty, qty_capped)
        qty = qty_capped

    if qty <= 0:
        logger.warning("[%s] Qty inválido, abortando.", SYMBOL)
        return

    logger.info(
        "[%s] 📊 EJECUTANDO: %s | Score=%d (%s) | Entry=%.2f | SL=%.2f | TP=%.2f | Risk=%.1f%% | Qty=%.4f",
        SYMBOL, direction, score, sources, entry_price, sl, tp, risk_pct, qty
    )

    # Bias = las fuentes de señal
    bias = sources

    try:
        ejecutar_apertura_completa(
            client, SYMBOL, direction, entry_price, sl, tp,
            qty, risk_pct, balance_at_open=account['wallet_balance'],
            bias=bias
        )
    except Exception as e:
        logger.error("[%s] Error ejecutando apertura: %s", SYMBOL, e, exc_info=True)


def main():
    global client
    client = inicializar()

    logger.info("Entrando en loop principal (polling cada 60s)...")

    while True:
        try:
            ciclo(client)
            logger.debug("Ciclo completado. Esperando 60s...")

        except Exception as e:
            logger.error(f"Error en loop principal: {e}", exc_info=True)

        try:
            time.sleep(60)
        except Exception as e:
            logger.error(f"Error en sleep: {e}")
            break


if __name__ == "__main__":
    main()

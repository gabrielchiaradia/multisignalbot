# src/execution.py

import time
import uuid
from datetime import datetime, timezone
from src.logger import logger
from src.config import BOT_ID
from src.journal import record_open, _load, _save
from src.notifier import crear_notifier
from src.exchange import (
    cancel_all_open_orders,
    place_market_entry_order,
    place_sl_tp,
    verificar_y_rescatar_sl_tp,
    get_open_position
)


def gestionar_resguardo_posicion(client, symbol):
    """
    Busca el trade activo en el journal y llama a la función de exchange
    para asegurar que los Stop Loss y Take Profit sigan vivos en Binance.
    """
    try:
        all_trades = _load()
        current_trade = None

        for t in all_trades:
            if t.get('symbol') == symbol and t.get('status') == 'OPEN':
                current_trade = t
                break

        if current_trade:
            owner = current_trade.get('bot_id', BOT_ID)

            if owner == "MANUAL":
                return
            elif owner == BOT_ID:
                verificar_y_rescatar_sl_tp(client, symbol, current_trade)
            else:
                pass
        else:
            logger.warning(f"[{symbol}] Hay posición en Binance pero no encontré el trade OPEN en el Journal.")

    except Exception as e:
        logger.error(f"Error en gestionar_resguardo_posicion para {symbol}: {e}")


def ejecutar_apertura_completa(client, symbol, signal, entry_price, sl_price, tp_price, qty, risk_pct, balance_at_open: float = 0.0, bias: str = "", ema50: float = None, trend_bias: str = None):
    """
    Orquesta la apertura: Cancela previas, MARKET entry, confirma posición y clava SL/TP.
    bias: fuentes de señal (ej: "RSI25+Donch20") — se guarda en el journal.
    ema50, trend_bias: bias macro al momento de la señal — se guarda en el journal.
    """
    try:
        # 1. Limpieza previa
        cancel_all_open_orders(client, symbol)

        # 2. Enviar orden MARKET (entrada inmediata)
        side = "BUY" if signal == "LONG" else "SELL"
        order = place_market_entry_order(client, symbol, side, qty)

        if not order or order.get('status') not in ['NEW', 'FILLED', 'PARTIALLY_FILLED']:
            logger.warning(f"[{symbol}] Orden MARKET rechazada o fallida.")
            return False

        # ID único para el journal
        trade_id = str(uuid.uuid4())[:8]

        # 3. Confirmar posición (MARKET ya debería estar lleno, doble check rápido)
        filled = False
        for _ in range(5):
            pos_info = client.futures_position_information(symbol=symbol)
            if any(float(p['positionAmt']) != 0 for p in pos_info if p['symbol'] == symbol):
                filled = True
                break
            time.sleep(0.5)

        if not filled:
            logger.error(f"[{symbol}] ⚠️ Orden MARKET enviada pero posición no detectada tras 2.5s.")
            return False

        # 4. Recuperar precio real de fill si está disponible
        actual_entry = float(order.get('avgPrice') or 0) or entry_price

        # 5. Colocar SL/TP
        try:
            place_sl_tp(client, symbol, side, qty, sl_price, tp_price)
            logger.info(f"[{symbol}] ✅ Posición abierta @ {actual_entry}. SL/TP colocados.")
        except Exception as e:
            logger.error(f"Error colocando SL/TP post-fill: {e}")

        record_open(trade_id, symbol, signal, actual_entry, sl_price, tp_price, qty, risk_pct, balance_at_open, bias=bias, ema50=ema50, trend_bias=trend_bias)
        crear_notifier().alert_trade_open(symbol, signal, actual_entry, sl_price, tp_price, qty, risk_pct, strategy=bias)

        return True

    except Exception as e:
        logger.error(f"Error crítico en ejecutar_apertura_completa: {e}")
        return False


def sincronizar_realidad_vs_journal(client, symbol):
    """
    Audita Binance vs Journal para:
    1. Registrar trades abiertos a mano.
    2. Cerrar trades en el journal con el PnL REAL si se cerraron (SL/TP o a mano).
    3. Promover PENDING_FILL a OPEN si la posición ya existe en Binance.
    4. Cancelar PENDING_FILL silenciosamente si la orden nunca se ejecutó.
    """
    try:
        all_trades = _load()
        pos_real = get_open_position(client, symbol)

        open_in_journal = [
            t for t in all_trades
            if t.get('symbol') == symbol and t.get('status') in ('OPEN', 'PENDING_FILL')
        ]

        modified = False
        ahora = datetime.now(timezone.utc).isoformat()

        # --- HELPER: MFE/MAE/duration/time-to-1R desde klines de 5m ---
        def calcular_excursiones(trade):
            """
            Consulta velas de 5m entre entry y close, calcula:
              - mfe_pct: máxima excursión favorable (% sobre entry)
              - mae_pct: máxima excursión adversa (% sobre entry, positivo)
              - duration_hours: tiempo total que estuvo abierto
              - time_to_1r_hours: tiempo en horas hasta que tocó +1R (None si nunca)
              - hit_tp / hit_sl: flags que indican si la high/low del recorrido
                rozó cualquiera de los niveles (útil cuando el cierre fue por timeout
                pero queremos saber si "casi" llegó al TP).
            """
            try:
                entry_dt = datetime.fromisoformat(trade['entry_time'])
                close_dt = datetime.fromisoformat(trade.get('close_time') or ahora)
                entry_ms = int(entry_dt.timestamp() * 1000)
                close_ms = int(close_dt.timestamp() * 1000)

                entry_price = float(trade.get('entry_price') or 0)
                sl_price    = float(trade.get('sl_price') or 0)
                tp_price    = float(trade.get('tp_price') or 0)
                direction   = trade.get('direction', 'LONG')
                if entry_price <= 0:
                    return

                klines = client.futures_klines(
                    symbol=trade['symbol'],
                    interval='5m',
                    startTime=entry_ms,
                    endTime=close_ms,
                    limit=1000
                )
                if not klines:
                    return

                risk_dist = abs(entry_price - sl_price) if sl_price else 0.0

                max_high = max(float(k[2]) for k in klines)
                min_low  = min(float(k[3]) for k in klines)

                if direction == 'LONG':
                    mfe_price = max_high
                    mae_price = min_low
                    mfe_pct   = (mfe_price - entry_price) / entry_price * 100
                    mae_pct   = (entry_price - mae_price) / entry_price * 100
                    hit_tp    = bool(tp_price) and max_high >= tp_price
                    hit_sl    = bool(sl_price) and min_low <= sl_price
                else:
                    mfe_price = min_low
                    mae_price = max_high
                    mfe_pct   = (entry_price - mfe_price) / entry_price * 100
                    mae_pct   = (mae_price - entry_price) / entry_price * 100
                    hit_tp    = bool(tp_price) and min_low <= tp_price
                    hit_sl    = bool(sl_price) and max_high >= sl_price

                # Time-to-1R: primera vela donde el high/low del recorrido alcanzó +1R
                time_to_1r_h = None
                if risk_dist > 0:
                    for k in klines:
                        k_open_ms = int(k[0])
                        k_high    = float(k[2])
                        k_low     = float(k[3])
                        reached = (k_high >= entry_price + risk_dist) if direction == 'LONG' \
                                  else (k_low <= entry_price - risk_dist)
                        if reached:
                            time_to_1r_h = (k_open_ms - entry_ms) / 1000.0 / 3600.0
                            break

                duration_h = (close_ms - entry_ms) / 1000.0 / 3600.0

                trade['mfe_pct']          = round(mfe_pct, 4)
                trade['mae_pct']          = round(mae_pct, 4)
                trade['mfe_price']        = round(mfe_price, 4)
                trade['mae_price']        = round(mae_price, 4)
                trade['duration_hours']   = round(duration_h, 2)
                trade['time_to_1r_hours'] = round(time_to_1r_h, 2) if time_to_1r_h is not None else None
                trade['hit_tp_intratrade'] = hit_tp
                trade['hit_sl_intratrade'] = hit_sl

                logger.info(
                    f"[{symbol}] Excursiones: MFE={mfe_pct:.2f}% MAE={mae_pct:.2f}% "
                    f"dur={duration_h:.1f}h time-to-1R={time_to_1r_h} hitTP={hit_tp} hitSL={hit_sl}"
                )
            except Exception as e:
                logger.error(f"Error calculando excursiones MFE/MAE: {e}")

        # --- FUNCIÓN INTERNA: CALCULAR PNL/FEES DESDE BINANCE ---
        def calcular_pnl_y_fees_final(trade):
            try:
                entry_dt = datetime.fromisoformat(trade['entry_time'])
                entry_ts = int(entry_dt.timestamp() * 1000)

                historial = client.futures_account_trades(
                    symbol=symbol,
                    startTime=entry_ts,
                    limit=100
                )

                if not historial:
                    logger.warning(f"[{symbol}] Sin historial de trades en Binance")
                    return

                hay_cierre = any(float(op.get('realizedPnl', 0)) != 0 for op in historial)
                if not hay_cierre:
                    logger.info(f"[{symbol}] Historial sin PnL realizado — orden aun no ejecutada. Ignorando cierre falso.")
                    return

                pnl_acumulado = 0.0
                fees_acumulados = 0.0
                ultimo_precio = trade.get('entry_price', 0)

                for op in historial:
                    realizado = float(op.get('realizedPnl', 0))
                    comm = float(op.get('commission', 0))
                    fees_acumulados += comm
                    if realizado != 0:
                        pnl_acumulado += realizado
                        ultimo_precio = float(op.get('price', 0))

                trade['pnl_bruto'] = round(pnl_acumulado, 4)
                trade['fees'] = round(fees_acumulados, 4)
                trade['pnl_usdt'] = round(pnl_acumulado - fees_acumulados, 4)
                trade['exit_price'] = ultimo_precio

                # Clasificar resultado y guardarlo en el journal (antes solo iba a Telegram)
                pnl_neto = trade['pnl_usdt']
                if pnl_neto > 0:
                    resultado = "WIN"
                elif pnl_neto < 0:
                    resultado = "LOSS"
                else:
                    resultado = "BREAKEVEN"
                trade['result'] = resultado

                logger.info(
                    f"[{symbol}] PnL final: "
                    f"Bruto={trade['pnl_bruto']} "
                    f"Fees={trade['fees']} "
                    f"Neto={trade['pnl_usdt']} "
                    f"Exit={ultimo_precio} "
                    f"Result={resultado}"
                )

                # Calcular MFE/MAE/duration/time-to-1R consultando klines de 5m
                calcular_excursiones(trade)

                # Notificación Telegram de cierre
                notifier = crear_notifier()
                notifier.alert_trade_close(
                    symbol=symbol,
                    pnl=pnl_neto,
                    result=resultado,
                    qty=float(trade.get('quantity', 0)),
                    entry_price=float(trade.get('entry_price', 0)),
                    exit_price=ultimo_precio,
                    balance_at_open=float(trade.get('balance_at_open', 0.0))
                )
            except Exception as e:
                logger.error(f"Error calculando PnL/Fees: {e}")

        # ==========================================
        # CASO 0: PENDING_FILL
        # ==========================================
        pending_in_journal = [t for t in open_in_journal if t.get('status') == 'PENDING_FILL']
        only_open_in_journal = [t for t in open_in_journal if t.get('status') == 'OPEN']

        if pending_in_journal:
            promovido = False
            for t in pending_in_journal:
                if pos_real and not promovido:
                    logger.info(f"[{symbol}] ✅ Orden PENDING_FILL ahora ejecutada. Promoviendo a OPEN.")
                    t['status'] = 'OPEN'
                    promovido = True
                    try:
                        side = "BUY" if t['direction'] == "LONG" else "SELL"
                        place_sl_tp(client, symbol, side, float(t['quantity']), float(t['sl_price']), float(t['tp_price']))
                    except Exception as e:
                        logger.error(f"[{symbol}] Error colocando SL/TP en promoción: {e}")
                    crear_notifier().alert_trade_open(
                        symbol, t['direction'], float(t['entry_price']),
                        float(t['sl_price']), float(t['tp_price']),
                        float(t['quantity']), float(t['risk_pct']),
                        strategy=t.get('bias', '')
                    )
                    modified = True
                else:
                    try:
                        ordenes_abiertas = client.futures_get_open_orders(symbol=symbol)
                        sigue_activa = any(
                            abs(float(o.get('price', 0)) - float(t.get('entry_price', 0))) < 0.02
                            for o in ordenes_abiertas
                            if o.get('side') == ('BUY' if t['direction'] == 'LONG' else 'SELL')
                        )
                    except Exception:
                        sigue_activa = True

                    if sigue_activa:
                        logger.info(f"[{symbol}] PENDING_FILL sigue activo en Binance — esperando fill.")
                    else:
                        pos_recheck = get_open_position(client, symbol)
                        if pos_recheck:
                            logger.info(f"[{symbol}] ✅ PENDING_FILL ejecutado en recheck. Promoviendo a OPEN.")
                            t['status'] = 'OPEN'
                            try:
                                side = "BUY" if t['direction'] == "LONG" else "SELL"
                                place_sl_tp(client, symbol, side, float(t['quantity']), float(t['sl_price']), float(t['tp_price']))
                            except Exception as e:
                                logger.error(f"[{symbol}] Error colocando SL/TP en recheck: {e}")
                            crear_notifier().alert_trade_open(
                                symbol, t['direction'], float(t['entry_price']),
                                float(t['sl_price']), float(t['tp_price']),
                                float(t['quantity']), float(t['risk_pct']),
                                strategy=t.get('bias', '')
                            )
                        else:
                            logger.warning(f"[{symbol}] PENDING_FILL cancelado/expirado en Binance: {t['trade_id']}")
                            t['status'] = 'CANCELLED'
                            t['close_time'] = ahora
                            t['result'] = 'CANCELLED'
                        modified = True

            if modified:
                _save(all_trades)
            return

        # ==========================================
        # CASO 1: SE CERRÓ (SL, TP o manual)
        # ==========================================
        if not pos_real and only_open_in_journal:
            for t in only_open_in_journal:
                logger.info(f"[{symbol}] Detectado cierre externo.")
                t['status'] = 'CLOSED'
                t['close_time'] = ahora
                calcular_pnl_y_fees_final(t)
                logger.info("Limpiando órdenes huérfanas previas...")
                cancel_all_open_orders(client, symbol)
                modified = True

        # ==========================================
        # CASO 2: SE ABRIÓ A MANO
        # ==========================================
        elif pos_real and not only_open_in_journal:
            try:
                import json, os
                otros_journals = [
                    f for f in os.listdir('logs')
                    if f.startswith('journal_') and f != f'journal_{BOT_ID}.json'
                ]
                posicion_de_otro_bot = False
                for jfile in otros_journals:
                    try:
                        with open(f'logs/{jfile}', 'r') as f:
                            otros_trades = json.load(f)
                        if any(
                            t.get('symbol') == symbol and
                            t.get('status') in ('OPEN', 'PENDING_FILL')
                            for t in otros_trades
                        ):
                            posicion_de_otro_bot = True
                            logger.info(f"[{symbol}] Posición abierta pertenece a otro bot ({jfile}). Ignorando.")
                            break
                    except Exception:
                        pass
            except Exception:
                posicion_de_otro_bot = False

            if not posicion_de_otro_bot:
                logger.warning(f"[{symbol}] ⚠️ Detectada posición abierta a mano. Registrando en Journal...")
                nuevo_trade = {
                    "trade_id": f"MANUAL-{str(uuid.uuid4())[:4]}",
                    "bot_id": "MANUAL",
                    "symbol": symbol,
                    "direction": pos_real['side'],
                    "entry_price": pos_real['entry'],
                    "sl_price": 0.0,
                    "tp_price": 0.0,
                    "quantity": pos_real['size'],
                    "risk_pct": 0.0,
                    "status": "OPEN",
                    "entry_time": ahora,
                    "close_time": None,
                    "pnl_usdt": 0.0
                }
                all_trades.append(nuevo_trade)
                modified = True

        # ==========================================
        # CASO 3: FLIP A MANO
        # ==========================================
        elif pos_real and only_open_in_journal:
            t = only_open_in_journal[0]
            if t['direction'] != pos_real['side']:
                logger.warning(f"[{symbol}] Cambio de dirección manual detectado.")
                t['status'] = 'CLOSED'
                t['close_time'] = ahora
                calcular_pnl_y_fees_final(t)
                cancel_all_open_orders(client, symbol)
                logger.info(f"[{symbol}] 🧹 Limpieza de órdenes por cambio de dirección manual.")
                modified = True

        if modified:
            _save(all_trades)

    except Exception as e:
        logger.error(f"Error en sincronizador: {e}")

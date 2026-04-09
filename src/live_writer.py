# src/live_writer.py
import json
import os
import threading
from datetime import datetime, timezone
from src.config import BOT_ID, BOT_NAME, SYMBOL, RR_RATIO, RISK_BASE, JOURNAL_FILE
from src.logger import logger
from src.journal import _load

_lock = threading.Lock()
LOG_DIR = os.path.abspath(os.path.dirname(JOURNAL_FILE) or "logs")
os.makedirs(LOG_DIR, exist_ok=True)

def _dashboard_path():
    return os.path.join(LOG_DIR, f"dashboard_trades_{BOT_ID}.json")

def _positions_path():
    return os.path.join(LOG_DIR, f"open_positions_{BOT_ID}.json")

def _all_positions_path():
    return os.path.join(LOG_DIR, f"open_positions_total.json")

def _status_path():
    return os.path.join(LOG_DIR, f"bot_status_{BOT_ID}.json")

def _safe_write(path: str, data):
    try:
        with _lock:
            temp_path = f"{path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, path)
    except Exception as e:
        logger.error(f"Error escribiendo {path}: {e}")

def exportar_dashboard(client):
    from src.exchange import get_account_status

    all_trades = _load()
    closed_trades = [t for t in all_trades if t.get('status') == 'CLOSED' and t.get('bot_id') in [BOT_ID, "MANUAL"]]
    open_trades_journal = [t for t in all_trades if t.get('status') == 'OPEN' and t.get('bot_id') in [BOT_ID, "MANUAL"]]

    pnl_neto_total = sum(t.get('pnl_usdt', 0) for t in closed_trades)
    fees_totales = sum(t.get('fees', 0) for t in closed_trades)
    pnl_bruto_total = sum(t.get('pnl_bruto', 0) for t in closed_trades)

    wins, losses, gross_profit, gross_loss = 0, 0, 0.0, 0.0
    current_balance = get_account_status(client).get('wallet_balance', 1000)
    capital_sim = current_balance - sum(t.get('pnl_usdt', 0) for t in closed_trades)

    formatted_closed = []
    for t in closed_trades:
        pnl = t.get('pnl_usdt', 0.0)
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)
        capital_sim += pnl

        try:
            t_in = datetime.fromisoformat(t['entry_time'])
            t_out = datetime.fromisoformat(t['close_time'])
            duration = round((t_out - t_in).total_seconds() / 3600, 1)
        except:
            duration = 0.0

        formatted_closed.append({
            "time": t.get("entry_time"),
            "close_time": t.get("close_time"),
            "symbol": t.get("symbol"),
            "direction": t.get("direction"),
            "entry": t.get("entry_price"),
            "sl": t.get("sl_price"),
            "tp": t.get("tp_price"),
            "exit": t.get("exit_price", t.get("entry_price")),
            "result": "WIN" if pnl > 0 else "LOSS",
            "pnl_bruto": round(pnl, 2),
            "fees": 0.0,
            "pnl": round(pnl, 2),
            "capital": round(capital_sim, 2),
            "score": t.get("risk_pct", 1.0),
            "duration_h": duration,
            "bias": t.get("bias", ""),
        })

    total_trades = wins + losses
    winrate = round(wins / total_trades * 100, 2) if total_trades > 0 else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0

    dashboard_data = {
        "summary": {
            "label": f"LIVE_{BOT_ID}",
            "symbol": SYMBOL,
            "strategy": "MultiSignal",
            "bot": BOT_NAME,
            "rr": RR_RATIO,
            "risk_pct": RISK_BASE,
            "total": total_trades,
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
            "profit_factor": profit_factor,
            "pnl_total": round(pnl_neto_total, 2),
            "balance_actual": round(current_balance, 2),
            "max_drawdown": 0,
        },
        "trades": formatted_closed
    }
    _safe_write(_dashboard_path(), dashboard_data)

    # Posiciones abiertas
    formatted_open = []
    if open_trades_journal:
        try:
            actual_positions = client.futures_position_information(symbol=SYMBOL)
            real_pos = next((p for p in actual_positions if float(p['positionAmt']) != 0), None)
            for t in open_trades_journal:
                t_dash = t.copy()
                t_dash["pnl"] = round(float(real_pos["unRealizedProfit"]), 2) if real_pos else 0.0
                t_dash["entry"] = t.get("entry_price")
                t_dash["sl"] = t.get("sl_price")
                t_dash["tp"] = t.get("tp_price")
                t_dash["time"] = t.get("entry_time")
                t_dash["bot"] = BOT_ID
                qty = float(t.get("quantity", 0))
                price = float(t.get("entry_price", 0))
                t_dash["capital"] = round(qty * price, 2)
                formatted_open.append(t_dash)
        except Exception as e:
            logger.error(f"[DASHBOARD] Error posiciones abiertas: {e}")

    _safe_write(_positions_path(), formatted_open)

    ruta_total = _all_positions_path()
    datos_finales = []
    if os.path.exists(ruta_total):
        try:
            with open(ruta_total, 'r') as f:
                existentes = json.load(f)
                datos_finales = [x for x in existentes if x.get("bot") != BOT_ID]
        except: pass
    datos_finales.extend(formatted_open)
    _safe_write(ruta_total, datos_finales)

def exportar_status(balance, cycle_count, pnl, margin, available, open_trades_count):
    data = {
        "bot_name": BOT_NAME,
        "symbols": [SYMBOL],
        "strategy": "MultiSignal 4h",
        "rr": RR_RATIO,
        "risk_per_trade": RISK_BASE,
        "max_open_trades": 1,
        "balance": round(balance, 2),
        "cycle_count": cycle_count,
        "pnl": round(pnl, 2),
        "margin": round(margin, 2),
        "available": round(available, 2),
        "open_trades": open_trades_count,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    _safe_write(_status_path(), data)

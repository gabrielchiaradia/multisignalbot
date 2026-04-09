import os
from dotenv import load_dotenv

load_dotenv()

# Identificación
BOT_ID = os.getenv("BOT_ID", "DEV")
BOT_NAME = os.getenv("BOT_NAME", f"MS_{BOT_ID}")

# Binance
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
IS_TESTNET = os.getenv("IS_TESTNET", "False").lower() == "true"

# Símbolo y Parámetros
SYMBOL = os.getenv("SYMBOL", "ETHUSDT")
LEVERAGE = int(os.getenv("LEVERAGE", "10"))

# Estrategia Multi-Señal
RR_RATIO = float(os.getenv("RR_RATIO", "2.0"))
RISK_BASE = float(os.getenv("RISK_BASE", "1.0"))             # 1% score=1
RISK_CONFLUENCIA = float(os.getenv("RISK_CONFLUENCIA", "1.5")) # 1.5% score>=2
MIN_SCORE = int(os.getenv("MIN_SCORE", "2"))                  # score mínimo para operar
MAX_HOLD_HOURS = int(os.getenv("MAX_HOLD_HOURS", "16"))
COOLDOWN_HOURS = int(os.getenv("COOLDOWN_HOURS", "2"))

# Compatibilidad con módulos reutilizados (risk.py, live_writer.py)
RISK_PER_TRADE = RISK_BASE
TP_RR_RATIO = RR_RATIO

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Rutas Dinámicas
JOURNAL_FILE = f"logs/journal_{BOT_ID}.json"
STATUS_FILE = f"logs/bot_status_{BOT_ID}.json"
OPEN_POSITIONS_FILE = f"logs/open_positions_{BOT_ID}.json"
DASHBOARD_TRADES_FILE = f"logs/dashboard_trades_{BOT_ID}.json"

import logging
import os
import sys
from datetime import datetime

# Forzar UTF-8 en stdout/stderr — necesario en Windows cuando el output se
# redirige a archivo (Task Scheduler, .bat con >> log) o el terminal no
# es UTF-8 por default. Sin esto, los emojis se escapan a ✅ etc.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass  # python <3.7 o stream no reconfigurable; sigue mejor que antes

# Crear carpeta de logs si no existe
if not os.path.exists('logs'):
    os.makedirs('logs')

# Configuración del nombre del archivo (un archivo nuevo por día)
log_filename = f"logs/bot_{datetime.now().strftime('%Y-%m-%d')}.log"

# Formato de los mensajes
log_format = logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 1. Configurar el Logger Principal
logger = logging.getLogger("MS_Bot")
logger.setLevel(logging.DEBUG) # Capturamos todo desde DEBUG para arriba

# 2. Handler para Archivo (Guarda todo, incluyendo errores detallados)
file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setFormatter(log_format)
file_handler.setLevel(logging.INFO)

# 3. Handler para Consola — solo aplica colores si stdout es TTY.
#    Si está redirigido a archivo, usa el formato plano (sin ANSI escapes).
class ColorFormatter(logging.Formatter):
    """Añade colores a los niveles de log en la consola"""
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    blue = "\x1b[36;20m"
    reset = "\x1b[0m"
    format_str = "%(asctime)s | %(levelname)-8s | %(message)s"

    FORMATS = {
        logging.DEBUG: blue + format_str + reset,
        logging.INFO: grey + format_str + reset,
        logging.WARNING: yellow + format_str + reset,
        logging.ERROR: red + format_str + reset,
        logging.CRITICAL: bold_red + format_str + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt='%H:%M:%S')
        return formatter.format(record)

console_handler = logging.StreamHandler(sys.stdout)
if sys.stdout.isatty():
    console_handler.setFormatter(ColorFormatter())
else:
    plain_format = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(plain_format)
console_handler.setLevel(logging.INFO)

# Agregar handlers al logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Evitar que los mensajes se dupliquen si se importa en varios archivos
logger.propagate = False
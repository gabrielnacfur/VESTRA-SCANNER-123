"""
Alerta diário do Setup 1-2-3 via Telegram.

Detecta o setup 1-2-3 de Alta no fechamento de cada ativo da lista (tickers.txt)
e envia mensagem no Telegram com:
  - Ticker
  - Preço de fechamento (C3)
  - Preço de gatilho (máxima do C2 — entrada se rompida no dia seguinte)
  - Preço sugerido de stop (mínima do C3)
  - % de risco entre entrada e stop

Uso:
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python alerta_123.py

  Múltiplos destinatários (separar por vírgula):
  TELEGRAM_CHAT_ID="123456,789012,-1001234567890" python alerta_123.py

  Funciona com: chat individual, grupo ou canal do Telegram.

Em desenvolvimento local:
  python alerta_123.py --dry-run    (não envia, só imprime)
  python alerta_123.py --force      (envia mesmo se nenhum setup, p/ teste)
"""
import os
import sys
import argparse
import datetime as dt
from pathlib import Path

import pandas as pd
import yfinance as yf
import requests


SCRIPT_DIR = Path(__file__).parent
TICKERS_FILE = SCRIPT_DIR / "tickers.txt"


def load_tickers():
    """Lê tickers.txt — um ticker por linha, ignora linhas vazias e comentários."""
    if not TICKERS_FILE.exists():
        print(f"⚠ Arquivo {TICKERS_FILE} não encontrado.")
        return []
    tickers = []
    for line in TICKERS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tickers.append(line.upper())
    return tickers


def detect_setup_123(df):
    """
    Detecta Setup 1-2-3 de Alta no ÚLTIMO candle disponível.
    Retorna dict com info se setup formado HOJE (no candle 3), senão None.

    Regras (versão simplificada):
      C1: close[-3] < close[-4]   (candle de baixa)
      C2: close[-2] > close[-3]   (candle de alta)
      C3: close[-1] < close[-2] and close[-1] >= close[-3]  (correção que segura)

    O setup é avaliado considerando que o último candle do df é o C3 (HOJE).
    """
    if len(df) < 4:
        return None

    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values

    c1 = close[-3] < close[-4]
    c2 = close[-2] > close[-3]
    c3 = close[-1] < close[-2] and close[-1] >= close[-3]

    if not (c1 and c2 and c3):
        return None

    entry_trigger = float(high[-2])     # máxima do C2 → gatilho de entrada
    stop_loss = float(low[-1])          # mínima do C3 → stop sugerido
    close_c3 = float(close[-1])
    risk_pct = (entry_trigger - stop_loss) / entry_trigger * 100

    return {
        "close_c3": close_c3,
        "entry_trigger": entry_trigger,
        "stop_loss": stop_loss,
        "risk_pct": risk_pct,
        "date_c3": df.index[-1].strftime("%d/%m/%Y"),
    }


def fetch_prices(ticker, days_back=15):
    """Baixa OHLC dos últimos N dias úteis via yfinance."""
    try:
        df = yf.Ticker(ticker).history(period=f"{days_back}d", auto_adjust=True)
        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"  ⚠ Erro {ticker}: {e}")
        return None


def scan_all(tickers):
    """Roda detecção em todos os tickers, retorna lista de setups encontrados."""
    setups = []
    for i, ticker in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {ticker}…", end=" ", flush=True)
        df = fetch_prices(ticker)
        if df is None or len(df) < 4:
            print("sem dados")
            continue
        setup = detect_setup_123(df)
        if setup:
            setup["ticker"] = ticker
            setups.append(setup)
            print(f"✓ SETUP! C3 em {setup['date_c3']}, gatilho U$ {setup['entry_trigger']:.2f}")
        else:
            print("não")
    return setups


def format_message(setups):
    """Monta a mensagem do Telegram em Markdown."""
    today = dt.datetime.now().strftime("%d/%m/%Y")
    if not setups:
        return f"📊 *Scanner Setup 1-2-3* — {today}\n\nNenhum setup formado hoje."

    lines = [f"📊 *Scanner Setup 1-2-3* — {today}", ""]
    lines.append(f"🎯 *{len(setups)} setup(s) formado(s) hoje:*\n")

    for s in setups:
        lines.append(f"*{s['ticker']}*")
        lines.append(f"  Fechamento C3: U$ {s['close_c3']:.2f}")
        lines.append(f"  🟢 Gatilho de entrada: *U$ {s['entry_trigger']:.2f}* (máxima C2)")
        lines.append(f"  🔴 Stop sugerido: U$ {s['stop_loss']:.2f} (mínima C3)")
        lines.append(f"  📏 Risco: {s['risk_pct']:.2f}%")
        lines.append("")

    lines.append("_Monitore o gatilho nos próximos pregões. Se a máxima do C2 for rompida, a entrada está acionada._")
    return "\n".join(lines)


def send_telegram(message, bot_token, chat_id):
    """Envia mensagem via API do Telegram. Retorna True se sucesso."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            return True
        print(f"⚠ Telegram retornou {r.status_code}: {r.text}")
        return False
    except Exception as e:
        print(f"⚠ Erro ao enviar Telegram: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="não envia mensagem")
    parser.add_argument("--force", action="store_true", help="envia mesmo sem setups (teste)")
    parser.add_argument("--always-notify", action="store_true",
                        help="envia mensagem mesmo quando 0 setups (default: só envia se >=1)")
    args = parser.parse_args()

    tickers = load_tickers()
    if not tickers:
        print("Nenhum ticker para processar. Adicione tickers em tickers.txt")
        sys.exit(1)

    print(f"Scanner Setup 1-2-3 — {len(tickers)} ativos\n")
    setups = scan_all(tickers)

    print(f"\n=== RESULTADO ===")
    print(f"{len(setups)} setup(s) detectado(s)")

    message = format_message(setups)
    print("\n--- Mensagem ---")
    print(message)
    print("--- fim ---\n")

    if args.dry_run:
        print("(dry-run: não enviado)")
        return

    if not setups and not args.force and not args.always_notify:
        print("Nenhum setup hoje, pulando envio. Use --always-notify para enviar mesmo assim.")
        return

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids_raw = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_ids_raw:
        print("⚠ TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados.")
        sys.exit(1)

    # Suporta múltiplos destinatários separados por vírgula
    # Ex: TELEGRAM_CHAT_ID="123456,789012,-1001234567890"
    # Funciona com: chat individual, grupo ou canal
    chat_ids = [cid.strip() for cid in chat_ids_raw.split(",") if cid.strip()]
    success = 0
    for cid in chat_ids:
        if send_telegram(message, bot_token, cid):
            print(f"✓ Mensagem enviada para {cid}")
            success += 1
        else:
            print(f"⚠ Falha ao enviar para {cid}")

    if success == 0:
        sys.exit(1)
    print(f"\n✓ {success}/{len(chat_ids)} destinatário(s) receberam.")


if __name__ == "__main__":
    main()

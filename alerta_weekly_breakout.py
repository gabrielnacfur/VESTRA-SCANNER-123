"""
Alerta de Rompimento Semanal (Long + Short) via Telegram.

Monitora preços DURANTE o pregão (a cada 15 min) e envia alerta quando:

  LONG  → preço atual rompe a máxima das últimas N semanas E SMA20 está subindo
  SHORT → preço atual rompe a mínima das últimas N semanas E SMA20 está descendo

A janela de semanas (N) é configurável no topo do arquivo: N_WEEKS = 1 (padrão)

Lista de ativos: weekly_tickers.txt (separada da estratégia 1-2-3)

Anti-spam: guarda estado em alert_state.json — só notifica UMA VEZ por ativo
por direção (long/short) por semana. No início da semana seguinte, o estado
é resetado e novos rompimentos podem ser alertados.

Uso:
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python alerta_weekly_breakout.py
  python alerta_weekly_breakout.py --dry-run    (não envia, só imprime)
"""
import os
import sys
import json
import argparse
import datetime as dt
from pathlib import Path

import pandas as pd
import yfinance as yf
import requests


SCRIPT_DIR = Path(__file__).parent
TICKERS_FILE = SCRIPT_DIR / "weekly_tickers.txt"
STATE_FILE = SCRIPT_DIR / "alert_state.json"

# ============ CONFIGURAÇÃO ============
N_WEEKS = 2  # ⚠️ ALTERE AQUI: 1, 2, 3 ou 4 semanas anteriores
# ======================================


def load_tickers():
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


def load_state():
    """Carrega estado anti-spam. Reseta automaticamente se semana mudou."""
    if not STATE_FILE.exists():
        return {"week_id": current_week_id(), "alerts": {}}
    try:
        state = json.loads(STATE_FILE.read_text())
        if state.get("week_id") != current_week_id():
            print(f"📅 Nova semana detectada — resetando estado de alertas.")
            return {"week_id": current_week_id(), "alerts": {}}
        return state
    except Exception:
        return {"week_id": current_week_id(), "alerts": {}}


def save_state(state):
    state["week_id"] = current_week_id()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def current_week_id():
    """Identificador único da semana atual: 'AAAA-WW' (ano-semana ISO)."""
    today = dt.date.today()
    iso = today.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def fetch_intraday_prices(ticker, lookback_days=60):
    """Baixa cotações: intraday recente + diário pra calcular ranges semanais e SMA."""
    try:
        # Diário pra cálculo de máxima/mínima das semanas anteriores e SMA20
        daily = yf.Ticker(ticker).history(period=f"{lookback_days}d", auto_adjust=True)
        if daily.empty or len(daily) < 25:
            return None, None

        # Cotação atual: pega o último preço (1m intraday se mercado aberto, senão close diário)
        try:
            intraday = yf.Ticker(ticker).history(period="1d", interval="1m", auto_adjust=True)
            if not intraday.empty:
                current_price = float(intraday["Close"].iloc[-1])
                current_high_today = float(intraday["High"].max())
                current_low_today = float(intraday["Low"].min())
            else:
                current_price = float(daily["Close"].iloc[-1])
                current_high_today = float(daily["High"].iloc[-1])
                current_low_today = float(daily["Low"].iloc[-1])
        except Exception:
            current_price = float(daily["Close"].iloc[-1])
            current_high_today = float(daily["High"].iloc[-1])
            current_low_today = float(daily["Low"].iloc[-1])

        return daily, {
            "price": current_price,
            "high_today": current_high_today,
            "low_today": current_low_today,
        }
    except Exception as e:
        print(f"  ⚠ Erro {ticker}: {e}")
        return None, None


def detect_breakout(daily_df, current, n_weeks):
    """
    Detecta rompimento. Retorna dict com info se houver rompimento, senão None.

    Considera rompimento se:
    - Today's High > max_high_prev_n_weeks (LONG) com SMA20 subindo
    - Today's Low < min_low_prev_n_weeks (SHORT) com SMA20 descendo
    """
    df = daily_df.copy()
    df["_date"] = df.index.date

    # Identifica semana de cada candle
    iso = pd.to_datetime(df.index).isocalendar()
    df["_week_id"] = iso.year.astype(str) + "-" + iso.week.astype(str).str.zfill(2)

    # Semana atual
    today_week = current_week_id()

    # Semanas anteriores (em ordem cronológica), excluindo a atual
    weeks_in_data = list(dict.fromkeys(df["_week_id"].tolist()))
    if today_week in weeks_in_data:
        weeks_in_data.remove(today_week)

    if len(weeks_in_data) < n_weeks:
        return None  # dados insuficientes

    target_weeks = weeks_in_data[-n_weeks:]
    mask = df["_week_id"].isin(target_weeks)
    ref_high = float(df.loc[mask, "High"].max())
    ref_low = float(df.loc[mask, "Low"].min())

    # SMA20 e inclinação
    sma20 = df["Close"].rolling(window=20, min_periods=20).mean()
    if pd.isna(sma20.iloc[-1]) or pd.isna(sma20.iloc[-2]):
        return None
    sma_today = float(sma20.iloc[-1])
    sma_yest = float(sma20.iloc[-2])
    sma_rising = sma_today > sma_yest
    sma_falling = sma_today < sma_yest

    # Detecta rompimento
    breakout_high = current["high_today"] > ref_high
    breakout_low = current["low_today"] < ref_low

    long_signal = breakout_high and sma_rising
    short_signal = breakout_low and sma_falling

    if not (long_signal or short_signal):
        return None

    return {
        "long": long_signal,
        "short": short_signal,
        "current_price": current["price"],
        "current_high": current["high_today"],
        "current_low": current["low_today"],
        "ref_high": ref_high,
        "ref_low": ref_low,
        "sma20": sma_today,
        "sma_rising": sma_rising,
        "sma_falling": sma_falling,
        "n_weeks": n_weeks,
    }


def scan_all(tickers, state):
    """Roda detecção em todos. Retorna lista de novos rompimentos (não alertados ainda esta semana)."""
    new_alerts = []
    for i, ticker in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {ticker}…", end=" ", flush=True)
        daily, current = fetch_intraday_prices(ticker)
        if daily is None or current is None:
            print("sem dados")
            continue

        breakout = detect_breakout(daily, current, N_WEEKS)
        if not breakout:
            print("não")
            continue

        # Verifica se já alertou esta semana pra esta direção
        already_long = state["alerts"].get(f"{ticker}_LONG", False)
        already_short = state["alerts"].get(f"{ticker}_SHORT", False)

        new_long = breakout["long"] and not already_long
        new_short = breakout["short"] and not already_short

        if not (new_long or new_short):
            if breakout["long"] or breakout["short"]:
                print("já alertado esta semana")
            else:
                print("não")
            continue

        breakout["ticker"] = ticker
        breakout["new_long"] = new_long
        breakout["new_short"] = new_short
        new_alerts.append(breakout)

        if new_long:
            state["alerts"][f"{ticker}_LONG"] = True
        if new_short:
            state["alerts"][f"{ticker}_SHORT"] = True

        directions = []
        if new_long:
            directions.append(f"🟢 LONG U$ {breakout['current_high']:.2f} > U$ {breakout['ref_high']:.2f}")
        if new_short:
            directions.append(f"🔴 SHORT U$ {breakout['current_low']:.2f} < U$ {breakout['ref_low']:.2f}")
        print("✓ ROMPIMENTO! " + " | ".join(directions))

    return new_alerts


def format_message(alerts, n_weeks):
    now = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    if not alerts:
        return None  # sem mensagem se não houver rompimento novo

    lines = [f"🔔 *Rompimento Semanal ({n_weeks}S)* — {now}", ""]
    lines.append(f"*{len(alerts)} novo(s) rompimento(s):*\n")

    for a in alerts:
        lines.append(f"*{a['ticker']}*  · preço atual U$ {a['current_price']:.2f}")
        if a["new_long"]:
            lines.append(f"  🟢 *LONG* — High {a['current_high']:.2f} rompeu máx {n_weeks}S de U$ {a['ref_high']:.2f}")
            lines.append(f"     SMA20: {a['sma20']:.2f} ↗ subindo")
        if a["new_short"]:
            lines.append(f"  🔴 *SHORT* — Low {a['current_low']:.2f} rompeu mín {n_weeks}S de U$ {a['ref_low']:.2f}")
            lines.append(f"     SMA20: {a['sma20']:.2f} ↘ descendo")
        lines.append("")

    lines.append(f"_Janela de {n_weeks} semana(s) anterior(es). Filtro SMA20 com inclinação ativo._")
    lines.append(f"_Próximo alerta deste ativo só na semana que vem._")
    return "\n".join(lines)


def send_telegram(message, bot_token, chat_ids_raw):
    chat_ids = [cid.strip() for cid in chat_ids_raw.split(",") if cid.strip()]
    success = 0
    for cid in chat_ids:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            r = requests.post(url, json={
                "chat_id": cid, "text": message,
                "parse_mode": "Markdown", "disable_web_page_preview": True,
            }, timeout=15)
            if r.status_code == 200:
                print(f"✓ Enviado para {cid}")
                success += 1
            else:
                print(f"⚠ Erro {r.status_code} no chat {cid}: {r.text}")
        except Exception as e:
            print(f"⚠ Falha no chat {cid}: {e}")
    return success > 0


def is_market_open_us():
    """Verifica se mercado americano está aberto (9:30-16:00 ET, seg-sex).
    Aproximação: usa horário de Brasília UTC-3 e converte. Não considera feriados."""
    now_utc = dt.datetime.utcnow()
    # NY = UTC-5 (EST) ou UTC-4 (EDT). Usar -5 como conservador.
    ny_hour = (now_utc.hour - 5) % 24
    weekday = now_utc.weekday()  # 0=seg
    if weekday >= 5:  # sáb/dom
        return False
    # Mercado aberto: 9:30 - 16:00 NY
    return 9 <= ny_hour < 16 or (ny_hour == 9 and now_utc.minute >= 30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ignore-market-hours", action="store_true",
                        help="Ignora verificação de horário de mercado")
    args = parser.parse_args()

    if not args.ignore_market_hours and not is_market_open_us():
        print("📕 Mercado americano fechado — pulando varredura.")
        return

    tickers = load_tickers()
    if not tickers:
        print("Nenhum ticker em weekly_tickers.txt")
        sys.exit(1)

    print(f"🔍 Scanner Rompimento Semanal ({N_WEEKS}S) — {len(tickers)} ativos\n")
    state = load_state()
    print(f"Semana atual: {state['week_id']} | Alertas já enviados: {len(state['alerts'])}\n")

    alerts = scan_all(tickers, state)
    print(f"\n=== {len(alerts)} novo(s) rompimento(s) ===\n")

    message = format_message(alerts, N_WEEKS)
    if message is None:
        print("Sem novos rompimentos. Estado salvo.")
        save_state(state)
        return

    print(message)
    print()

    if args.dry_run:
        print("(dry-run: não enviado, estado NÃO salvo)")
        return

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_ids:
        print("⚠ Secrets não configurados.")
        sys.exit(1)

    if send_telegram(message, bot_token, chat_ids):
        save_state(state)
        print("✓ Estado atualizado.")
    else:
        print("⚠ Envio falhou — estado NÃO salvo, alertas serão tentados de novo.")
        sys.exit(1)


if __name__ == "__main__":
    main()

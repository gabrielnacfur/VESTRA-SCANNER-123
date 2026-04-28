# Alertas de Estratégias via Telegram

Este repositório roda **2 scanners independentes** que enviam alertas no Telegram:

1. **Setup 1-2-3** (`alerta_123.py`) — roda 1× por dia após o fechamento do mercado
2. **Rompimento Semanal Long+Short** (`alerta_weekly_breakout.py`) — roda a cada 15 min durante o pregão

Cada um tem sua **própria lista de tickers** e seu **próprio workflow** no GitHub Actions, mas compartilham os mesmos secrets do Telegram.

---

## Configuração inicial (1× só)

### 1. Bot do Telegram
- Fale com **@BotFather** no Telegram
- `/newbot` → escolha nome e username
- Guarde o **TOKEN**

### 2. Chat ID
- Mande "oi" para o bot
- Acesse `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates`
- Procure `"chat":{"id":...}`
- Para canal: adicione o bot como admin, mande mensagem no canal antes
- Múltiplos destinatários: separe por vírgula (`123,456,-1001234567890`)

### 3. Secrets no GitHub
- **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_BOT_TOKEN` = token do BotFather
- `TELEGRAM_CHAT_ID` = chat id (ou múltiplos)

---

## Estrutura do repositório

```
alerta/
├── alerta_123.py                  ← scanner 1-2-3 (1×/dia)
├── alerta_weekly_breakout.py      ← scanner rompimento semanal (15 em 15 min)
├── tickers.txt                    ← ativos do 1-2-3
├── weekly_tickers.txt             ← ativos do rompimento semanal
├── alert_state.json               ← estado anti-spam (auto-gerado)
├── requirements.txt
└── .github/workflows/
    ├── scanner.yml                ← workflow do 1-2-3
    └── weekly_breakout.yml        ← workflow do rompimento semanal
```

---

## Scanner 1-2-3

Roda 1×/dia às 18:30 BRT, detecta se o último candle é o C3 de um Setup 1-2-3, manda alerta com gatilho e stop. Edite `tickers.txt` pra mudar a lista.

---

## Scanner Rompimento Semanal

### Como funciona
- Roda a cada **15 minutos durante o pregão americano** (9:30-16:00 NY)
- **LONG**: alerta se High atual romper máxima das últimas N semanas E SMA20 subindo
- **SHORT**: alerta se Low atual romper mínima das últimas N semanas E SMA20 descendo
- **Anti-spam**: cada par (ticker, direção) alertado apenas **uma vez por semana**

### Configurar a janela
Abra `alerta_weekly_breakout.py` e edite:

```python
N_WEEKS = 2  # 1, 2, 3 ou 4 semanas
```

Commite e a próxima execução usa o valor novo.

### Editar lista
Edite `weekly_tickers.txt` (independente do 1-2-3).

### Mensagem exemplo
```
🔔 Rompimento Semanal (2S) — 21/04/2026 14:45

3 novo(s) rompimento(s):

NVDA  · preço atual U$ 920.50
  🟢 LONG — High 921.30 rompeu máx 2S de U$ 915.80
     SMA20: 895.20 ↗ subindo

META  · preço atual U$ 480.10
  🔴 SHORT — Low 479.50 rompeu mín 2S de U$ 482.00
     SMA20: 495.00 ↘ descendo
```

### Estado anti-spam
O arquivo `alert_state.json` mantém quem já foi alertado nesta semana:

```json
{
  "week_id": "2026-17",
  "alerts": {
    "NVDA_LONG": true,
    "META_SHORT": true
  }
}
```

Reseta automaticamente quando muda a semana. O workflow faz commit desse arquivo de volta ao repo após cada execução (por isso `permissions: contents: write` no YAML).

---

## Testar localmente

```bash
pip install -r requirements.txt

# Dry-run (não envia)
python alerta_weekly_breakout.py --dry-run

# Forçar execução fora do horário do pregão
python alerta_weekly_breakout.py --ignore-market-hours --dry-run

# Com Telegram real
TELEGRAM_BOT_TOKEN="..." TELEGRAM_CHAT_ID="..." python alerta_weekly_breakout.py --ignore-market-hours
```

---

## Limitações conhecidas

- **GitHub Actions latência de 5-15 min** quando sob carga. Cron de 15 min na prática roda em ~15-25 min.
- **Yahoo Finance** tem ~1-2 min de delay nos dados intraday — OK pro intervalo de 15 min.
- **Verificação de mercado aberto** é aproximada (UTC-5 fixo, não considera feriados nem DST). Use `--ignore-market-hours` se precisar.
- **Inatividade**: GitHub desativa workflows agendados após 60 dias sem atividade. Edite qualquer arquivo de vez em quando.

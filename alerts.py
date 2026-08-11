"""
Alertas de Conta — Platah
Roda todo dia (seg-sex) via Railway cron
Verifica status de pagamento e orçamento nas contas de anúncio
Cada problema gera uma mensagem separada (para encaminhar ao cliente)
Destinatários: Jeison + Maicon
"""

import os
import requests
from datetime import date, timedelta

# ─── Config ───────────────────────────────────────────────────────────────────

WINDSOR_API_KEY   = os.environ["WINDSOR_API_KEY"]
ZAPI_INSTANCE     = os.environ["ZAPI_INSTANCE"]
ZAPI_TOKEN        = os.environ["ZAPI_TOKEN"]
ZAPI_CLIENT_TOKEN = os.environ["ZAPI_CLIENT_TOKEN"]

ALERT_RECIPIENTS = [
    os.environ.get("WHATSAPP_PERSONAL", "5547991655505"),  # Jeison
    "5547997497459",                                        # Maicon
]

# ─── Contas por cliente ───────────────────────────────────────────────────────

META_ACCOUNTS = {
    "Oimu":         ["3132055553730364", "356767994192116"],
    "Kukiê":        ["736573021440943"],
    "Infanti":      ["943835795646882", "2395094684162485"],
    "Vic & Johnny": ["342095542654544"],
    "Undertop":     ["446436329249349", "8262146623914189"],
    "787 Shirts":   ["345077603197566"],
    "Monnari":      ["267750700965455"],
    "Serinah":      ["1697338310459624"],
}

GOOGLE_ACCOUNTS = {
    "Oimu":         ["7132104797"],
    "Kukiê":        ["4026094180"],
    "Infanti":      ["1383946764"],
    "Vic & Johnny": ["2798923099"],
    "Undertop":     ["2341810352"],
    "787 Shirts":   ["2276201464"],
    "Monnari":      ["4241884415"],
}

TIKTOK_ACCOUNTS = {
    "787 Shirts": ["7060470095306375170"],
}

# Status Meta Ads → descrição legível
META_STATUS = {
    1: None,           # Ativa — sem alerta
    2: "conta desativada",
    3: "problema de pagamento (unsettled)",
    7: "conta em revisão de risco",
    9: "verba esgotada ou cartão recusado",
}

# ─── WhatsApp ─────────────────────────────────────────────────────────────────

def send_whatsapp(text):
    url = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}/send-text"
    headers = {"Content-Type": "application/json", "Client-Token": ZAPI_CLIENT_TOKEN}
    for recipient in ALERT_RECIPIENTS:
        resp = requests.post(url, headers=headers,
                             json={"phone": recipient, "message": text}, timeout=15)
        print(f"  {'OK' if resp.ok else 'ERRO'} -> {recipient}")

# ─── Meta Ads — status das contas ─────────────────────────────────────────────

def verificar_meta():
    """Consulta status de cada conta Meta via Graph API. Retorna lista de alertas."""
    token_resp = requests.get(
        "https://connectors.windsor.ai/facebook",
        params={
            "api_key":  WINDSOR_API_KEY,
            "fields":   "account_id,account_status",
            "accounts": ",".join(a for contas in META_ACCOUNTS.values() for a in contas),
            "date_from": date.today().strftime("%Y-%m-%d"),
            "date_to":   date.today().strftime("%Y-%m-%d"),
        },
        timeout=60,
    )
    rows = token_resp.json() if token_resp.ok else []
    if isinstance(rows, dict):
        rows = rows.get("data", [])

    # Mapeia account_id -> status
    status_map = {str(r.get("account_id", "")): r.get("account_status") for r in rows}

    alertas = []
    for cliente, contas in META_ACCOUNTS.items():
        for account_id in contas:
            status = status_map.get(str(account_id))
            if status is None:
                continue
            try:
                status = int(status)
            except (ValueError, TypeError):
                continue
            descricao = META_STATUS.get(status)
            if descricao:
                alertas.append((cliente, "Meta Ads", descricao, account_id))

    return alertas


# ─── Google Ads — orçamento e cobrança ────────────────────────────────────────

def verificar_google():
    """Verifica orçamento e status de cobrança nas contas Google Ads."""
    dev_token     = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    client_id     = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

    if not dev_token:
        return []

    try:
        token = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        }, timeout=10).json()["access_token"]
    except Exception as e:
        print(f"  Google token erro: {e}")
        return []

    alertas = []

    for cliente, cids in GOOGLE_ACCOUNTS.items():
        for cid in cids:
            headers = {
                "Authorization":   f"Bearer {token}",
                "developer-token": dev_token,
                "login-customer-id": cid,
                "Content-Type":    "application/json",
            }

            # 1. Verifica status de cobrança
            try:
                resp = requests.post(
                    f"https://googleads.googleapis.com/v23/customers/{cid}/googleAds:search",
                    headers=headers,
                    json={"query": "SELECT billing_setup.status FROM billing_setup"},
                    timeout=30,
                )
                if resp.ok:
                    for row in resp.json().get("results", []):
                        status = row.get("billingSetup", {}).get("status", "")
                        if status not in ("APPROVED", "APPROVED_HELD"):
                            alertas.append((
                                cliente, "Google Ads",
                                f"problema de cobrança ({status.lower().replace('_', ' ')})",
                                cid
                            ))
            except Exception as e:
                print(f"  Google billing erro {cid}: {e}")

            # 2. Verifica orçamento da conta
            try:
                resp = requests.post(
                    f"https://googleads.googleapis.com/v23/customers/{cid}/googleAds:search",
                    headers=headers,
                    json={"query": (
                        "SELECT account_budget.status, "
                        "account_budget.amount_served_micros, "
                        "account_budget.approved_spending_limit_micros "
                        "FROM account_budget "
                        "WHERE account_budget.status = 'APPROVED'"
                    )},
                    timeout=30,
                )
                if resp.ok:
                    for row in resp.json().get("results", []):
                        budget = row.get("accountBudget", {})
                        served  = float(budget.get("amountServedMicros", 0)) / 1_000_000
                        limit_s = budget.get("approvedSpendingLimitMicros", "0")
                        # UNLIMITED = string vazia ou None
                        if not limit_s or limit_s in ("", "UNSPECIFIED", "UNKNOWN"):
                            continue
                        limit = float(limit_s) / 1_000_000
                        # Alerta apenas se consumiu >= 95% do limite aprovado
                        if limit > 0 and served >= limit * 0.95:
                            alertas.append((
                                cliente, "Google Ads",
                                f"orçamento quase esgotado ({served:,.0f}/{limit:,.0f})",
                                cid
                            ))
            except Exception as e:
                print(f"  Google budget erro {cid}: {e}")

    return alertas


# ─── TikTok — gasto zero como proxy ───────────────────────────────────────────

def verificar_tiktok(yesterday):
    """TikTok não expõe status de conta — usa gasto zero como proxy."""
    todas_contas = [a for contas in TIKTOK_ACCOUNTS.values() for a in contas]
    try:
        resp = requests.get(
            "https://connectors.windsor.ai/tiktok",
            params={
                "api_key":   WINDSOR_API_KEY,
                "fields":    "account_id,spend",
                "date_from": yesterday,
                "date_to":   yesterday,
                "accounts":  ",".join(todas_contas),
            },
            timeout=60,
        )
        rows = resp.json()
        if isinstance(rows, dict):
            rows = rows.get("data", [])
    except Exception as e:
        print(f"  TikTok erro: {e}")
        return []

    alertas = []
    for cliente, contas in TIKTOK_ACCOUNTS.items():
        account_set  = set(str(a) for a in contas)
        rows_cliente = [r for r in rows if str(r.get("account_id", "")) in account_set]
        total_spend  = sum(float(r.get("spend") or 0) for r in rows_cliente)
        if rows_cliente and total_spend == 0:
            alertas.append((cliente, "TikTok Ads", "sem gasto ontem (possível verba esgotada)", contas[0]))

    return alertas


# ─── Monta mensagem por problema ──────────────────────────────────────────────

def montar_mensagem(cliente, plataforma, descricao, account_id, data_fmt):
    emoji = {"Meta Ads": "📣", "Google Ads": "🔍", "TikTok Ads": "🎵"}.get(plataforma, "⚠️")
    return (
        f"{emoji} *Alerta de conta — {cliente}*\n"
        f"\n"
        f"Plataforma: *{plataforma}*\n"
        f"Problema: *{descricao}*\n"
        f"Conta ID: `{account_id}`\n"
        f"\n"
        f"_Verificado em {data_fmt}. Por favor confirme no gerenciador de anúncios._"
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def run():
    if date.today().weekday() in (5, 6):
        print("Fim de semana -- sem alertas.")
        return

    yesterday_fmt = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
    today_fmt     = date.today().strftime("%d/%m/%Y")
    yesterday     = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Verificando alertas de conta — {today_fmt}")

    alertas  = []
    alertas += verificar_meta()
    alertas += verificar_google()
    alertas += verificar_tiktok(yesterday)

    if not alertas:
        print("Nenhum problema encontrado -- tudo OK!")
        return

    print(f"{len(alertas)} problema(s) encontrado(s)")

    for cliente, plataforma, descricao, account_id in alertas:
        msg = montar_mensagem(cliente, plataforma, descricao, account_id, today_fmt)
        print(f"  Enviando alerta: {cliente} / {plataforma}")
        print(msg)
        send_whatsapp(msg)

    print("Alertas enviados.")


if __name__ == "__main__":
    run()

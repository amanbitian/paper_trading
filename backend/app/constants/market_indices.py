from __future__ import annotations


def normalize_index_code(index_code: str) -> str:
    return index_code.strip().upper().replace(" ", "").replace("-", "").replace("_", "")


NSE_INDEX_MEMBERSHIP_URLS: dict[str, str] = {
    "NIFTY 50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY 100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "NIFTY 200": "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "NIFTY 500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "BANKNIFTY": "https://nsearchives.nseindia.com/content/indices/ind_niftybanklist.csv",
    "FINNIFTY": "https://nsearchives.nseindia.com/content/indices/ind_niftyfinancelist.csv",
    "MIDCPNIFTY": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcapselect_list.csv",
}

INDEX_DEFINITIONS: dict[str, dict[str, str | None]] = {
    "NIFTY50": {
        "index_name": "NIFTY 50",
        "provider": "NSE",
        "exchange": "NSE",
        "yahoo_symbol": "^NSEI",
        "membership_url": NSE_INDEX_MEMBERSHIP_URLS["NIFTY 50"],
        "stock_flag": "is_nifty50",
    },
    "NIFTY100": {
        "index_name": "NIFTY 100",
        "provider": "NSE",
        "exchange": "NSE",
        "yahoo_symbol": "^CNX100",
        "membership_url": NSE_INDEX_MEMBERSHIP_URLS["NIFTY 100"],
        "stock_flag": "is_nifty100",
    },
    "NIFTY200": {
        "index_name": "NIFTY 200",
        "provider": "NSE",
        "exchange": "NSE",
        "yahoo_symbol": None,
        "membership_url": NSE_INDEX_MEMBERSHIP_URLS["NIFTY 200"],
        "stock_flag": "is_nifty200",
    },
    "NIFTY500": {
        "index_name": "NIFTY 500",
        "provider": "NSE",
        "exchange": "NSE",
        "yahoo_symbol": None,
        "membership_url": NSE_INDEX_MEMBERSHIP_URLS["NIFTY 500"],
        "stock_flag": "is_nifty500",
    },
    "BANKNIFTY": {
        "index_name": "NIFTY Bank",
        "provider": "NSE",
        "exchange": "NSE",
        "yahoo_symbol": "^NSEBANK",
        "membership_url": NSE_INDEX_MEMBERSHIP_URLS["BANKNIFTY"],
        "stock_flag": "is_banknifty",
    },
    "FINNIFTY": {
        "index_name": "NIFTY Financial Services",
        "provider": "NSE",
        "exchange": "NSE",
        "yahoo_symbol": "NIFTY_FIN_SERVICE.NS",
        "membership_url": NSE_INDEX_MEMBERSHIP_URLS["FINNIFTY"],
        "stock_flag": "is_finnifty",
    },
    "MIDCPNIFTY": {
        "index_name": "NIFTY Midcap Select",
        "provider": "NSE",
        "exchange": "NSE",
        "yahoo_symbol": "NIFTY_MID_SELECT.NS",
        "membership_url": NSE_INDEX_MEMBERSHIP_URLS["MIDCPNIFTY"],
        "stock_flag": "is_midcpnifty",
    },
    "SENSEX": {
        "index_name": "S&P BSE Sensex",
        "provider": "BSE",
        "exchange": "BSE",
        "yahoo_symbol": "^BSESN",
        "membership_url": None,
        "stock_flag": "is_sensex",
    },
}

INDEX_MEMBERSHIP_URLS_BY_CODE: dict[str, str] = {
    code: definition["membership_url"]
    for code, definition in INDEX_DEFINITIONS.items()
    if definition.get("membership_url")
}

STOCK_INDEX_FLAG_COLUMNS: dict[str, str] = {
    code: str(definition["stock_flag"])
    for code, definition in INDEX_DEFINITIONS.items()
    if definition.get("stock_flag")
}

STOCK_INDEX_FILTER_OPTIONS: list[dict[str, str]] = [
    {
        "label": str(definition["index_name"]),
        "value": code,
        "flag_column": str(definition["stock_flag"]),
    }
    for code, definition in INDEX_DEFINITIONS.items()
    if definition.get("stock_flag")
]

# Standalone nse_csv_* tables (from scripts/create_nse_index_csv_tables.py)
NSE_CSV_INDEX_TABLES: dict[str, str] = {
    "nifty50": "nse_csv_nifty_50",
    "nifty100": "nse_csv_nifty_100",
    "nifty200": "nse_csv_nifty_200",
    "nifty500": "nse_csv_nifty_500",
    "banknifty": "nse_csv_banknifty",
    "finnifty": "nse_csv_finnifty",
    "midcpnifty": "nse_csv_midcpnifty",
}

NSE_CSV_TREND_FILTER_OPTIONS: list[dict[str, str]] = [
    {"label": "NIFTY 50", "value": "nifty50", "table_name": NSE_CSV_INDEX_TABLES["nifty50"]},
    {"label": "NIFTY 100", "value": "nifty100", "table_name": NSE_CSV_INDEX_TABLES["nifty100"]},
    {"label": "NIFTY 200", "value": "nifty200", "table_name": NSE_CSV_INDEX_TABLES["nifty200"]},
    {"label": "NIFTY 500", "value": "nifty500", "table_name": NSE_CSV_INDEX_TABLES["nifty500"]},
    {"label": "NIFTY Bank", "value": "banknifty", "table_name": NSE_CSV_INDEX_TABLES["banknifty"]},
    {"label": "NIFTY Financial Services", "value": "finnifty", "table_name": NSE_CSV_INDEX_TABLES["finnifty"]},
    {"label": "NIFTY Midcap Select", "value": "midcpnifty", "table_name": NSE_CSV_INDEX_TABLES["midcpnifty"]},
]

NSE_CSV_INDEX_LABELS: dict[str, str] = {
    option["value"]: option["label"] for option in NSE_CSV_TREND_FILTER_OPTIONS
}


def build_nse_csv_symbol_exists_sql(*, param_name: str = "nifty_index", stock_alias: str = "s") -> str:
    return "\n".join(
        (
            f"          OR (:{param_name} = '{option['value']}' AND EXISTS ("
            f"SELECT 1 FROM {option['table_name']} csv "
            f"WHERE UPPER(csv.symbol) = UPPER({stock_alias}.symbol)))"
        )
        for option in NSE_CSV_TREND_FILTER_OPTIONS
    )


def stock_index_flag_for_code(index_code: str | None) -> str | None:
    if not index_code:
        return None
    return STOCK_INDEX_FLAG_COLUMNS.get(normalize_index_code(index_code))

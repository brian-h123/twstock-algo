from twstock.universe import get_top50_universe

TICKER_NAMES: dict[str, str] = {
    "2330": "台積電",
    "2454": "聯發科",
    "2308": "台達電",
    "2317": "鴻海",
    "3711": "日月光投控",
    "2383": "台光電",
    "2345": "智邦",
    "3037": "欣興",
    "2303": "聯電",
    "2891": "中信金",
    "2382": "廣達",
    "2360": "致茂",
    "2881": "富邦金",
    "3017": "奇鋐",
    "2882": "國泰金",
    "2885": "元大金",
    "2327": "國巨",
    "2887": "台新新光金",
    "6669": "緯穎",
    "2412": "中華電",
    "2884": "玉山金",
    "2886": "兆豐金",
    "2368": "金像電",
    "2357": "華碩",
    "3231": "緯創",
    "1303": "南亞",
    "2301": "光寶科",
    "2890": "永豐金",
    "3661": "世芯-KY",
    "1216": "統一",
    "2883": "凱基金",
    "2449": "京元電子",
    "2344": "華邦電",
    "7769": "鴻勁",
    "2892": "第一金",
    "2880": "華南金",
    "2059": "川湖",
    "2408": "南亞科",
    "3653": "健策",
    "3008": "大立光",
    "5880": "合庫金",
    "2603": "長榮",
    "2395": "研華",
    "2002": "中鋼",
    "1301": "台塑",
    "4904": "遠傳",
    "3045": "台灣大",
    "2207": "和泰車",
    "6919": "康霈",
    "6505": "台塑化",
}


def display_label(ticker: str) -> str:
    """Return 'ticker — name' for dropdown display.

    Args:
        ticker: 4-digit ticker string.

    Returns:
        '{ticker} — {name}' if the ticker is in TICKER_NAMES, else '{ticker}'.
    """
    name = TICKER_NAMES.get(ticker)
    if name is None:
        return ticker
    return f"{ticker} — {name}"


__all__ = ["TICKER_NAMES", "display_label", "get_top50_universe"]

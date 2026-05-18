from datetime import date


def get_top50_universe() -> list[str]:
    """Return the TWSE 0050 ETF constituent tickers as a hard-coded snapshot.

    Returns:
        List of 50 ticker strings (e.g. "2330"), ordered by index weight
        descending as of UNIVERSE_SNAPSHOT_DATE.

    Source:
        https://www.yuantaetfs.com/product/detail/0050/ratio
        Snapshot date: 2026-05-08 (published weight date on source page)
    """
    return [
        "2330",  # 台積電
        "2454",  # 聯發科
        "2308",  # 台達電
        "2317",  # 鴻海
        "3711",  # 日月光投控
        "2383",  # 台光電
        "2345",  # 智邦
        "3037",  # 欣興
        "2303",  # 聯電
        "2891",  # 中信金
        "2382",  # 廣達
        "2360",  # 致茂
        "2881",  # 富邦金
        "3017",  # 奇鋐
        "2882",  # 國泰金
        "2885",  # 元大金
        "2327",  # 國巨
        "2887",  # 台新新光金
        "6669",  # 緯穎
        "2412",  # 中華電
        "2884",  # 玉山金
        "2886",  # 兆豐金
        "2368",  # 金像電
        "2357",  # 華碩
        "3231",  # 緯創
        "1303",  # 南亞
        "2301",  # 光寶科
        "2890",  # 永豐金
        "3661",  # 世芯-KY
        "1216",  # 統一
        "2883",  # 凱基金
        "2449",  # 京元電子
        "2344",  # 華邦電
        "7769",  # 鴻勁
        "2892",  # 第一金
        "2880",  # 華南金
        "2059",  # 川湖
        "2408",  # 南亞科
        "3653",  # 健策
        "3008",  # 大立光
        "5880",  # 合庫金
        "2603",  # 長榮
        "2395",  # 研華
        "2002",  # 中鋼
        "1301",  # 台塑
        "4904",  # 遠傳
        "3045",  # 台灣大
        "2207",  # 和泰車
        "6919",  # 康霈
        "6505",  # 台塑化
    ]


def get_listing_dates() -> dict[str, date]:
    """Return the TWSE listing date for each ticker in the Top-50 universe.

    Returns:
        Dict mapping ticker string to its TWSE listing date (掛牌日期).

    Source:
        TWSE Open API t187ap03_L, fetched 2026-05-17.
        Re-run scripts/fetch_listing_dates.py when the universe expands.
    """
    return {
        "2330": date(1994, 9, 5),
        "2454": date(2001, 7, 23),
        "2308": date(1988, 12, 19),
        "2317": date(1991, 6, 18),
        "3711": date(2018, 4, 30),
        "2383": date(1998, 11, 27),
        "2345": date(1995, 11, 15),
        "3037": date(2002, 8, 26),
        "2303": date(1985, 7, 16),
        "2891": date(2002, 5, 17),
        "2382": date(1999, 1, 8),
        "2360": date(1996, 12, 21),
        "2881": date(2001, 12, 19),
        "3017": date(2002, 9, 27),
        "2882": date(2001, 12, 31),
        "2885": date(2002, 2, 4),
        "2327": date(1993, 10, 22),
        "2887": date(2002, 2, 18),
        "6669": date(2019, 3, 27),
        "2412": date(2000, 10, 27),
        "2884": date(2002, 1, 28),
        "2886": date(2002, 2, 4),
        "2368": date(1998, 3, 9),
        "2357": date(1996, 11, 14),
        "3231": date(2003, 8, 19),
        "1303": date(1967, 11, 15),
        "2301": date(1995, 11, 17),
        "2890": date(2002, 5, 9),
        "3661": date(2014, 10, 28),
        "1216": date(1987, 12, 28),
        "2883": date(2001, 12, 28),
        "2449": date(2001, 5, 9),
        "2344": date(1995, 10, 18),
        "7769": date(2025, 11, 27),
        "2892": date(2003, 1, 2),
        "2880": date(2001, 12, 19),
        "2059": date(2008, 6, 25),
        "2408": date(2000, 8, 17),
        "3653": date(2009, 11, 18),
        "3008": date(2002, 3, 11),
        "5880": date(2011, 12, 1),
        "2603": date(1987, 9, 21),
        "2395": date(1999, 12, 13),
        "2002": date(1974, 12, 26),
        "1301": date(1964, 7, 27),
        "4904": date(2005, 8, 24),
        "3045": date(2002, 8, 26),
        "2207": date(1997, 2, 25),
        "6919": date(2024, 10, 2),
        "6505": date(2003, 12, 26),
    }

import re
from dataclasses import dataclass


@dataclass
class Signal:
    raw_text: str
    symbol: str
    direction: str
    entry: float
    targets: dict
    stoploss: float
    is_trade_signal: bool = True


DIRECTION_WORDS = ["buy", "long", "bought", "sell", "short"]

DIRECTION_LABEL = {
    "buy": "BUY",
    "long": "BUY",
    "bought": "BUY",
    "sell": "SELL",
    "short": "SELL",
}


def _clean_symbol(raw_symbol):
    symbol = raw_symbol.strip()
    symbol = re.sub(r"[\|｜,]", " ", symbol)
    symbol = re.sub(r"\s+", " ", symbol)
    symbol = symbol.upper()

    # options markers only when attached to a strike digit
    # (e.g. NFTY23150PE -> NFTY23150 PE) but NOT "RELIANCE"
    symbol = re.sub(r"(?<=\d)(CE|PE)\b", r" \1", symbol)

    # long-form option types
    symbol = re.sub(r"\bCALL\b", "CE", symbol)
    symbol = re.sub(r"\bPUT\b", "PE", symbol)

    symbol = re.sub(r"\bNFTY\b", "NIFTY", symbol)
    symbol = re.sub(r"\bB\s*N\b", "BANKNIFTY", symbol)
    symbol = re.sub(r"\bBN\b", "BANKNIFTY", symbol)
    symbol = re.sub(r"\bNIFTY\b", "NIFTY", symbol)
    symbol = re.sub(r"\bBANKNIFTY\b", "BANKNIFTY", symbol)
    symbol = re.sub(r"\bSENSEX\b", "SENSEX", symbol)

    # drop expiry dates from the symbol:
    #   "22 SEP" / "SEP-17" / "17_SEPT" / "SEP 22" -> removed
    months = r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*"
    symbol = re.sub(rf"\b\d{{1,2}}\s*[-._#]?\s*{months}\b", " ", symbol)
    symbol = re.sub(rf"\b{months}\s*[-._#]?\s*\d{{1,2}}\b", " ", symbol)

    symbol = re.sub(r"\s+", " ", symbol)

    return symbol.strip()


def _extract_entry(lower, num_tokens):
    sep = r"[\s:=@>\-]*"
    patterns = [
        # premium directly after the option marker: "BUY 23100 PE 75"
        # -> entry = 75 (number AFTER CE/PE is the entry premium)
        r"(?:ce|pe|call|put)" + sep + r"(-?\d+\.?\d*)",
        # after an explicit direction/level keyword
        r"(?:buy|sell|long|short|above|below|near|at|entry|entered|from)"
        + r"\s*[=:@>\-]?\s*(-?\d+\.?\d*)",
        r"(?:above|below|near|@|at|from|entry)"
        + r"\s*[=:@>\-]?\s*(-?\d+\.?\d*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            return match.group(1)
    return None


def _extract_stoploss(lower):
    match = re.search(
        r"(?:stop\s*loss|stoploss|sl)\s*[=@:\-]?\s*>?\s*(-?\d+\.?\d*)",
        lower,
    )
    if match:
        value = float(match.group(1))
        if value > 0:
            return value
    return None


def _extract_targets(lower):
    targets = {}

    patterns = [
        (r"target\s*1(?![0-9])\s*[=@:\-]?\s*(\d+\.?\d*)", "T1"),
        (r"\bt\s*1(?![0-9])\s*[=@:\-]?\s*(\d+\.?\d*)", "T1"),
        (r"\btg\s*1(?![0-9])\s*[=@:\-]?\s*(\d+\.?\d*)", "T1"),
        (r"target\s*2(?![0-9])\s*[=@:\-]?\s*(\d+\.?\d*)", "T2"),
        (r"\bt\s*2(?![0-9])\s*[=@:\-]?\s*(\d+\.?\d*)", "T2"),
        (r"\btg\s*2(?![0-9])\s*[=@:\-]?\s*(\d+\.?\d*)", "T2"),
        (r"target\s*3(?![0-9])\s*[=@:\-]?\s*(\d+\.?\d*)", "T3"),
        (r"\bt\s*3(?![0-9])\s*[=@:\-]?\s*(\d+\.?\d*)", "T3"),
        (r"\btg\s*3(?![0-9])\s*[=@:\-]?\s*(\d+\.?\d*)", "T3"),
    ]

    for pattern, label in patterns:
        match = re.search(pattern, lower)
        if match:
            value = float(match.group(1))
            if value > 0:
                targets[label] = value

    if not targets:
        # real group format: "TARGET - 30 40 50" (all on one line)
        # junk between keyword and numbers can include emoji/arrows.
        kw = r"(?:target|targets|tgt|trg|tg)\s*[^\w0-9]*\s*"
        inline = re.search(
            kw + r"(\d+(?:\.\d+)?)"
            r"(?:\s+(\d+(?:\.\d+)?))?"
            r"(?:\s+(\d+(?:\.\d+)?))?",
            lower,
        )
        if inline:
            values = [v for v in inline.groups() if v]
            for i, value in enumerate(values, start=1):
                targets[f"T{i}"] = float(value)

        # "TARGET > 480/490/500+" or "TARGET: 550/610/700+"
        slash = re.search(
            kw + r"(\d+(?:\.\d+)?)"
            r"(?:\s*/\s*(\d+(?:\.\d+)?))?"
            r"(?:\s*/\s*(\d+(?:\.\d+)?))?"
            r"\s*\+?",
            lower,
        )
        if slash:
            values = [v for v in slash.groups() if v]
            for i, value in enumerate(values, start=1):
                targets[f"T{i}"] = float(value)

    return targets


def _extract_symbol(text):
    # Prefer a clean "NIFTY 23100 PE" reconstruction straight from the
    # message, which works even when emojis/bold distort the leading
    # token (e.g. "**NIFTY*****✅**** | BUY 23100 PE 75" or
    # "🔥 TRADE SETUP — NIFTY 23250 CE 🟢 | BUY:...").
    index_word = re.search(
        r"\b(NIFTY|SENSEX|BANKNIFTY)\b", text, re.IGNORECASE
    )
    strike = re.search(
        r"(\d[\d,]*)\s*(CE|PE|CALL|PUT)\b", text, re.IGNORECASE
    )
    if index_word and strike:
        return _clean_symbol(
            f"{index_word.group(1)} {strike.group(1)} "
            f"{strike.group(2)}"
        )

    tokens = re.split(
        r"\s*(?:buy|sell|long|short|above|below|entry|entered|from|@|"
        r"target\s*1|target\s*2|target\s*3|t\s*1|t\s*2|t\s*3|tg\s*1|tg\s*2|tg\s*3|"
        r"targets|target|stop\s*loss|stoploss|sl)\b",
        text,
        flags=re.IGNORECASE,
    )

    symbol = tokens[0] if tokens else text

    # multiple header lines? keep the LAST line, which is the real
    # ticker line (e.g. "#ZEROHERO #15_SEPT\nNIFTY 23600 CE FROM 20")
    lines = symbol.splitlines()
    if len(lines) > 1:
        symbol = lines[-1]

    # strip trailing junk like "buy-" or emoji markers
    symbol = re.sub(r"[\-\.:]\s*$", "", symbol)
    symbol = re.sub(r"\s{2,}", " ", symbol)
    symbol = re.sub(r"#\S+", "", symbol)

    symbol = _clean_symbol(symbol)

    return symbol if symbol else "UNKNOWN"


# Keywords that mark a message as investment/payment SPAM,
# never a real trading signal.
SPAM_KEYWORDS = [
    "deposit", "withdraw", "withdrawal", "invest", "investment",
    "payment", "credited", "credit", "commission", "profit sharing",
    "account handling", "wa.me", "whatsapp", "paytm", "gpay",
    "phonepe", "phone pay", "bank transfer", "joining", "joined",
    "joining start", "new join", "referral", "upi", "refund",
    "payout", "earn money", "money will", "minimum investment",
    "maximum investment", "25% comission", "comission",
    # paid/advance signals hide the real SL+targets behind payment;
    # never auto-trade those (e.g. "TGT paid | SL paid", "SL PAID")
    "sl paid", "tgt paid", "tg paid", "target paid", "paid call",
    "advance level", "advance call", "paid level", "wait for level",
    "full advance", "level wait",
]


def _has_spam(text):
    lower = text.lower()
    return any(keyword in lower for keyword in SPAM_KEYWORDS)


def parse_signal(text):
    """
    Parse a signal message.

    Example:
        nfty 23150pe buy above 60 target 1 70 target 2 80 target 3 90 stoploss 50

    Returns:
        Signal object or None if nothing parseable.
    """
    if not text:
        return None

    text = text.strip()

    # strip markdown/symbol noise that would break number parsing:
    #   bold "**", rupee/dollar signs "₹2**35" -> "235"
    cleanup_text = text.replace("**", "")
    cleanup_text = re.sub(r"[₹$€£]", "", cleanup_text)
    # thousands separators: "NIFTY 22,850 CE" -> "NIFTY 22850 CE"
    # (only comma between digits, so "70, 80" lists are untouched)
    cleanup_text = re.sub(r",(?=\d{3}\b)", "", cleanup_text)
    lower = cleanup_text.lower()

    num_tokens = re.findall(r"-?\d+\.?\d*", cleanup_text)

    if not num_tokens:
        return None

    direction_word = next(
        (w for w in DIRECTION_WORDS if re.search(rf"\b{w}\b", lower)),
        None,
    )

    direction = DIRECTION_LABEL.get(direction_word, "BUY")

    entry_raw = _extract_entry(lower, num_tokens)

    entry = float(entry_raw) if entry_raw else None

    stoploss = _extract_stoploss(lower)

    targets = _extract_targets(lower)

    if entry is None:
        stoploss_nums = re.findall(
            r"(?:stop\s*loss|stoploss|sl)\s*(-?\d+\.?\d*)",
            lower,
        )

        for tok in num_tokens:
            num = float(tok)

            if stoploss_nums and tok == stoploss_nums[0]:
                continue

            if num > 0:
                entry = num
                break

    if entry is None:
        return None

    symbol = _extract_symbol(cleanup_text)

    # A 'trade signal' must have an entry AND a stoploss AND at least
    # one target.  A signal that has targets but no SL is incomplete
    # (the bot cannot place a stop-order without a stop price) so it is
    # NOT a trade signal.
    # (real group format uses 'FROM 20 TARGET 30 40 50 SL 10' with
    #  no buy/sell word, so a direction word is NOT required)
    has_targets = bool(targets)
    has_sl = stoploss is not None

    # reject investment / payment spam messages outright. Paid
    # teasers hide the real SL/targets behind payment ("TGT paid |
    # SL paid | Wait for level"); a FULLY specified signal (real SL +
    # targets present) that merely also contains such phrasing (e.g.
    # "... SL 180 | Wait for level ...") is still a real tradeable call.
    if has_targets and has_sl and entry is not None:
        if not _has_spam(text):
            is_trade_signal = True
        else:
            is_trade_signal = False
    else:
        is_trade_signal = False

    return Signal(
        raw_text=text,
        symbol=symbol,
        direction=direction,
        entry=entry,
        targets=targets,
        stoploss=stoploss,
        is_trade_signal=is_trade_signal,
    )


if __name__ == "__main__":
    tests = [
        "nfty 23150pe buy above 60 target 1 70 target 2 80 target 3 90 stoploss 50",
        "NIFTY 23150 PE buy @ 60 SL 50 T1 70 T2 80 T3 90",
        "BANKNIFTY 51000 CE sell below 100 stoploss 120 target1 80 target2 60 target3 40",
        "BTCUSDT buy above 50000 sl 48000 t1 52000 t2 54000",
        "ETHUSDT long above 3500 target1 3650 target2 3800 target3 4000 stoploss 3300",
        "reliance buy 2400 sl 2350 target1 2500 target2 2600",
        "NIFTY 24500 PE BUY 150 T1 165 T2 180 T3 200 SL 135",
    ]

    for test in tests:
        print("=" * 70)
        print("RAW:", test)
        sig = parse_signal(test)

        if sig:
            print("SYMBOL    :", sig.symbol)
            print("DIRECTION :", sig.direction)
            print("ENTRY     :", sig.entry)
            print("STOPLOSS  :", sig.stoploss)
            print("TARGETS   :", sig.targets)
        else:
            print("PARSE FAILED")
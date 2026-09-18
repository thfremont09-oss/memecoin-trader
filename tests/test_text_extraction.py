from memecoin_trader.signals.text_extraction import extract_token_addresses

SOLANA_ADDR = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"  # well-formed base58, 44 chars


def test_extracts_a_bare_solana_address():
    text = f"this coin is going to moon, CA: {SOLANA_ADDR} 🚀"
    assert extract_token_addresses(text) == [SOLANA_ADDR]


def test_no_address_and_no_resolver_returns_empty():
    assert extract_token_addresses("just hype, no address here") == []


def test_falls_back_to_cashtag_resolution_when_no_address():
    resolved = {"MOON": "RESOLVED_ADDR_1"}
    result = extract_token_addresses("$MOON is about to explode", resolve_cashtag=resolved.get)
    assert result == ["RESOLVED_ADDR_1"]


def test_address_takes_priority_over_cashtag():
    text = f"$MOON {SOLANA_ADDR}"
    result = extract_token_addresses(text, resolve_cashtag=lambda tag: "SHOULD_NOT_BE_USED")
    assert result == [SOLANA_ADDR]


def test_unresolvable_cashtag_yields_nothing():
    result = extract_token_addresses("$UNKNOWN to the moon", resolve_cashtag=lambda tag: None)
    assert result == []


def test_caps_number_of_cashtags_resolved():
    calls = []

    def resolver(tag):
        calls.append(tag)
        return f"ADDR_{tag}"

    text = "$AA $BB $CC $DD check these out"
    result = extract_token_addresses(text, resolve_cashtag=resolver, max_cashtags=2)
    assert len(result) == 2
    assert len(calls) == 2

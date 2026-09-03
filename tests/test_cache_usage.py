from monkeycode.cache_usage import parse_cache_usage


def test_parses_openai_cached_tokens() -> None:
    usage = {"prompt_tokens_details": {"cached_tokens": 128}, "total_tokens": 200}

    parsed = parse_cache_usage("openai", usage)

    assert parsed.available is True
    assert parsed.cached_tokens == 128
    assert parsed.cache_read_tokens == 128
    assert parsed.cache_creation_tokens is None


def test_parses_anthropic_cache_read_and_creation_tokens() -> None:
    usage = {
        "input_tokens": 1000,
        "cache_creation_input_tokens": 256,
        "cache_read_input_tokens": 512,
    }

    parsed = parse_cache_usage("anthropic", usage)

    assert parsed.available is True
    assert parsed.cache_creation_tokens == 256
    assert parsed.cache_read_tokens == 512
    assert parsed.cached_tokens == 512


def test_marks_cache_usage_unavailable_when_provider_omits_fields() -> None:
    parsed = parse_cache_usage("openai", {"total_tokens": 42})

    assert parsed.available is False
    assert parsed.cached_tokens is None

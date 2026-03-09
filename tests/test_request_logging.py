from app.utils.request_logging import (
    extract_claude_usage,
    extract_openai_usage,
)


def test_extract_openai_usage_supports_cached_prompt_details():
    usage = extract_openai_usage(
        {
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 45,
                "total_tokens": 165,
                "prompt_tokens_details": {
                    "cached_tokens": 32,
                },
            }
        }
    )

    assert usage == {
        "input_tokens": 120,
        "output_tokens": 45,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 32,
        "total_tokens": 165,
    }


def test_extract_claude_usage_supports_cache_token_fields():
    usage = extract_claude_usage(
        {
            "usage": {
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_creation_input_tokens": 64,
                "cache_read_input_tokens": 48,
            }
        }
    )

    assert usage == {
        "input_tokens": 200,
        "output_tokens": 80,
        "cache_creation_tokens": 64,
        "cache_read_tokens": 48,
        "total_tokens": 392,
    }

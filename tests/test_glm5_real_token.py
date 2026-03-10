import pytest

from app.core.config import settings
from app.core.upstream import UpstreamClient
from app.models.schemas import Message, OpenAIRequest
from tests.real_upstream_test_utils import (
    assert_usage_present,
    extract_content,
    install_real_anonymous,
)


@pytest.mark.asyncio
async def test_glm5_with_real_anonymous_request(monkeypatch):
    install_real_anonymous(monkeypatch)

    client = UpstreamClient()
    request = OpenAIRequest(
        model=settings.GLM5_MODEL,
        messages=[
            Message(
                role="user",
                content="请只输出字符串 GLM5_OK，不要输出任何其他内容。",
            )
        ],
        stream=False,
    )

    payload = await client.chat_completion(request)
    content = extract_content(payload)

    assert "GLM5_OK" in content
    assert_usage_present(payload)

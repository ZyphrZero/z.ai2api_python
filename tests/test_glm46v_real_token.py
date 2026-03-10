import pytest

from app.core.config import settings
from app.core.upstream import UpstreamClient
from app.models.schemas import ContentPart, ImageUrl, Message, OpenAIRequest
from tests.real_upstream_test_utils import (
    RED_2X2_PNG_DATA_URL,
    assert_usage_present,
    extract_content,
    install_real_auth,
)


@pytest.mark.asyncio
async def test_glm46v_with_real_auth_token_and_image(monkeypatch):
    install_real_auth(monkeypatch)

    client = UpstreamClient()
    request = OpenAIRequest(
        model=settings.GLM46V_MODEL,
        messages=[
            Message(
                role="user",
                content=[
                    ContentPart(
                        type="text",
                        text="请判断这张图片的主色调。如果它是红色，只输出 RED_OK。",
                    ),
                    ContentPart(
                        type="image_url",
                        image_url=ImageUrl(url=RED_2X2_PNG_DATA_URL),
                    ),
                ],
            )
        ],
        stream=False,
    )

    payload = await client.chat_completion(request)
    content = extract_content(payload)

    assert "RED_OK" in content
    assert_usage_present(payload)

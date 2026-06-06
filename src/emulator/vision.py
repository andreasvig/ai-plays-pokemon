"""Screenshot encoding for the direct-multimodal Player.

The Player LLM sees raw screenshots; this turns a PIL image into the base64
data URL / message-content blocks the model API expects. (The earlier
separate-VLM mode and the on-demand ask_vlm follow-up tool have been removed —
they are no longer used.)
"""

import base64
import io
from typing import Any

from PIL import Image


class VisionPipeline:
    """Encodes screenshots for the direct-multimodal Player LLM."""

    def __init__(self, config: dict[str, Any]):
        # `config` is accepted for call-site compatibility; nothing is
        # configurable now that the separate-VLM mode is gone.
        pass

    def analyze_screenshot(self, image: Image.Image) -> dict:
        """Encode a turn-start screenshot for the LLM."""
        return {"image_base64": self._image_to_base64(image)}

    def format_for_llm(self, analysis: dict) -> list[dict]:
        """Format the screenshot as message content for the LLM.

        Returns a list of content blocks suitable for the OpenAI messages API.
        """
        if "image_base64" in analysis:
            return [
                {"type": "text", "text": "[Game Screen]"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{analysis['image_base64']}",
                        "detail": "high",
                    },
                },
            ]
        return [{"type": "text", "text": "[No game screen available]"}]

    def image_to_data_url(self, image: Image.Image) -> str:
        """Convert a PIL Image to a data: URL suitable for ImageUrl content parts."""
        return f"data:image/png;base64,{self._image_to_base64(image)}"

    def _image_to_base64(self, image: Image.Image) -> str:
        """Convert a PIL Image to base64-encoded PNG string."""
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

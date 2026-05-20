"""Vision pipeline: separate VLM mode and direct multimodal mode.

Handles screenshot analysis at turn start and on-demand VLM follow-up questions.
All model calls go through OpenRouter via the OpenAI SDK.
"""

import base64
import io
from typing import Any, Optional

from openai import OpenAI
from PIL import Image


class VisionPipeline:
    """Configurable vision pipeline with two modes.

    - separate_vlm: VLM analyses screenshot into text, LLM gets text only
    - direct_multimodal: LLM gets the raw screenshot directly
    """

    def __init__(self, config: dict[str, Any]):
        self.vision_mode = config.get("vision_mode", "separate_vlm")
        self.vlm_model = config.get("vlm_model", "")
        self.llm_model = config.get("llm_model", "")
        self.vlm_system_prompt = config.get("vlm_system_prompt", "")
        self.vlm_ask_prompt = config.get("vlm_ask_prompt", "")

        # Accumulated VLM cost (USD) from OpenRouter
        self.total_cost_usd = 0.0

        import os
        api_key = config.get("openrouter_api_key", "") or os.environ.get("OPENROUTER_API_KEY", "")
        self._client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )

    def analyze_screenshot(self, image: Image.Image) -> dict:
        """Analyze a screenshot at turn start.

        In separate_vlm mode: calls VLM, returns {"description": text}
        In direct_multimodal mode: returns {"image_base64": base64_string}
        """
        if self.vision_mode == "separate_vlm":
            description = self._call_vlm(
                image=image,
                system_prompt=self.vlm_system_prompt,
                user_prompt="Describe the current game state.",
            )
            return {"description": description}
        else:
            # Direct multimodal - encode image for the LLM
            b64 = self._image_to_base64(image)
            return {"image_base64": b64}

    def ask_vlm(self, image: Image.Image, question: str) -> str:
        """Ask the VLM a follow-up question about the current screenshot.

        Always uses the VLM model, even in direct_multimodal mode.
        """
        return self._call_vlm(
            image=image,
            system_prompt=self.vlm_ask_prompt,
            user_prompt=question,
        )

    def format_for_llm(self, analysis: dict) -> list[dict]:
        """Format the vision analysis as message content for the LLM.

        Returns a list of content blocks suitable for the OpenAI messages API.
        """
        if "description" in analysis:
            # Separate VLM mode - text only
            return [{"type": "text", "text": f"[Game Screen]\n{analysis['description']}"}]
        elif "image_base64" in analysis:
            # Direct multimodal - include the image
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

    # --- Internal ---

    def _call_vlm(
        self,
        image: Image.Image,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Call the VLM model with an image and prompt."""
        b64 = self._image_to_base64(image)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": user_prompt},
                ],
            },
        ]

        response = self._client.chat.completions.create(
            model=self.vlm_model,
            messages=messages,
            max_tokens=1024,
        )

        # Extract cost from OpenRouter usage extras
        try:
            usage_extra = getattr(response.usage, 'model_extra', None) or {}
            cost = usage_extra.get('cost')
            if cost is not None:
                self.total_cost_usd += float(cost)
        except (AttributeError, TypeError):
            pass

        return response.choices[0].message.content or ""

    def image_to_data_url(self, image: Image.Image) -> str:
        """Convert a PIL Image to a data: URL suitable for ImageUrl content parts."""
        return f"data:image/png;base64,{self._image_to_base64(image)}"

    def _image_to_base64(self, image: Image.Image) -> str:
        """Convert a PIL Image to base64-encoded PNG string."""
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

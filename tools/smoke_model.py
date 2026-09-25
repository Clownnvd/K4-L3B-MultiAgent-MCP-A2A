"""Identity and JSON smoke test; prints no credentials or generated reasoning."""
import asyncio
from pathlib import Path

import httpx2
from dotenv import load_dotenv

from student_agent.model_adapter import ModelSettings, OpenAICompatibleModel


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    settings = ModelSettings.load()
    headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    async with httpx2.AsyncClient(timeout=10, headers=headers) as client:
        response = await client.get(settings.base_url + "/models")
        response.raise_for_status()
        ids = {item["id"] for item in response.json()["data"]}
        if settings.served_name not in ids:
            raise ValueError("Configured model ID is not served by this endpoint")
        result = await OpenAICompatibleModel(settings, client).complete(
            "connectivity_probe", {"instruction": 'Return exactly {"ok":true}.'}
        )
        if result != {"ok": True}:
            raise ValueError("Model failed the JSON smoke test")
    print(f"PASS: {settings.checkpoint}; served identity and JSON response verified")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (httpx2.HTTPError, ValueError, KeyError) as error:
        print(f"FAIL: model smoke test ({type(error).__name__}); service not verified")
        raise SystemExit(1) from None

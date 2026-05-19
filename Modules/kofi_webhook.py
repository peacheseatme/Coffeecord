import json
import logging
from collections.abc import Awaitable, Callable

from aiohttp import web

# Environment variables expected by this module:
# KOFI_VERIFICATION_TOKEN=...  (from Ko-fi webhook settings)
# KOFI_PORT=5000               (optional, defaults in caller)


class KoFiWebhookServer:
    def __init__(
        self,
        *,
        verification_token: str,
        pending_links: dict[str, int],
        on_payload: Callable[[dict], Awaitable[None]],
    ) -> None:
        self.verification_token = verification_token
        self.pending_links = pending_links
        self.on_payload = on_payload
        self._runner: web.AppRunner | None = None

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        try:
            form = await request.post()
            payload_raw = form.get("data")
            if not payload_raw:
                return web.json_response({"ok": False, "error": "Missing data field"}, status=400)
            payload = json.loads(payload_raw)
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"ok": False, "error": "Invalid payload"}, status=400)

        token = str(payload.get("verification_token") or "")
        if token != self.verification_token:
            logging.warning("Rejected Ko-fi webhook: verification token mismatch")
            return web.json_response({"ok": False, "error": "Forbidden"}, status=403)

        await self.on_payload(payload)
        return web.json_response({"ok": True})

    async def start(self, *, host: str = "0.0.0.0", port: int = 5000) -> None:
        app = web.Application()
        app.add_routes([web.post("/kofi-webhook", self._handle_webhook)])

        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, host=host, port=port)
            await site.start()
        except BaseException:
            await runner.cleanup()
            raise
        self._runner = runner

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

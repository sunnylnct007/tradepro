# IBKR Web API — notes pasted by the owner

Collected as the owner pastes sections of IBKR's Web API documentation (22 Nov 2026 onward).
Facts only; what we do with each is decided in the code that uses it.

## Websocket — unsolicited messages

### Bulletins (`topic: "blt"`)
Server-pushed notices about exchange issues, system problems or other urgent trading
information. Not a news feed. Arrives on the same websocket as the order (`sor`) and trade
(`str`) streams, so it joins that work item.

    {"topic": "blt", "args": {"id": "<unique bulletin id>", "message": "<text>"}}

`args.id` is required and unique per bulletin; `args.message` is the content.

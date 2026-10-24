"""Money-movement hold: a tool call that would move money (a write to a payment, payout or transfer API, or a tool
named for it) is held until you type cr: allow, like the egress hold. The lists below are the one place to extend;
~/.cloakroom/config.json adds "money_hosts" (["host/path-prefix"]) and "money_tools" (["tool name fragment"]), and
"money_hold": false turns the hold off."""
from __future__ import annotations

import re

# host -> paths on it that move money (reads such as GET /v1/balance or listing payouts are not held)
HOSTS = {
    r"api\.stripe\.com": r"/v1/(?:payouts|transfers|charges|payment_intents|refunds)\b",
    r"(?:production|development|sandbox)\.plaid\.com": r"/(?:transfer|payment_initiation)/",
    r"api(?:-sandbox)?\.dwolla\.com": r"/transfers\b",
    r"api(?:\.sandbox)?\.(?:wise|transferwise)\.com": r"/transfers\b|/payments\b",
    r"[\w-]+(?:\.[\w-]+)*\.adyen(?:payments)?\.com": r"/[Pp]ayouts?\b|/payments\b",
    r"api-m(?:\.sandbox)?\.paypal\.com": r"/v1/payments/payouts\b|/v2/payments/|/v2/checkout/orders/[^/\s]+/capture\b",
    r"connect\.squareup(?:sandbox)?\.com": r"/v2/(?:payments|refunds)\b",
    r"(?:sandbox\.)?api\.increase\.com": r"/(?:ach|wire|real_time_payments|check)_transfers\b",
    r"app\.moderntreasury\.com": r"/api/payment_orders\b",
    r"api\.column\.com": r"/transfers\b",
}
# payment CLIs that create a money movement
CLI = re.compile(r"\bstripe\s+(?:payouts|transfers|charges|payment_intents|refunds)\s+(?:create|confirm|capture)\b")
# tool names (the part after mcp__server__, in snake_case) with a money verb: create_payout, send_money, pay_invoice
TOOL = re.compile(r"(?:^|_)(?:send|create|initiate|make|execute|submit|schedule|approve|confirm|capture)_(?:[a-z]+_)?"
                  r"(?:payments?|payouts?|transfers?|wires?|ach|refunds?|money|remittances?)(?:_|$)|"
                  r"(?:^|_)(?:pay|send_money|transfer_funds|wire_funds|refund)(?:_|$)")
# a shell command that writes (sends a body or a write method), not a plain GET
WRITE = re.compile(r"(?i)\s(?:-X\s*|--request[\s=]+)['\"]?(?:POST|PUT|PATCH)\b|\s(?:-d|--data(?:-raw|-binary|-urlencode)?|"
                   r"--json|-F|--form|--post-data)(?=[\s=]|$)|\s-Method\s+['\"]?(?:Post|Put|Patch)\b|\s-Body\b|"
                   r"\bhttps?\s+(?:POST|PUT|PATCH)\b")
URL = re.compile(r"(?:https?://)?((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?::\d+)?(/[^\s'\"\\<>]*)?")
WRITE_METHODS = {"POST", "PUT", "PATCH"}


def _endpoint(text: str, cfg: dict) -> str:
    """The first payment endpoint named in the text, as host + matched path ("api.stripe.com/v1/transfers"), or ""."""
    extra = [e.split("/", 1) + [""] for e in cfg.get("money_hosts", []) if e]
    for m in URL.finditer(text):
        host, path = m.group(1).lower(), m.group(2) or ""
        for h, p in HOSTS.items():
            hit = re.fullmatch(h, host) and re.search(p, path)
            if hit:
                return host + hit.group()
        for h, p, *_ in extra:
            if (host == h.lower() or host.endswith("." + h.lower())) and path.startswith("/" + p if p else ""):
                return host + ("/" + p if p else "")
    return ""


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def reason(tool: str, args: dict, shell_tools, cfg: dict) -> str:
    """Why this call would move money ("api.stripe.com/v1/transfers", "tool create_payout"), or "". Names the
    endpoint or tool, never values."""
    if not cfg.get("money_hold", True):
        return ""
    if tool in shell_tools:
        cmd = args.get("command", "") if isinstance(args.get("command"), str) else ""
        cli = CLI.search(cmd)
        if cli:
            return " ".join(cli.group().split())
        where = _endpoint(cmd, cfg)
        return where if where and WRITE.search(" " + cmd) else ""
    name = tool.split("__")[-1]
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).replace("-", "_").lower()
    if TOOL.search(snake) or any(f and f.lower() in snake for f in cfg.get("money_tools", [])):
        return f"tool {name}"
    method = str(args.get("method", "")).upper()
    if method in WRITE_METHODS:
        for s in _strings(args):
            where = _endpoint(s, cfg)
            if where:
                return where
    return ""

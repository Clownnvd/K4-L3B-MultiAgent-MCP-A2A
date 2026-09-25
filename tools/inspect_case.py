"""Inspect official MCP evidence without displaying credentials (development only)."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', default='L3B_CASE_001')
    parser.add_argument('--all-tools', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = dotenv_values(root / '.env')
    headers = {
        'Authorization': 'Bearer ' + config['COMPETITION_TEAM_API_KEY'],
        'Accept': 'application/json, text/event-stream',
        'Content-Type': 'application/json',
    }
    counter = 0

    def rpc(method: str, params: dict) -> dict:
        nonlocal counter
        counter += 1
        body = json.dumps({'jsonrpc': '2.0', 'id': counter, 'method': method, 'params': params}).encode()
        request = urllib.request.Request(config['MCP_ENDPOINT'], data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=25) as response:
            session = response.headers.get('mcp-session-id')
            if session:
                headers['Mcp-Session-Id'] = session
            raw = response.read().decode('utf-8')
            if 'application/json' in response.headers.get('content-type', ''):
                payload = json.loads(raw)
            else:
                payload = next(json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: '))
        if 'error' in payload:
            raise RuntimeError('MCP RPC error code ' + str(payload['error'].get('code')))
        return payload['result']

    initialized = rpc('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'kingpro-inspection', 'version': '0.1.0'}})
    headers['MCP-Protocol-Version'] = initialized['protocolVersion']
    tools = rpc('tools/list', {})['tools']
    cases = json.loads((root / 'inputs' / f'{args.case}.json').read_text(encoding='utf-8'))
    order_id = cases.get('customer_request', {}).get('claimed_order_id') or cases['candidate_order_ids'][0]
    selected = tools if args.all_tools else [t for t in tools if t['name'] in {'get_order', 'get_policy'}]
    results = {}
    for tool in selected:
        parameters = {'case_id': args.case}
        props = tool['inputSchema']['properties']
        if 'order_id' in props:
            parameters['order_id'] = order_id
        if 'policy_version' in props:
            parameters['policy_version'] = cases['policy_version']
        if 'customer_unique_id' in props:
            order_data = results.get('get_order', {}).get('data', {})
            parameters['customer_unique_id'] = order_data.get('customer_unique_id') or cases['customer_unique_id_hint']
        result = rpc('tools/call', {'name': tool['name'], 'arguments': parameters})
        if result.get('isError'):
            message = ' '.join(c.get('text', '') for c in result.get('content', []))
            message = message.replace(config['COMPETITION_TEAM_API_KEY'], '[REDACTED]')
            print(json.dumps({'tool': tool['name'], 'error': message}, ensure_ascii=False))
            break
        evidence = result.get('structuredContent')
        if evidence is None:
            evidence = json.loads(next(c['text'] for c in result.get('content', []) if c.get('type') == 'text'))
        results[tool['name']] = evidence
    target = root / '.local' / 'inspection' / f'{args.case}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()

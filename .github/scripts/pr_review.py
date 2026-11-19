"""Post a PR review and a check run as github-actions[bot].

Requires a workflow token with ``pull-requests: write`` and ``checks: write``.
The review body is taken from ``comment_id`` (an existing issue comment on the
same PR) or from ``body``; both are optional, with a short default.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = 'https://api.github.com'
REPO = os.environ['REPO']
PR = os.environ['PR']
TOKEN = os.environ['GH_TOKEN']
COMMENT_ID = (os.environ.get('COMMENT_ID') or '').strip()
BODY = (os.environ.get('BODY') or '').strip()


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            'Authorization': f'Bearer {TOKEN}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')
        raise SystemExit(f'{method} {path} -> {exc.code}: {detail}') from exc


def review_body() -> str:
    if COMMENT_ID:
        comment = call('GET', f'/repos/{REPO}/issues/comments/{COMMENT_ID}')
        if comment.get('issue_url', '').rstrip('/').split('/')[-1] != PR:
            raise SystemExit(f'comment {COMMENT_ID} does not belong to PR #{PR}')
        body = (comment.get('body') or '').strip()
        if body:
            return body
    return BODY or f'Automated review for #{PR}: no blocking findings recorded.'


def main() -> None:
    pull = call('GET', f'/repos/{REPO}/pulls/{PR}')
    body = review_body()
    review = call(
        'POST',
        f'/repos/{REPO}/pulls/{PR}/reviews',
        {'body': body, 'event': 'COMMENT'},
    )
    print(f'review: {review.get("html_url")}')
    try:
        check = call(
            'POST',
            f'/repos/{REPO}/check-runs',
            {
                'name': 'review/bot',
                'head_sha': pull['head']['sha'],
                'status': 'completed',
                'conclusion': 'neutral',
                'output': {
                    'title': f'Review posted for #{PR}',
                    'summary': body[:60000],
                },
            },
        )
        print(f'check: {check.get("html_url")}')
    except SystemExit as exc:
        print(f'check run skipped: {exc}', file=sys.stderr)


if __name__ == '__main__':
    main()

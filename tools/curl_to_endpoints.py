# -*- coding: utf-8 -*-
"""
Помощник: превращает cURL, скопированный в DevTools (Copy as cURL (bash)),
в заготовку блока для endpoints.json.

Запуск:
    python curl_to_endpoints.py search < curl_search.txt
    python curl_to_endpoints.py document < curl_document.txt

Дальше вручную: заменить конкретные значения на плейсхолдеры
({query}, {doc_id}, {article}, {court}, {date_from}, {date_to}, {page})
и заполнить response_map по фактическому JSON-ответу.
"""
import json
import re
import shlex
import sys
from urllib.parse import urlparse

SKIP_HEADERS = {
    "cookie", "user-agent", "accept-encoding", "connection",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-ch-ua",
    "sec-ch-ua-mobile", "sec-ch-ua-platform", "referer", "origin",
    "content-length", "host",
}


def parse(curl: str) -> dict:
    curl = curl.replace("\\\n", " ").replace("^\n", " ")
    tokens = shlex.split(curl)
    url, method, headers, body = None, None, {}, None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-X", "--request"):
            i += 1
            method = tokens[i].upper()
        elif t in ("-H", "--header"):
            i += 1
            if ":" in tokens[i]:
                k, v = tokens[i].split(":", 1)
                if k.strip().lower() not in SKIP_HEADERS:
                    headers[k.strip()] = v.strip()
        elif t in ("-d", "--data", "--data-raw", "--data-binary"):
            i += 1
            body = tokens[i]
        elif t.startswith("http"):
            url = t
        i += 1

    if method is None:
        method = "POST" if body else "GET"

    parsed = urlparse(url or "")
    path = parsed.path + (("?" + parsed.query) if parsed.query else "")

    body_obj = None
    if body:
        try:
            body_obj = json.loads(body)
        except Exception:
            body_obj = body

    return {
        "_base_url": f"{parsed.scheme}://{parsed.netloc}",
        "method": method,
        "url": path,
        "headers": headers,
        "body": body_obj,
        "response_map": {"__TODO__": "заполнить по фактическому JSON-ответу"},
    }


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "endpoint"
    block = parse(sys.stdin.read())
    base = block.pop("_base_url")
    print(f'// base_url: "{base}"')
    print(json.dumps({name: block}, ensure_ascii=False, indent=2))

    suspicious = [h for h in block["headers"]
                  if any(w in h.lower() for w in ("token", "csrf", "auth", "xsrf", "session"))]
    if suspicious:
        print("\n// ВНИМАНИЕ: заголовки", ", ".join(suspicious),
              "выглядят как сессионные.")
        print("// Скорее всего их проставляет сам браузер — попробуйте УДАЛИТЬ их из конфига.")
        print("// Захардкоженный токен протухнет через несколько часов и запросы начнут падать 403.")

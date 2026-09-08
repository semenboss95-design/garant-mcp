# -*- coding: utf-8 -*-
"""
build_endpoints — сборка endpoints.json из записей record_api.py.

Эвристиками выбирает «главный» запрос каждой фазы, расставляет плейсхолдеры
и выводит response_map по фактическому JSON-ответу. Всё, в чём не уверен,
выносит в отчёт ENDPOINTS_REPORT.md на ручную сверку.

Запуск:  python build_endpoints.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).parent
REC_DIR = HERE / "recordings"
OUT = HERE / "endpoints.json"
REPORT = HERE / "ENDPOINTS_REPORT.md"

LIST_PHASES = {"search", "practice"}
TEXT_PHASES = {"document", "article"}

FIELD_HINTS = {
    "doc_id":      r"^(id|doc_?id|document_?id|guid|nd|code|key)$",
    "title":       r"(name|title|header|caption)",
    "requisites":  r"(requisit|rekvizit|annotation|subtitle)",
    "date":        r"(date|data)$|^(date|data)",
    "status":      r"(status|actual|state)",
    "case_number": r"(case|delo|number|nomer|no)$|case_?num",
    "court":       r"(court|sud|organ)",
    "edition":     r"(edition|redact|redakc|version|versi)",
    "changed_by":  r"(chang|amend|izmen|act)",
    "in_force":    r"(valid|force|deystv|begin|start)",
}

PHASE_URL_HINT = {
    "search":    r"(search|find|poisk)",
    "practice":  r"(search|find|practice|praktik)",
    "document":  r"(document|doc|text)",
    "article":   r"(paragraph|article|item|point|anchor|doc)",
    "revisions": r"(redact|redakc|edition|version|history|card)",
}


# --- обход JSON -------------------------------------------------------------
def walk(obj, path=""):
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            yield from walk(v, f"{path}.{i}" if path else str(i))


def biggest_dict_array(resp):
    best, best_len = None, 0
    for path, val in walk(resp):
        if isinstance(val, list) and val and isinstance(val[0], dict):
            if len(val) > best_len:
                best, best_len = path, len(val)
    return best


def longest_string(resp):
    best, best_len = None, 0
    for path, val in walk(resp):
        if isinstance(val, str) and len(val) > best_len:
            best, best_len = path, len(val)
    return best, best_len


def match_field(keys: list[str], hint: str) -> str | None:
    rx = re.compile(FIELD_HINTS[hint], re.I)
    exact = [k for k in keys if rx.fullmatch(k) or rx.search(k)]
    return exact[0] if exact else None


def find_by_hint(resp, hint: str) -> str | None:
    """Ищет поле по всему ответу (для скалярных полей вроде редакции)."""
    rx = re.compile(FIELD_HINTS[hint], re.I)
    for path, val in walk(resp):
        if isinstance(val, (str, int)) and val not in ("", None):
            leaf = path.split(".")[-1]
            if rx.search(leaf):
                return path
    return None


# --- выбор главного запроса фазы -------------------------------------------
def pick(records: list[dict], phase: str) -> dict | None:
    if not records:
        return None
    rx = re.compile(PHASE_URL_HINT.get(phase, "."), re.I)
    scored = []
    for r in records:
        resp = r.get("response_sample")
        if not isinstance(resp, (dict, list)):
            continue
        score = 0
        if rx.search(r["path"]):
            score += 50
        if phase in LIST_PHASES and biggest_dict_array(resp):
            score += 40
        if phase in TEXT_PHASES:
            _, ln = longest_string(resp)
            score += min(ln // 100, 40)
        if phase == "revisions" and biggest_dict_array(resp):
            score += 30
        score += min(len(json.dumps(resp, ensure_ascii=False)) // 500, 20)
        if r["method"] == "POST" and phase in ("search", "practice"):
            score += 10
        scored.append((score, r))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


# --- плейсхолдеры -----------------------------------------------------------
def parametrize_path(path: str, phase: str) -> tuple[str, list[str]]:
    notes = []
    ids = re.findall(r"/(\d{4,}[\w\-/]*)", path)
    out = path
    if ids:
        out = re.sub(r"/\d{4,}", "/{doc_id}", out, count=1)
        notes.append(f"в пути найден идентификатор — заменён на {{doc_id}} ({ids[0]})")
        if phase == "article":
            out = re.sub(r"/\d+(?=/|$)", "/{article}", out, count=1)
            notes.append("второй числовой сегмент заменён на {article} — ПРОВЕРИТЬ")
    return out, notes


def parametrize_body(body, phase: str) -> tuple[object, list[str]]:
    notes = []
    if not isinstance(body, dict):
        return body, notes
    out = dict(body)
    # самая длинная строка = поисковый запрос
    str_keys = [(k, v) for k, v in out.items() if isinstance(v, str) and len(v) > 2]
    if str_keys and phase in ("search", "practice"):
        k = max(str_keys, key=lambda kv: len(kv[1]))[0]
        notes.append(f'поле "{k}" принято за поисковый запрос → {{query}}')
        out[k] = "{query}"
    for k, v in list(out.items()):
        if re.search(r"page(?!size)|pagenum|offset", k, re.I) and isinstance(v, int):
            out[k] = "{page}"
            notes.append(f'поле "{k}" → {{page}}')
    return out, notes


# --- сборка блока -----------------------------------------------------------
def build_block(rec: dict, phase: str) -> tuple[dict, list[str]]:
    notes = []
    url, n = parametrize_path(rec["path"], phase)
    notes += n
    body, n = parametrize_body(rec.get("body"), phase)
    notes += n

    suspicious = [h for h in rec["headers"]
                  if re.search(r"token|csrf|xsrf|auth|session", h, re.I)]
    headers = {k: v for k, v in rec["headers"].items() if k not in suspicious}
    if suspicious:
        notes.append("УДАЛЕНЫ сессионные заголовки " + ", ".join(suspicious) +
                     " — их проставит браузер; захардкоженные протухают")

    resp = rec["response_sample"]
    rmap: dict[str, str] = {}

    if phase in LIST_PHASES or phase == "revisions":
        arr = biggest_dict_array(resp)
        if arr is None:
            notes.append("НЕ НАЙДЕН массив результатов — заполнить response_map вручную")
        else:
            rmap["items"] = arr
            sample = resp
            for part in arr.split("."):
                sample = sample[int(part)] if isinstance(sample, list) else sample[part]
            keys = list(sample[0].keys()) if sample and isinstance(sample[0], dict) else []
            wanted = (["doc_id", "title", "requisites", "date", "status"]
                      if phase == "search" else
                      ["doc_id", "title", "case_number", "court", "date"]
                      if phase == "practice" else
                      ["edition", "changed_by", "in_force"])
            for w in wanted:
                hit = match_field(keys, w)
                if hit:
                    rmap[w if w != "in_force" else "in_force_from"] = hit
                else:
                    notes.append(f'поле "{w}" не опознано среди {keys} — заполнить вручную')
    else:
        tp, ln = longest_string(resp)
        if tp:
            rmap["text"] = tp
            notes.append(f'текст взят из "{tp}" ({ln} символов) — проверить, что это тело нормы')
        for w, key in (("title", "title"), ("edition", "edition")):
            hit = find_by_hint(resp, w)
            if hit:
                rmap[key] = hit
            else:
                notes.append(f'поле "{key}" не найдено — заполнить вручную')
        if phase == "article":
            hit = find_by_hint(resp, "doc_id")
            if hit:
                rmap["anchor"] = hit

    return {
        "method": rec["method"],
        "url": url,
        "headers": headers,
        "body": body,
        "response_map": rmap,
    }, notes


def main():
    if not REC_DIR.exists():
        raise SystemExit("Нет папки recordings/. Сначала: python record_api.py")

    cfg = {
        "_комментарий": "Сгенерировано build_endpoints.py из recordings/. "
                        "Сверить по ENDPOINTS_REPORT.md перед боевым использованием.",
        "base_url": "https://internet.garant.ru",
        "login_url": "https://internet.garant.ru/",
        "rate_limit_sec": 1.5,
    }
    report = ["# Отчёт сборки endpoints.json\n",
              "Всё, что ниже помечено как требующее проверки, — проверить вручную "
              "по recordings/<фаза>.json перед подачей документов.\n"]

    for phase in ("search", "document", "article", "revisions", "practice"):
        f = REC_DIR / f"{phase}.json"
        report.append(f"\n## {phase}\n")
        if not f.exists():
            report.append("- ✗ записи нет — перезапустить record_api.py\n")
            continue
        recs = json.loads(f.read_text(encoding="utf-8"))
        rec = pick(recs, phase)
        if not rec:
            report.append(f"- ✗ среди {len(recs)} запросов подходящий не найден\n")
            continue
        block, notes = build_block(rec, phase)
        cfg[phase] = block
        report.append(f"- запрос: `{rec['method']} {rec['path']}`\n")
        report.append(f"- кандидатов было: {len(recs)}\n")
        report.append(f"- response_map: `{json.dumps(block['response_map'], ensure_ascii=False)}`\n")
        for n in notes:
            report.append(f"  - ⚠ {n}\n")

    OUT.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text("".join(report), encoding="utf-8")
    print(f"Записано: {OUT.name}")
    print(f"Отчёт:    {REPORT.name}")
    print("\nДальше: прочитать отчёт, затем  python ../sync_norms.py --check")


if __name__ == "__main__":
    main()

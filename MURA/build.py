#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MURA — dataset compiler + QC gate.

  in :  MURA/samples/*.json      (one complete sample object per file)
  out:  MURA/out/MURA_DATASET_V1.jsonl   (one compact JSON object per line)
        MURA/out/registry.json            (per-sample metrics)
        MURA/out/qc_report.txt            (human readable gate log)

GATES
  G1  schema completeness / types
  G2  raw_transcript   600..900 words
  G3  chapter text    1200..1600 words
  G4  every evidence_quote is a VERBATIM substring of raw_transcript
  G5  correction test: superseded (wrong) variant must never reach grounding
      or the chapter; the corrected value must be present in grounding
  G6  hallucination test: proper names inside the chapter must be anchored in
      the transcript / grounding
  G7  date test: every 4-digit year in the chapter must be grounded
  G8  diversity test: unique id / target person / anchor / title / voice, plus
      near-duplicate detection on chapter 8-gram shingles
  G9  language-region coherence
  G10 >= 2 ascending generations in family_graph
"""
import json, os, re, sys, unicodedata
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(ROOT, "samples")
OUT_DIR = os.path.join(ROOT, "out")
OUT_FILE = os.path.join(OUT_DIR, "MURA_DATASET_V1.jsonl")
REGISTRY = os.path.join(OUT_DIR, "registry.json")
QC_LOG = os.path.join(OUT_DIR, "qc_report.txt")

REQUIRED_TOP = ["sample_id", "metadata", "raw_transcript",
                "extracted_grounding", "literary_chapter"]
REQUIRED_META = ["language", "region", "culture", "era", "narrator_persona"]
REQUIRED_GR = ["target_person", "family_graph", "uncertain_claims",
               "timeline", "locations"]
REQUIRED_NODE = ["name", "relation", "period", "facts", "evidence_quote",
                 "confidence"]
REQUIRED_CH = ["title", "material_anchor", "text"]

KZ_GRAPH = set("әғқңөұүһі")
CYR = r"А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі"

# cultural / religious / ideological terms that may legitimately appear in a
# chapter without being a "person" -> downgrade to warning
SOFT_TERMS = {
    "алла", "құдай", "тәңір", "рамазан", "ораза", "құрбан", "айт", "наурыз",
    "соғыс", "отан", "мәскеу", "одақ", "союз", "партия", "комсомол", "октябрь",
    "ленин", "жеңіс", "жеті ата", "шежіре", "құда", "құдағи", "келін", "жеңге",
    "нағашы", "аруақ", "бата", "ас", "той", "садақа", "құран", "мешіт",
    "бог", "господь", "пасха", "рождество", "победа", "цк", "кпсс",
    "god", "christmas", "easter", "victory", "union", "lord",
    "ош", "бішкек", "самарқанд", "бухара", "хиуа", "тошкент", "аллоҳ",
}

# given-name lexicon used to catch a NEW person smuggled into the chapter
NAME_DICT = set("""
айганым кулжан сатыбалды танирберди танірберді кулман балжан сакен жаксылык
гулзия камшат ерболат куляш серикжан рабига айнур данияр турсын абдикерим
зейнеп зылиха асия
айгуль айгерим алихан алмас аманжол аскар асем асель асхат бауыржан бекзат
болат дарига данияр дидар ержан ермек ерлан жамбыл жанар жанна жарас
кенжегул кенже марат мейрам меруерт мухтар нурлан nursultan ораз омирбек
руслан саги сандугаш серик тарлан улан улжан уркия шарипа шолпан ындыра
ержан абай алдияр акжол аружан
виктор сергей андрей николай иван пётр петр алексей дмитрий михаил
юрий валентина мария анна екатерина ольга татьяна галина людмила наталья
наталия светлана надежда зоя клар а тамара лида раиса степан федор кузьма
григорий василий егор илья роман павел геннадий анатолий леонид борис
аркадий семен александр евгений владимир вячеслав артём артем денис игорь
кирилл максим нина гена генадий прокопий захар матвей
асанбек бектур каныбек нургазы сулайман токтогон турдукул жаныбек эрмек
айпери бакыт гулнара дарика жамийла мээрим нурлан саламат уул
ахмадбек бахром дилшод карим нодир рахим сайфи тохта увайса фархода
абдумалик гулчехра мамлакат мумин нигора раззок сайера умар уткир хайит
алишер бекзод бухор джахон зиёда ислом камол латофат махмуд нозим олим
рустам сардор шавкат эркин юлдаш
джон джеймс уильям томас роберт джордж чарльз артур гарольд альберт
уолтер рональд кеннет дэвид ричард джозеф маргарет дороти бетти хелен
элис рут эдит флоренс элизабет мэри сара кэтрин энн джейн эмили грейс
харольд берт стэн лесли сидней регинальд арнольд альфред перси
уэйн дэйл глен ллойд карл дуэйн юджин марвин кертис верн рой ли
джимми билли бобби донни ронни кенни
""".split())

SURNAME_RE = re.compile(
    r"\b([A-ZА-ЯЁӘҒҚҢӨҰҮҺІA-Z][\w]*"
    r"(?:ов|ев|ёв|ский|цкий|ович|евич|вич|ук|юк|ко|енко|ашвили|дзе|"
    r"son|sen|ton|well|ford|ley|field|man|by|worth|shaw|cott|ridge|dale|bury|ham|ing|er)\b)",
    re.UNICODE)


def norm_ws(s):
    return re.sub(r"\s+", " ", s.replace("\u00a0", " ")).strip()


def nfc(s):
    return unicodedata.normalize("NFC", s)


def word_count(s):
    return len(re.findall(r"\S+", s))


def load_samples():
    files = sorted(f for f in os.listdir(SAMPLE_DIR) if f.endswith(".json"))
    out = []
    for f in files:
        with open(os.path.join(SAMPLE_DIR, f), encoding="utf-8") as fh:
            out.append((f, json.load(fh)))
    return out


def grounding_blob(sample):
    g = sample["extracted_grounding"]
    parts = []

    def walk(x):
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, (int, float)):
            parts.append(str(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(g)
    ch = sample["literary_chapter"]
    parts += [ch.get("title", ""), ch.get("material_anchor", "")]
    parts.append(sample["raw_transcript"])
    return nfc(norm_ws(" ".join(parts)).lower())


SENT_END = re.compile(r"[\.\!\?…;:]\s*$")


def is_sentence_start(text, idx):
    """True if position idx begins a sentence (or follows a dash/quote)."""
    pre = text[:idx]
    if not pre.strip():
        return True
    if re.search(r"[\.\!\?…]\s*[»”\"\)\—\-]?\s*$", pre):
        return True
    if re.search(r"[\n]\s*$", pre):
        return True
    return False


_WORD_CACHE = {}


def seen_words(blob):
    """All lowercase word tokens contained in an anchor blob (cached)."""
    key = id(blob)
    got = _WORD_CACHE.get(key)
    if got is None:
        got = set(re.findall(r"[\w\-]{3,}", blob))
        if len(_WORD_CACHE) < 64:
            _WORD_CACHE[key] = got
    return got


def candidate_names(text, blob, bigrams=True, mid_trigger=True):
    """Proper-name candidates in `text` that are not anchored in `blob`."""
    text = nfc(text)
    hits = []
    for m in re.finditer(r"\b([A-ZА-ЯЁӘҒҚҢӨҰҮҺІA-Z][\w]{1,20})\b", text):
        tok = m.group(1)
        low = tok.lower()
        mid_sentence = mid_trigger and not is_sentence_start(text, m.start())
        looks_like_name = low in NAME_DICT or bool(SURNAME_RE.match(tok))
        if not (mid_sentence or looks_like_name):
            continue
        if low in SOFT_TERMS:
            continue
        if low in blob:
            continue
        stem = low
        for suf in ("қызы", "кызы", "ұлы", "улы", "ович", "евич", "овна", "евна"):
            if low.endswith(suf) and len(low) > len(suf) + 2:
                stem = low[: -len(suf)]
                break
        if stem in blob or stem[:5] in blob:
            continue
        # Turkic/Slavic inflection: token starts with an anchored word stem
        inflected = False
        for w in seen_words(blob):
            if len(w) >= 4 and low.startswith(w) and len(low) - len(w) <= 4:
                inflected = True
                break
        if inflected:
            continue
        hits.append((tok, "mid-sentence capital" if mid_sentence else "name-dict"))
    # multi-word capitalised sequences never anchored
    if not bigrams:
        return hits
    for m in re.finditer(r"\b([A-ZА-ЯЁӘҒҚҢӨҰҮҺІA-Z][\w]*\s+[A-ZА-ЯЁӘҒҚҢӨҰҮҺІA-Z][\w]+)\b", text):
        cand = nfc(m.group(1)).lower()
        if cand in blob or any(p in blob for p in cand.split()):
            continue
        hits.append((m.group(1), "bigram"))
    return hits


def shingles(text, k=8):
    toks = re.findall(r"\w+", nfc(text).lower())
    return {" ".join(toks[i:i + k]) for i in range(max(0, len(toks) - k + 1))}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_sample(fname, s, seen):
    errs, warns = [], []
    sid = s.get("sample_id", fname)

    for k in REQUIRED_TOP:
        if k not in s:
            errs.append(f"G1 missing top-level key: {k}")
    for k in REQUIRED_META:
        if k not in s.get("metadata", {}):
            errs.append(f"G1 missing metadata.{k}")
    g = s.get("extracted_grounding", {})
    for k in REQUIRED_GR:
        if k not in g:
            errs.append(f"G1 missing extracted_grounding.{k}")
    ch = s.get("literary_chapter", {})
    for k in REQUIRED_CH:
        if k not in ch:
            errs.append(f"G1 missing literary_chapter.{k}")
    for node in g.get("family_graph", []) or []:
        for k in REQUIRED_NODE:
            if k not in node:
                errs.append(f"G1 family_graph node missing '{k}': {node.get('name','?')}")
        c = node.get("confidence")
        if not isinstance(c, (int, float)) or not (0.0 <= float(c) <= 1.0):
            errs.append(f"G1 confidence out of range: {node.get('name')}={c}")
    if errs:
        return errs, warns

    raw, txt = s["raw_transcript"], ch["text"]
    wc_raw, wc_txt = word_count(raw), word_count(txt)
    if not (600 <= wc_raw <= 900):
        errs.append(f"G2 raw_transcript words={wc_raw} (need 600-900)")
    if not (1200 <= wc_txt <= 1600):
        errs.append(f"G3 chapter words={wc_txt} (need 1200-1600)")

    blob_raw = norm_ws(nfc(raw))
    for coll in ("family_graph", "timeline", "locations"):
        for node in g.get(coll, []) or []:
            q = norm_ws(nfc(node.get("evidence_quote", "")))
            if not q:
                errs.append(f"G4 empty evidence_quote [{coll}:{node.get('name','?')}]")
            elif q not in blob_raw:
                errs.append(f"G4 evidence_quote NOT verbatim [{coll}:{node.get('name','?')}] :: {q[:80]}")

    anchor = grounding_blob(s)
    for c in g.get("resolved_corrections", []) or []:
        wrong, right = str(c.get("wrong", "")), str(c.get("right", ""))
        if wrong and re.search(r"(?<![\w])" + re.escape(nfc(wrong)) + r"(?![\w])", nfc(txt)):
            errs.append(f"G5 chapter contains superseded variant '{wrong}' (correct='{right}')")
        gr_json = nfc(json.dumps({k: v for k, v in g.items() if k != "resolved_corrections"},
                                 ensure_ascii=False).lower())
        if right and right.lower() not in gr_json:
            errs.append(f"G5 corrected value '{right}' absent from grounding body")

    for tok, why in candidate_names(txt, anchor):
        if tok.lower() in SOFT_TERMS:
            warns.append(f"G6 soft term in chapter: {tok}")
        else:
            errs.append(f"G6 unanchored proper name in chapter: '{tok}' ({why})")
    for tok, why in candidate_names(norm_ws(nfc(json.dumps(g, ensure_ascii=False))),
                                    norm_ws(nfc(raw)).lower(), bigrams=False, mid_trigger=False):
        errs.append(f"G6 unanchored proper name in grounding: '{tok}' ({why})")

    years = set(re.findall(r"\b(1[7-9]\d\d|20\d\d)\b", anchor))
    for y in re.findall(r"\b(1[7-9]\d\d|20\d\d)\b", txt):
        if y not in years:
            errs.append(f"G7 year '{y}' in chapter not grounded")

    for label, val in (("sample_id", sid),
                       ("material_anchor", norm_ws(ch["material_anchor"]).lower()),
                       ("target_person", norm_ws(g.get("target_person", "")).lower()),
                       ("chapter_title", norm_ws(ch["title"]).lower()),
                       ("persona", norm_ws(s["metadata"]["narrator_persona"]).lower())):
        if val in seen[label]:
            errs.append(f"G8 duplicate {label}: {val}")
        seen[label].add(val)
    combo = (s["metadata"]["region"], s["metadata"]["era"],
             norm_ws(g.get("target_person", "")))
    if combo in seen["combo"]:
        errs.append("G8 duplicate region+era+target combo")
    seen["combo"].add(combo)
    sh = shingles(txt)
    for prev_id, prev_sh in seen["shingles"]:
        j = jaccard(sh, prev_sh)
        if j > 0.12:
            errs.append(f"G8 near-duplicate chapter vs {prev_id} (jaccard={j:.2f})")
    seen["shingles"].append((sid, sh))

    lang = s["metadata"]["language"]
    cyr = len(re.findall(r"[" + CYR + r"]", raw))
    lat = len(re.findall(r"[A-Za-z]", raw))
    if lang.startswith("kk") and (cyr < 800 or not (set(raw.lower()) & KZ_GRAPH)):
        errs.append("G9 language=kk but transcript lacks Kazakh Cyrillic graphemes")
    if lang.startswith("ru") and cyr < 800:
        errs.append("G9 language=ru but transcript is not Cyrillic-dominant")
    if lang.startswith("en") and lat < 1800:
        errs.append("G9 language=en but transcript is not Latin-dominant")
    if lang.startswith("ky") and not (set(raw.lower()) & set("ңөү")):
        errs.append("G9 language=ky but no Kyrgyz-specific graphemes")
    if lang.startswith("uz") and not re.search(r"[ғқҳў]|o'|g'", raw, re.I):
        errs.append("G9 language=uz but no Uzbek-specific graphemes")

    gens = [n for n in g["family_graph"] if re.search(
        r"(ата|дед|grand|әже|әке|father|mother|шеше|папа|мама|бүбү|апа|бобо|момо|nana|pop|dad|mum|"
        r"отец|мать|бабуш|дедуш|родител|parent|бүбү|эне|ата-эне|bobo|buvi|ota|она|nana|gran|grandm|"
        r"great-|прадед|прабаб)",
        str(n.get("relation", "")).lower())]
    if len(gens) < 2:
        errs.append("G10 fewer than 2 ascending generations in family_graph")

    if not re.search(r"\.\.\.|…", raw):
        warns.append("no ellipsis/pause markers in transcript")
    if len(g.get("uncertain_claims", []) or []) == 0:
        warns.append("no uncertain_claims recorded")
    return errs, warns


def main():
    seen = defaultdict(set)
    seen["shingles"] = []
    samples, report, failed, log = [], [], [], []
    for fname, s in load_samples():
        errs, warns = check_sample(fname, s, seen)
        sid = s.get("sample_id", fname)
        if errs:
            failed.append((sid, fname, errs, warns))
        else:
            samples.append(s)
            report.append({
                "sample_id": sid,
                "country": s["metadata"].get("country", ""),
                "region": s["metadata"]["region"],
                "culture": s["metadata"]["culture"],
                "profession": s["metadata"].get("profession", ""),
                "era": s["metadata"]["era"],
                "language": s["metadata"]["language"],
                "raw_words": word_count(s["raw_transcript"]),
                "chapter_words": word_count(s["literary_chapter"]["text"]),
                "graph_nodes": len(s["extracted_grounding"]["family_graph"]),
                "uncertain": len(s["extracted_grounding"]["uncertain_claims"]),
                "timeline_nodes": len(s["extracted_grounding"]["timeline"]),
                "locations": len(s["extracted_grounding"]["locations"]),
                "corrections": len(s["extracted_grounding"].get("resolved_corrections", [])),
                "material_anchor": s["literary_chapter"]["material_anchor"],
                "target_person": s["extracted_grounding"]["target_person"],
                "warnings": warns,
            })
            if warns:
                log.append(f"~ {sid}: " + "; ".join(warns))
    samples.sort(key=lambda x: x["sample_id"])
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n")
    with open(REGISTRY, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    for sid, fname, errs, warns in failed:
        log.append(f"x {sid} ({fname})")
        log += [f"    - {e}" for e in errs]

    print(f"PASS {len(samples)}  FAIL {len(failed)}  -> {OUT_FILE}")
    if report:
        tr = sum(r["raw_words"] for r in report)
        tc = sum(r["chapter_words"] for r in report)
        print(f"words: transcripts={tr}  chapters={tc}  total={tr+tc}")
        print("avg chapter words: %.0f" % (tc / len(report)))
    if log:
        print("\n".join(log))
    with open(QC_LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

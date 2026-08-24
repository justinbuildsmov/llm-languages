"""Aggregate results.jsonl into analysis.json for the findings site."""

import json
import re
import statistics
import unicodedata
from collections import defaultdict
from itertools import combinations
from pathlib import Path

RESULTS = Path("results/results.jsonl")
OUT = Path("results/analysis.json")

MODEL_ORDER = ["haiku", "sonnet", "opus", "fable", "luna", "terra", "sol"]
MODEL_LABEL = {
    "haiku": "Claude Haiku 4.5", "sonnet": "Claude Sonnet 5",
    "opus": "Claude Opus 5", "fable": "Claude Fable 5",
    "luna": "GPT-5.6 Luna", "terra": "GPT-5.6 Terra", "sol": "GPT-5.6 Sol",
}
LANG_ORDER = ["en", "zh", "hi", "es", "fr", "ar", "bn", "pt", "ru", "ur"]
LANG_LABEL = {
    "en": "English", "zh": "Mandarin", "hi": "Hindi", "es": "Spanish",
    "fr": "French", "ar": "Arabic", "bn": "Bengali", "pt": "Portuguese",
    "ru": "Russian", "ur": "Urdu",
}

# Script ranges for language-fidelity detection
SCRIPT_RANGES = {
    "zh": [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)],
    "hi": [(0x0900, 0x097F)],
    "ar": [(0x0600, 0x06FF), (0x0750, 0x077F)],
    "bn": [(0x0980, 0x09FF)],
    "ru": [(0x0400, 0x04FF)],
    "ur": [(0x0600, 0x06FF), (0x0750, 0x077F)],
}
LATIN_MARKERS = {
    "en": ["breakfast", "you", "the", "with"],
    "es": ["desayun", "con", "una", "pan"],
    "fr": ["petit", "déjeuner", "avec", "pain"],
    "pt": ["café", "manhã", "com", "pão"],
}

# Food concepts: {concept: {"global": [...], per-lang extra surface forms}}
# Patterns are lowercase substrings matched against the lowercased response.
CONCEPTS = {
    "eggs": ["egg", "huevo", "œuf", "oeuf", "ovo", "яйц", "яич", "омлет", "鸡蛋", "雞蛋", "蛋", "अंडे", "अंडा", "انڈ", "بيض", "بیض", "ডিম", "omelet", "omelette", "tortilla francesa"],
    "oatmeal / porridge": ["oat", "porridge", "avena", "avoine", "aveia", "овсян", "каша", "燕麦", "麦片", "ओट्स", "दलिया", "شوفان", "جئ کا دلیہ", "دلیہ", "ওটস", "জই", "muesli", "musli", "granola"],
    "yogurt": ["yogurt", "yoghurt", "yogur", "yaourt", "iogurte", "йогурт", "酸奶", "दही", "زبادي", "لبن", "دہی", "দই", "skyr", "kefir", "кефир", "labneh", "لبنة"],
    "toast / bread": ["toast", "bread", "pan ", "pan,", "pan.", "pain", "pão", "хлеб", "тост", "面包", "麵包", "吐司", "ब्रेड", "टोस्ट", "خبز", "توست", "ٹوسٹ", "روٹی", "রুটি", "টোস্ট", "baguette", "tostada"],
    "fruit": ["fruit", "fruta", "фрукт", "banana", "banane", "plátano", "банан", "berr", "baya", "ягод", "水果", "香蕉", "浆果", "फल", "केला", "فاكهة", "فواكه", "موز", "پھل", "کیلا", "ফল", "কলা", "apple", "manzana", "pomme", "maçã", "яблок", "苹果", "سیب"],
    "avocado": ["avocado", "aguacate", "avocat", "abacate", "авокадо", "牛油果", "鳄梨", "एवोकाडो", "أفوكادو", "ایوکاڈو", "অ্যাভোকাডো"],
    "pancakes / waffles": ["pancake", "waffle", "crêpe", "crepe", "panqueca", "блин", "оладь", "сырник", "煎饼", "松饼", "पैनकेक", "چیلا", "فطائر", "پین کیک", "প্যানকেক", "hotcake"],
    "congee / rice porridge": ["congee", "粥", "稀饭", "خچڑی", "خچری", "কিচুড়ি", "খিচুড়ি", "খিচুরি", "kanji", "arroz caldo"],
    "soy milk / doujiang": ["豆浆", "豆漿", "soy milk", "soymilk", "leche de soja", "豆乳"],
    "baozi / youtiao / jianbing": ["包子", "油条", "煎饼果子", "馒头", "饅頭", "烧麦", "肠粉", "腸粉", "茶叶蛋", "豆腐脑", "煎餃", "煎饺"],
    "paratha / roti / naan": ["paratha", "parantha", "पराठा", "پراٹھ", "পরোটা", "روٹی", "रोटी", "naan", "नान", "puri", "पूरी", "پوری", "chapati", "चपाती"],
    "idli / dosa / poha / upma": ["idli", "dosa", "poha", "upma", "इडली", "डोसा", "पोहा", "उपमा", "sambar", "सांभर", "chilla", "cheela", "चीला"],
    "ful / hummus / falafel": ["فول", "ful medames", "hummus", "حمص", "falafel", "فلافل", "فتة", "زعتر", "جبنة", "مناقيش", "شكشوكة", "shakshuka"],
    "halwa puri / nihari": ["حلوہ پوری", "حلوه پوری", "هلوة", "نہاری", "نان چنے", "چنے", "سری پائے", "پائے", "halwa puri"],
    "kasha / syrniki / tvorog": ["сырник", "творог", "гречк", "гречнев", "запеканк", "манн", "пшённ", "пшенн", "рисовая каша", "овсяная каша", "блины", "оладьи"],
    "pão de queijo / tapioca": ["pão de queijo", "tapioca", "cuscuz", "queijo minas", "requeijão", "açaí", "acai", "vitamina de"],
    "croissant / tartine": ["croissant", "tartine", "brioche", "pain au chocolat", "viennoiserie", "confiture"],
    "cheese": ["cheese", "queso", "fromage", "queijo", "сыр", "奶酪", "芝士", "पनीर", "جبن", "پنیر", "পনির"],
    "coffee / tea": ["coffee", "café", "kaffee", "кофе", "咖啡", "कॉफी", "قهوة", "کافی", "কফি", "tea", "té ", "thé", "chá", "чай", "茶", "चाय", "شاي", "چائے", "চা", "لاچا"],
    "smoothie": ["smoothie", "batido", "licuado", "смузи", "奶昔", "स्मूदी", "سموذي", "اسموتھی", "স্মুদি", "lassi", "लस्सी", "لسی"],
}

# Concepts counted as "culturally local" per language (used for the
# localization index: share of runs mentioning at least one of these).
LOCAL_CONCEPTS = {
    "zh": ["congee / rice porridge", "soy milk / doujiang", "baozi / youtiao / jianbing"],
    "hi": ["paratha / roti / naan", "idli / dosa / poha / upma"],
    "ar": ["ful / hummus / falafel"],
    "ur": ["paratha / roti / naan", "halwa puri / nihari"],
    "bn": ["paratha / roti / naan", "congee / rice porridge"],  # khichuri under congee bucket
    "ru": ["kasha / syrniki / tvorog"],
    "pt": ["pão de queijo / tapioca"],
    "fr": ["croissant / tartine"],
    "es": [],  # left out: no single unambiguous marker set chosen
    "en": [],
}
WESTERN_DEFAULT = ["yogurt", "oatmeal / porridge", "avocado", "smoothie"]


def in_ranges(ch, ranges):
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in ranges)


def detect_fidelity(lang, text):
    """True if the response appears to be written in the target language."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    if lang in SCRIPT_RANGES:
        n = sum(1 for c in letters if in_ranges(c, SCRIPT_RANGES[lang]))
        if lang == "zh":  # CJK text mixes in Latin brand words; lower bar
            return n / len(letters) > 0.3
        return n / len(letters) > 0.5
    low = text.lower()
    return any(m in low for m in LATIN_MARKERS[lang])


def _match(pat, low):
    if pat.isascii() and pat.strip().isalpha():
        return re.search(r"\b" + re.escape(pat.strip()) + r"\b", low) is not None
    return pat in low


def concept_hits(text):
    low = text.lower()
    return {c for c, pats in CONCEPTS.items() if any(_match(p, low) for p in pats)}


def main():
    recs = [json.loads(l) for l in RESULTS.open()]
    recs = [r for r in recs if not r.get("error")]

    by_ml = defaultdict(list)  # (model, lang) -> [rec]
    for r in recs:
        by_ml[(r["model"], r["language"])].append(r)

    # --- lengths & latency ---
    length_grid = {m: {} for m in MODEL_ORDER}
    latency = {}
    for m in MODEL_ORDER:
        lats = [r["latency_s"] for l in LANG_ORDER for r in by_ml[(m, l)]]
        latency[m] = {
            "median": round(statistics.median(lats), 1),
            "p90": round(sorted(lats)[int(len(lats) * 0.9)], 1),
        }
        for l in LANG_ORDER:
            lens = [len(r["text"]) for r in by_ml[(m, l)]]
            length_grid[m][l] = int(statistics.median(lens))

    # --- language fidelity ---
    fidelity = {m: {} for m in MODEL_ORDER}
    fidelity_fails = []
    for (m, l), rs in by_ml.items():
        ok = [detect_fidelity(l, r["text"]) for r in rs]
        fidelity[m][l] = round(100 * sum(ok) / len(ok))
        for r, o in zip(rs, ok):
            if not o:
                fidelity_fails.append({"model": m, "language": l, "run": r["run"],
                                       "snippet": r["text"][:160]})

    # --- concept mentions ---
    # per (lang, concept): % of all runs (across models) mentioning it
    lang_concept = {l: defaultdict(int) for l in LANG_ORDER}
    lang_counts = defaultdict(int)
    # per (model, lang): localization + western-default rates, consistency
    localization = {m: {} for m in MODEL_ORDER}
    western = {m: {} for m in MODEL_ORDER}
    consistency = {m: {} for m in MODEL_ORDER}
    for (m, l), rs in by_ml.items():
        hitsets = [concept_hits(r["text"]) for r in rs]
        for h in hitsets:
            lang_counts[l] += 1
            for c in h:
                lang_concept[l][c] += 1
        loc = LOCAL_CONCEPTS[l]
        localization[m][l] = round(100 * sum(1 for h in hitsets if h & set(loc)) / len(hitsets)) if loc else None
        western[m][l] = round(100 * sum(1 for h in hitsets if h & set(WESTERN_DEFAULT)) / len(hitsets))
        # mean pairwise Jaccard of concept sets across the 10 runs
        pairs = list(combinations(hitsets, 2))
        jac = [len(a & b) / len(a | b) if a | b else 1.0 for a, b in pairs]
        consistency[m][l] = round(statistics.mean(jac), 2)

    concept_grid = {
        l: {c: round(100 * lang_concept[l][c] / lang_counts[l]) for c in CONCEPTS}
        for l in LANG_ORDER
    }

    # concept grid split by provider (Claude models vs GPT models)
    provider_of = {"haiku": "claude", "sonnet": "claude", "opus": "claude",
                   "fable": "claude", "luna": "openai", "terra": "openai", "sol": "openai"}
    pc = {p: {l: defaultdict(int) for l in LANG_ORDER} for p in ("claude", "openai")}
    pn = {p: defaultdict(int) for p in ("claude", "openai")}
    for (m, l), rs in by_ml.items():
        p = provider_of[m]
        for r in rs:
            pn[p][l] += 1
            for c in concept_hits(r["text"]):
                pc[p][l][c] += 1
    concept_grid_provider = {
        p: {l: {c: round(100 * pc[p][l][c] / pn[p][l]) for c in CONCEPTS} for l in LANG_ORDER}
        for p in ("claude", "openai")
    }

    # concept grid per model
    concept_grid_model = {}
    for m in MODEL_ORDER:
        concept_grid_model[m] = {}
        for l in LANG_ORDER:
            rs = by_ml[(m, l)]
            hitsets = [concept_hits(r["text"]) for r in rs]
            concept_grid_model[m][l] = {
                c: round(100 * sum(1 for h in hitsets if c in h) / len(hitsets)) for c in CONCEPTS
            }

    # --- sample answers: median-length answer per model per language ---
    samples = {}
    for m in MODEL_ORDER:
        samples[m] = {}
        for l in LANG_ORDER:
            rs = sorted(by_ml[(m, l)], key=lambda r: len(r["text"]))
            mid = rs[len(rs) // 2]
            samples[m][l] = mid["text"]

    out = {
        "meta": {
            "total_calls": len(recs),
            "models": MODEL_ORDER, "model_label": MODEL_LABEL,
            "languages": LANG_ORDER, "lang_label": LANG_LABEL,
            "runs_per_combo": 10,
        },
        "length_grid": length_grid,
        "latency": latency,
        "fidelity": fidelity,
        "fidelity_fails": fidelity_fails,
        "concept_grid": concept_grid,
        "concept_grid_provider": concept_grid_provider,
        "concept_grid_model": concept_grid_model,
        "localization": localization,
        "western": western,
        "consistency": consistency,
        "samples": samples,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))

    # console digest
    print(f"{len(recs)} records")
    print("\nMedian chars by model (en / zh / avg all):")
    for m in MODEL_ORDER:
        avg = int(statistics.mean(length_grid[m].values()))
        print(f"  {m:8s} en={length_grid[m]['en']:5d} zh={length_grid[m]['zh']:5d} avg={avg:5d}")
    print(f"\nFidelity failures: {len(fidelity_fails)}")
    for f in fidelity_fails[:12]:
        print(f"  {f['model']:8s} {f['language']} run{f['run']}: {f['snippet'][:90]!r}")
    print("\nLocalization % (mentions local food) by model, averaged over langs with local sets:")
    for m in MODEL_ORDER:
        vals = [v for v in localization[m].values() if v is not None]
        wst = statistics.mean(western[m].values())
        print(f"  {m:8s} local={statistics.mean(vals):5.1f}%  western-default={wst:5.1f}%")
    print("\nTop concepts per language:")
    for l in LANG_ORDER:
        top = sorted(concept_grid[l].items(), key=lambda kv: -kv[1])[:5]
        print(f"  {l}: " + ", ".join(f"{c} {v}%" for c, v in top))
    print("\nConsistency (mean Jaccard of food sets across 10 runs), avg per model:")
    for m in MODEL_ORDER:
        print(f"  {m:8s} {statistics.mean(consistency[m].values()):.2f}")


if __name__ == "__main__":
    main()

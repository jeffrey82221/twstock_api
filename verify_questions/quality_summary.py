"""重算 DATA_QUALITY_DIMENSIONS.md §4 的數字與出題分數。

只讀 LEDGER.md、questions/ 與 runs/，不連資料庫：
    python verify_questions/quality_summary.py
"""
import collections
import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DIMS = ["完整", "唯一", "及時", "有效", "準確", "一致", "精確", "可取得", "合理"]
FULL = {d: d + "性" for d in DIMS}
TAG_TO_DIM = {
    "completeness": "完整", "field": "完整", "uniqueness": "唯一",
    "point-in-time": "及時", "timeliness": "及時", "freshness": "及時",
    "validity": "有效", "accuracy": "準確", "internal": "一致",
    "cross-source": "一致", "temporal": "一致", "unit": "一致",
    "consistency": "一致", "precision": "精確", "availability": "可取得",
    "reasonableness": "合理",
}
SEV_BURDEN = {"S1": 8, "S2": 4, "S3": 2, "S4": 1}
# 維度後果係數：README §7.4 的預設嚴重度（S1=4、S2=3、S3=2），有效／合理／精確「看後果」取 2
CONSEQUENCE = {"可取得": 4, "準確": 4, "一致": 4, "唯一": 3, "完整": 3,
               "有效": 3, "合理": 2, "及時": 2, "精確": 1}
REPAIR_FACTOR = 1.5  # 未結負擔 ≥ 平均 × 此值，新題斷言不得落在已有未結 issue 的表上


def section(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def latest_results(card, card_tags):
    if re.search(r"^status:\s*draft\b", card, re.M):
        return {}
    if "## 歷次結果" not in card:
        return {}
    history = card.split("## 歷次結果", 1)[1]
    entries = re.findall(r"^\| \d{4}-\d{2}-\d{2} \|.*$", history, re.M)
    if not entries:
        return {}
    latest = entries[-1]
    run = re.search(r"\]\(\.\./runs/([^/)]+\.md)\)", latest)
    if not run:
        # 沒有詳細 run 時，只採用明確記載全部斷言通過的回歸。
        match = re.search(r"(\d+)\s*[／/]\s*(\d+)", latest)
        if "PASS" in latest and match and int(match.group(1)) == int(match.group(2)) == len(card_tags):
            return dict.fromkeys(card_tags, "PASS")
        return {}

    with open(os.path.join(HERE, "runs", run.group(1)), encoding="utf-8") as file:
        text = file.read()
    results = {}
    for line in section(text, "## 斷言結果", "## ").splitlines():
        cells = [c.strip().strip("*") for c in line.split("|")]
        if len(cells) < 4:
            continue
        match = re.match(r"(C\d+)\b", cells[1])
        if not match or match.group(1) not in card_tags:
            continue
        result = cells[3] if cells[2] in FULL.values() else cells[2]
        if re.match(r"^(PASS|FAIL)(?:\b|（)", result):
            results[match.group(1)] = "PASS" if result.startswith("PASS") else "FAIL"
    return results


def main():
    ledger = open(os.path.join(HERE, "LEDGER.md"), encoding="utf-8").read()

    assertions = collections.Counter()
    tags = collections.Counter()
    ok, bad = collections.Counter(), collections.Counter()
    for f in glob.glob(os.path.join(HERE, "questions", "Q*.md")):
        card = open(f, encoding="utf-8").read()
        card_tags = dict(re.findall(r"^### (C\d+) ([\w-]+)", card, re.M))
        for tag in card_tags.values():
            assertions[TAG_TO_DIM[tag]] += 1
            tags[tag] += 1

        for assertion, result in latest_results(card, card_tags).items():
            dim = TAG_TO_DIM[card_tags[assertion]]
            (ok if result == "PASS" else bad)[dim] += 1

    issues = collections.defaultdict(lambda: [0, 0, 0])  # 已修、未結、未結負擔
    ids = set()
    for r in section(ledger, "## Issues", "## 維度矩陣").splitlines():
        if not r.startswith("| [I"):
            continue
        c = [x.strip() for x in r.split("|")]
        ids.add(c[1])
        closed = c[6].startswith("closed")
        for d in DIMS:
            if d in c[3]:
                issues[d][1 if not closed else 0] += 1
                if not closed:
                    issues[d][2] += SEV_BURDEN[c[2][:2]]

    cover, applicable = (collections.Counter() for _ in range(2))
    rows = 0
    for r in section(ledger, "## 維度矩陣", "## 剖析基線").splitlines():
        if not r.startswith("| `"):
            continue
        rows += 1
        c = [x.strip() for x in r.split("|")]
        for i, d in enumerate(DIMS):
            cell = c[3 + i]
            if cell == "N/A":
                continue
            applicable[d] += 1
            if re.search(r"Q\d+-C", cell):
                cover[d] += 1

    total_yield = len(ids) / sum(assertions.values())
    avg_burden = sum(v[2] for v in issues.values()) / len(DIMS)

    # 獨立性：標籤為 accuracy（對照管線外 golden 值）或 cross-source（兩個來源）的斷言數，
    # 只有準確、一致兩個維度有這類標籤；cross-source 無法從標籤判斷兩邊是否真的獨立（README §7.2）
    indep = {"準確": tags["accuracy"], "一致": tags["cross-source"]}
    print(f"{rows} 張表、{sum(assertions.values())} 條斷言、{len(ids)} 個 issue；平均未結負擔 {avg_burden:.1f}\n")
    print("| 維度 | 斷言數 | 表覆蓋（有斷言／適用） | 覆蓋缺口 | 最近結果 ✓／✗ | Issue（已修／未結） | 未結負擔 | 缺陷產出率 | 後果係數 | **出題分數** | 先修？ |")
    print("| --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | --- |")
    scored = []
    for d in DIMS:
        gap = 1 - cover[d] / applicable[d]
        fixed, opened, burden = issues[d]
        yld = (fixed + opened) / assertions[d]
        score = gap * (1 + yld / total_yield) * CONSEQUENCE[d]
        scored.append((score, d, gap, yld, burden))
    for score, d, gap, yld, burden in sorted(scored, reverse=True):
        fixed, opened, _ = issues[d]
        flag = "是" if burden >= avg_burden * REPAIR_FACTOR else ""
        print(f"| {FULL[d]} | {assertions[d]} | {cover[d]}／{applicable[d]} | {gap:.0%} "
              f"| {ok[d]}／{bad[d]} | {fixed + opened}（{fixed}／{opened}） | {burden} "
              f"| {yld:.0%} | {CONSEQUENCE[d]} | **{score:.2f}** | {flag} |")
    print()
    for d, n in indep.items():
        print(f"獨立性 {FULL[d]}：{n}／{assertions[d]} = {n / assertions[d]:.0%}")
    print(f"一致性各標籤：" + "、".join(f"{t} {tags[t]}" for t in ("internal", "cross-source", "temporal", "unit", "consistency")))


if __name__ == "__main__":
    main()

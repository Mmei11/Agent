"""L1：OTC 药品说明书查询。

边界（见 docs/drug-info-policy.md）：
- 只查用户主动点名的 OTC 药品；
- 只返回说明书里的适应症、禁忌、注意事项、不良反应、相互作用；
- 不给剂量、不做个性化判断、不根据症状推荐药品；
- 处方药直接拒绝，未收录的药品不编造。
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

LABELS_PATH = Path(__file__).with_name("labels.jsonl")

# 处方药种子清单：命中即拒绝说明书查询，交回医生/药师。
# 这不是穷举，缺失的名称会落到“暂未收录”，仍然不会给出建议。
PRESCRIPTION_NAMES = (
    "阿莫西林",
    "头孢",
    "阿奇霉素",
    "左氧氟沙星",
    "甲硝唑",
    "二甲双胍",
    "氨氯地平",
    "缬沙坦",
    "华法林",
    "地塞米松",
    "泼尼松",
    "曲马多",
    "可待因",
    "艾司唑仑",
    "舍曲林",
    "氟西汀",
    "胰岛素",
)

RECOMMENDATION_CUES = (
    "吃什么",
    "用什么",
    "推荐",
    "买什么",
    "开点",
    "开药",
    "吃点",
    "能吃什么",
    "可以吃什么",
)

LABEL_QUERY_CUES = (
    "说明书",
    "禁忌",
    "副作用",
    "不良反应",
    "相互作用",
    "注意事项",
    "怎么吃",
    "用法",
    "用量",
    "剂量",
)

RECOMMENDATION_REFUSAL = (
    "本系统不能根据症状推荐具体药物，也不判断你适合吃哪种药。"
    "你可以告诉我具体药品名称，我帮你查询说明书中的适应症、禁忌和注意事项；"
    "具体用药请咨询药师或医生。"
)

PRESCRIPTION_REFUSAL = (
    "该药属于处方药或需要医生评估，本系统不提供说明书查询和用药建议。"
    "请到医院就诊或咨询药师，按处方使用。"
)

NOT_FOUND = (
    "暂未收录该药品的说明书信息。请以药品包装内的说明书为准，"
    "或咨询药师或医生。"
)

# 展示顺序固定，模型只能选择子集，不能改变顺序。
SECTION_ORDER = (
    "indications",
    "contraindications",
    "precautions",
    "adverse_reactions",
    "interactions",
)
SECTION_LABELS = {
    "indications": "适应症",
    "contraindications": "禁忌",
    "precautions": "注意事项",
    "adverse_reactions": "不良反应",
    "interactions": "药物相互作用",
}


@dataclass(frozen=True)
class DrugLabel:
    id: str
    name: str
    aliases: tuple[str, ...]
    otc: bool
    indications: str
    contraindications: str
    precautions: str
    adverse_reactions: str
    interactions: str
    source_title: str
    source_issuer: str
    source_version: str
    verified: bool


@dataclass(frozen=True)
class DrugReply:
    kind: str  # drug_label / drug_guard
    text: str
    drug_id: str | None = None
    verified: bool = False
    label: DrugLabel | None = None


@lru_cache(maxsize=1)
def load_labels() -> tuple[DrugLabel, ...]:
    if not LABELS_PATH.exists():
        return ()
    labels = []
    for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        if not data.get("otc"):
            continue
        labels.append(
            DrugLabel(
                id=data["id"],
                name=data["name"],
                aliases=tuple(data.get("aliases") or ()),
                otc=True,
                indications=data.get("indications", ""),
                contraindications=data.get("contraindications", ""),
                precautions=data.get("precautions", ""),
                adverse_reactions=data.get("adverse_reactions", ""),
                interactions=data.get("interactions", ""),
                source_title=data.get("source_title", ""),
                source_issuer=data.get("source_issuer", ""),
                source_version=data.get("source_version", ""),
                verified=bool(data.get("verified")),
            )
        )
    return tuple(labels)


def find_label(message: str) -> DrugLabel | None:
    text = message.strip().lower()
    if not text:
        return None
    for label in load_labels():
        if any(alias.lower() in text for alias in label.aliases):
            return label
    return None


def _prescription_hit(message: str) -> bool:
    return any(name in message for name in PRESCRIPTION_NAMES)


def _recommendation_request(message: str) -> bool:
    if "药" not in message:
        return False
    return any(cue in message for cue in RECOMMENDATION_CUES)


def _label_query(message: str) -> bool:
    if not any(cue in message for cue in LABEL_QUERY_CUES):
        return False
    # “怎么吃/用法/用量”必须和“药”同现，避免把普通问题当药品查询。
    if "药" in message:
        return True
    return any(cue in message for cue in ("说明书", "副作用", "不良反应", "相互作用"))


def render_label(
    label: DrugLabel, sections: list[str] | None = None, intro: str = ""
) -> str:
    chosen = [key for key in SECTION_ORDER if sections and key in sections]
    if not chosen:
        chosen = list(SECTION_ORDER)

    verified = "已核实" if label.verified else "未核实"
    lines = []
    if intro:
        lines.append(intro.strip())
        lines.append("")
        lines.append(
            f"以下是「{label.name}」说明书中相关的部分，仅作药品信息参考，"
            "不构成用药建议，也不能判断是否适合你。"
        )
    else:
        lines.append(
            f"以下是「{label.name}」的说明书摘要，仅作药品信息参考，"
            "不构成用药建议，也不能判断是否适合你。"
        )
    lines.append("")

    for key in chosen:
        lines.append(f"{SECTION_LABELS[key]}：{getattr(label, key)}")

    lines.append("")
    lines.append(
        "用法用量请阅读药品说明书或咨询药师；本系统不提供剂量建议。"
        "是否适合你的情况请咨询医生或药师。"
    )
    lines.append("")
    lines.append(
        f"来源：{label.source_title}（{verified}，版本 {label.source_version}）"
    )
    lines.append("本内容由系统整理，仅供健康参考，不能替代专业医生诊断。")
    return "\n".join(lines)


def reply_for_message(message: str) -> DrugReply | None:
    """用户主动提到药品时返回 L1 回复；普通追问返回 None，交给模型。"""
    text = message.strip()
    if not text:
        return None

    # 处方药优先拒绝：即使同时提到 OTC，也不做混合查询。
    if _prescription_hit(text):
        return DrugReply(kind="drug_guard", text=PRESCRIPTION_REFUSAL)

    label = find_label(text)
    if label is not None:
        return DrugReply(
            kind="drug_label",
            text=render_label(label),
            drug_id=label.id,
            verified=label.verified,
            label=label,
        )

    if _recommendation_request(text):
        return DrugReply(kind="drug_guard", text=RECOMMENDATION_REFUSAL)

    if _label_query(text):
        return DrugReply(kind="drug_guard", text=NOT_FOUND)

    return None

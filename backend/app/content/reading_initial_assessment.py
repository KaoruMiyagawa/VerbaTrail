"""Versioned independent, original English reading initial-assessment tasks.

V1 is retained unchanged for in-progress and historical runs. V2 reorders the
same options and uses separate question IDs so its stored answer key cannot
overwrite V1's immutable Question records.
"""
V1_VERSION = "reading-initial-community-garden-v1"
V1_RULE_VERSION = "reading-mcq-evidence-v1"
V1_TASK_ID = "reading-community-garden-v1"
V2_VERSION = "reading-initial-community-garden-v2"
V2_RULE_VERSION = "reading-mcq-evidence-v2"
V2_TASK_ID = "reading-community-garden-v2"
VERSION = V2_VERSION
RULE_VERSION = V2_RULE_VERSION
TASK_ID = V2_TASK_ID
PASSAGE = """When the old railway depot became a community garden, residents expected the hardest work to be digging. They soon discovered that deciding how to share the small site took longer. The committee divided the land into twelve plots, but reserved two beds near the entrance for anyone who wanted to grow herbs without renting a plot.

Water was the next concern. A roof tank collected rain, yet it could not supply the garden through a dry August. Instead of buying a larger tank, volunteers placed mulch around young plants. The layer reduced evaporation, so the existing supply lasted longer. The committee also asked plot holders to water before nine in the morning, when less water would be lost to heat.

In its first season, the garden produced fewer vegetables than the committee had forecast. However, the weekly open mornings attracted families who did not garden. A nearby café began using some of the surplus herbs, while the library hosted short workshops on seed saving. The committee therefore judged success by more than the weight of the harvest: it also counted visits and workshop attendance.

For the next season, the residents rejected a proposal to add more plots. They chose instead to widen the central path, because wheelbarrows had difficulty passing when two people worked side by side. The change would reduce the growing area slightly, but make deliveries and shared work easier."""
V1_QUESTIONS = [
 {"id":"readgarden-q1","prompt":"What were the two entrance beds intended for?","options":["Herbs for visitors without a rented plot","Vegetables for the café","Tools used by the committee","Plants that needed extra water"],"answer":"Herbs for visitors without a rented plot","explanation":"The first paragraph says the two beds were reserved for anyone wanting herbs without renting a plot.","skill":"explicit_detail"},
 {"id":"readgarden-q2","prompt":"Why did volunteers use mulch?","options":["To reduce water loss from the soil","To collect more rain from the roof","To make extra room for plots","To protect the path from wheelbarrows"],"answer":"To reduce water loss from the soil","explanation":"The passage states that mulch reduced evaporation, helping the existing water supply last longer.","skill":"cause_effect"},
 {"id":"readgarden-q3","prompt":"What can be inferred about the committee’s first-season measure of success?","options":["It included community participation as well as produce","It focused only on income from the café","It was based on the number of rented plots","It ignored activities outside the garden"],"answer":"It included community participation as well as produce","explanation":"Although the harvest was below forecast, the committee also counted visits and workshop attendance.","skill":"inference"},
 {"id":"readgarden-q4","prompt":"Why did residents choose to widen the central path?","options":["To make shared work and deliveries easier","To increase the number of plots","To help rain reach the roof tank","To provide space for more workshops"],"answer":"To make shared work and deliveries easier","explanation":"The final paragraph explains wheelbarrows could not pass easily and says widening would make deliveries and shared work easier.","skill":"purpose_and_reason"},
]

# V2 keeps the passage, prompts, and comprehension targets while reordering
# answer options to positions 3, 1, 4, 2. Every position occurs once.
V2_QUESTIONS = [
 {"id":"readgarden-v2-q1","prompt":V1_QUESTIONS[0]["prompt"],"options":["Vegetables for the café","Tools used by the committee","Herbs for visitors without a rented plot","Plants that needed extra water"],"answer":V1_QUESTIONS[0]["answer"],"explanation":V1_QUESTIONS[0]["explanation"],"skill":V1_QUESTIONS[0]["skill"]},
 {"id":"readgarden-v2-q2","prompt":V1_QUESTIONS[1]["prompt"],"options":["To reduce water loss from the soil","To collect more rain from the roof","To make extra room for plots","To protect the path from wheelbarrows"],"answer":V1_QUESTIONS[1]["answer"],"explanation":V1_QUESTIONS[1]["explanation"],"skill":V1_QUESTIONS[1]["skill"]},
 {"id":"readgarden-v2-q3","prompt":V1_QUESTIONS[2]["prompt"],"options":["It focused only on income from the café","It was based on the number of rented plots","It ignored activities outside the garden","It included community participation as well as produce"],"answer":V1_QUESTIONS[2]["answer"],"explanation":V1_QUESTIONS[2]["explanation"],"skill":V1_QUESTIONS[2]["skill"]},
 {"id":"readgarden-v2-q4","prompt":V1_QUESTIONS[3]["prompt"],"options":["To increase the number of plots","To make shared work and deliveries easier","To help rain reach the roof tank","To provide space for more workshops"],"answer":V1_QUESTIONS[3]["answer"],"explanation":V1_QUESTIONS[3]["explanation"],"skill":V1_QUESTIONS[3]["skill"]},
]

LIMITATIONS = ["四道原创客观题只观察本材料中的细节、因果、推断和目的理解；题量不足以估计整体阅读能力或 IELTS Band。旧版 v1 四题正确项均位于第一选项，其作答保留作历史记录，但不作为无偏能力证据。"]
V1_DESIGN_RATIONALE = "V1 四题分别考查细节定位、因果、推断和目的理解，但四题正确项全部位于第一选项，因此固定选择首项即可得到 4/4。该版作答只保留为可追溯历史，不作为无偏能力证据。"
V2_DESIGN_RATIONALE = "四题均为单一最佳答案：直接定位、因果关系、跨句推断、方案目的。文本约 190 词、四段、常见学术生活词汇，无专业背景要求；要求跨句整合但不含复杂句法陷阱。V2 正确项位置依次为第 3、1、4、2 项，每个位置恰好一次；仅改变选项顺序，不改变题干、材料、答案含义或理解目标。作为短初始观察，标记 evidence_insufficient。"
DESIGN_RATIONALE = V2_DESIGN_RATIONALE

VERSIONS = {
    V1_VERSION: {"version":V1_VERSION,"rule_version":V1_RULE_VERSION,"task_id":V1_TASK_ID,"topic_id":"assessment-reading-initial-v1","passage":PASSAGE,"questions":V1_QUESTIONS,"position_biased":True,"design_rationale":V1_DESIGN_RATIONALE},
    V2_VERSION: {"version":V2_VERSION,"rule_version":V2_RULE_VERSION,"task_id":V2_TASK_ID,"topic_id":"assessment-reading-initial-v2","passage":PASSAGE,"questions":V2_QUESTIONS,"position_biased":False,"design_rationale":V2_DESIGN_RATIONALE},
}
QUESTIONS = V2_QUESTIONS

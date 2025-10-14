import json

# ---------- 加载模型 JSON ----------
with open("parsed_model_core.json", "r", encoding="utf-8") as f:
    model = json.load(f)

# ---------- 初始化统计 ----------
stats = {
    "Mode": 0,
    "Module": 0,
    "BehavioralStateMachine": 0,
    "ConstraintStateMachine": 0,
    "Variables": {"MV": 0, "LV": 0, "SV": 0, "CV": 0, "unknown": 0},
    "Events": {"atomic": 0, "composite": 0, "undefined": 0}
}

# ---------- 工具函数 ----------
def get_variable_type(attr):
    # 优先使用 fms_annotations
    for ann in attr.get("fms_annotations", []):
        stereo_name = ann.get("stereotype_name", "").lower()
        if "monitored" in stereo_name:
            return "MV"
        elif "logic" in stereo_name:
            return "LV"
        elif "state" in stereo_name:
            return "SV"
        elif "controlled" in stereo_name:
            return "CV"
    # 如果 annotations 没有，则尝试从 stereotypes 判断
    for st in attr.get("stereotypes", []):
        st_lower = st.lower()
        if "monitored" in st_lower:
            return "MV"
        elif "logic" in st_lower:
            return "LV"
        elif "state" in st_lower:
            return "SV"
        elif "controlled" in st_lower:
            return "CV"
    return "unknown"

def process_class(c):
    # 统计 Mode / Module
    stereo_list = [s.lower() for s in c.get("stereotypes",[])]
    if "mode" in stereo_list:
        stats["Mode"] += 1
    elif "modular" in stereo_list or "module" in stereo_list:
        stats["Module"] += 1

    # 统计变量
    for attr in c.get("ownedAttributes", []):
        vtype = get_variable_type(attr)
        stats["Variables"][vtype] += 1

    # 统计状态机
    for sm in c.get("ownedBehaviors", []):
        sm_type = sm.get("stereotypes", [])
        sm_type_lower = [s.lower() for s in sm_type]
        if "behavioral statemachine" in sm_type_lower:
            stats["BehavioralStateMachine"] += 1
        elif "constraint statemachine" in sm_type_lower:
            stats["ConstraintStateMachine"] += 1

def process_event(signal):
    stereo_list = [s.lower() for s in signal.get("stereotypes",[])]
    # 支持 automic/atomic
    if any(s in ["atomic event", "automic event"] for s in stereo_list):
        stats["Events"]["atomic"] += 1
    elif "composite event" in stereo_list:
        stats["Events"]["composite"] += 1
    else:
        stats["Events"]["undefined"] += 1

def traverse_package(pkg):
    for child in pkg.get("children", []):
        ctype = child.get("type", "")
        if ctype == "uml:Class":
            process_class(child)
        elif ctype == "uml:Signal":
            process_event(child)
        elif ctype == "uml:Package":
            traverse_package(child)

# ---------- 开始遍历 ----------
for pkg in model.get("packages", []):
    traverse_package(pkg)

# ---------- 输出结果 ----------
print("Model Statistics:")
print(f"Mode blocks: {stats['Mode']}")
print(f"Module blocks: {stats['Module']}")
print(f"Behavioral State Machines: {stats['BehavioralStateMachine']}")
print(f"Constraint State Machines: {stats['ConstraintStateMachine']}")
print("Variables:")
for k, v in stats["Variables"].items():
    print(f"  {k}: {v}")
print("Events:")
for k, v in stats["Events"].items():
    print(f"  {k}: {v}")

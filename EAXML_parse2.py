import json
import re
import os

# -------------------------
# 工具函数
# -------------------------

def clean_id(ea_id: str) -> str:
    if not ea_id:
        return ea_id
    return re.sub(r'^EAID_', '', ea_id)

def clean_datatype(dt: str) -> str:
    if not dt:
        return dt
    return dt.replace('EAJava_', '')

def clean_fms_annotations(fms_list):
    cleaned = []
    for ann in fms_list:
        props = {}
        for k, v in ann.get('props', {}).items():
            # 保留 substate/subevent/guard 信息
            if k.startswith('map_to_') or k in ['value', 'guard_truthTable', 'substate', 'subevent', 'composite_event']:
                if k == 'guard_truthTable' and v:
                    v = os.path.basename(v)
                props[k] = v
        if props:
            cleaned.append({'props': props})
    return cleaned

# -------------------------
# EAID → name 替换函数
# -------------------------

def replace_eaid_with_name(value, eaid_map):
    """
    将 EAID 或 EAID 列表替换为 name。
    支持逗号分隔字符串。
    """
    if not value:
        return value

    if isinstance(value, str):
        parts = [v.strip() for v in value.split(",")]
        replaced = []
        for v in parts:
            if v in eaid_map:
                name = eaid_map[v].get("name")
                replaced.append(name if name else v)
            else:
                replaced.append(v)
        return ",".join(replaced)
    elif isinstance(value, list):
        return [eaid_map.get(v, {}).get("name", v) for v in value]
    else:
        return value

# -------------------------
# 核心递归函数
# -------------------------

def simplify_element(el, eaid_map):
    # -------------------------
    # 1️⃣ 处理元素自身的 fms_annotations
    # -------------------------
    if 'fms_annotations' in el:
        for ann in el['fms_annotations']:
            for k, v in ann.get('props', {}).items():
                if k in ['substate', 'subevent', 'composite_event']:
                    ann['props'][k] = replace_eaid_with_name(v, eaid_map)

    # -------------------------
    # 2️⃣ ownedAttributes
    # -------------------------
    for attr in el.get('ownedAttributes', []):
        if 'fms_annotations' in attr:
            for ann in attr['fms_annotations']:
                for k, v in ann.get('props', {}).items():
                    if k in ['substate', 'subevent', 'composite_event']:
                        ann['props'][k] = replace_eaid_with_name(v, eaid_map)

    # -------------------------
    # 3️⃣ ownedBehaviors -> regions -> states / transitions
    # -------------------------
    for sm in el.get('ownedBehaviors', []):
        for reg in sm.get('regions', []):
            for st in reg.get('states', []):
                if 'fms_annotations' in st:
                    for ann in st['fms_annotations']:
                        for k, v in ann.get('props', {}).items():
                            if k in ['substate', 'subevent', 'composite_event']:
                                ann['props'][k] = replace_eaid_with_name(v, eaid_map)

            for tr in reg.get('transitions', []):
                if 'fms_annotations' in tr:
                    for ann in tr['fms_annotations']:
                        for k, v in ann.get('props', {}).items():
                            if k in ['substate', 'subevent', 'composite_event']:
                                ann['props'][k] = replace_eaid_with_name(v, eaid_map)

    # -------------------------
    # 4️⃣ children 递归
    # -------------------------
    for child in el.get('children', []):
        simplify_element(child, eaid_map)



# -------------------------
# 主程序
# -------------------------

if __name__ == "__main__":
    input_file = "parsed_model_simplified.json"  # 上一步的输出
    output_file = "parsed_model_core.json"
    eaid_map_file = "eaid_stereotype_map.json"   # 来自 EAXML_parse1 的结果

    # 读取 EAID 映射表
    with open(eaid_map_file, 'r', encoding='utf-8') as fh:
        eaid_map = json.load(fh)

    # 若是 EAXML_parse1 旧格式，直接使用；若是新版嵌套结构，则转换
    if isinstance(list(eaid_map.values())[0], dict) and "name" in list(eaid_map.values())[0]:
        # 新版映射：EAID → {"name": "...", "stereotypes": [...]}
        pass
    elif "stereotype_map" in eaid_map and "name_map" in eaid_map:
        combined = {}
        for eid, name in eaid_map["name_map"].items():
            combined[eid] = {"name": name, "stereotypes": eaid_map["stereotype_map"].get(eid, [])}
        eaid_map = combined

    # 读取模型
    with open(input_file, 'r', encoding='utf-8') as fh:
        model = json.load(fh)

    # 简化每个 package
    for pkg in model.get('packages', []):
        simplify_element(pkg, eaid_map)

    # 输出结果
    with open(output_file, 'w', encoding='utf-8') as fh:
        json.dump(model, fh, indent=2, ensure_ascii=False)

    print(f"✅ 核心 JSON 已生成: {output_file}")

import xml.etree.ElementTree as ET
import json
from collections import defaultdict

# -------------------------
# 工具函数
# -------------------------
def localname(tag: str) -> str:
    """去掉命名空间前缀，返回本地名"""
    if tag is None:
        return None
    return tag.split('}')[-1] if '}' in tag else tag

def get_attr(el: ET.Element, name: str):
    """按本地属性名返回属性值（支持 xmi:id / xmi:type 等命名空间属性）"""
    if el is None:
        return None
    # 直接键匹配优先（当调用者传入 'xmi:type' 之类时）
    if name in el.attrib:
        return el.attrib[name]
    # 再按本地名匹配
    for k, v in el.attrib.items():
        if localname(k) == name:
            return v
    return None

# -------------------------
# 构建 EAID -> stereotype 映射 & FMS 注释映射
# -------------------------
def build_eaid_maps(root):
    """
    返回:
      - eaid_combined_map: { EAID: {"stereotypes": [...], "name": str} }
      - fms_annotations: dict EAID -> [annotation,...]
    """
    eaid_stereotype_map = defaultdict(list)
    fms_annotations = defaultdict(list)
    id_name_map = {}

    for el in root.iter():
        el_id = get_attr(el, "id")
        el_name = get_attr(el, "name")
        if el_id and el_name:
            id_name_map[el_id] = el_name

        # stereotype base_*
        for raw_k, raw_v in el.attrib.items():
            k_local = localname(raw_k)
            if k_local.startswith("base_") and raw_v:
                base_id = raw_v
                props = {localname(a_k): a_v for a_k, a_v in el.attrib.items()}
                stereo_name = el.attrib.get("__EAStereoName") or localname(el.tag)
                if stereo_name not in eaid_stereotype_map[base_id]:
                    eaid_stereotype_map[base_id].append(stereo_name)
                fms_annotations[base_id].append({
                    "stereotype_element": localname(el.tag),
                    "stereotype_name": stereo_name,
                    "props": props
                })

    # ✅ 组合为统一字典
    eaid_combined_map = {}
    all_ids = set(eaid_stereotype_map.keys()) | set(id_name_map.keys())
    for eid in all_ids:
        eaid_combined_map[eid] = {
            "stereotypes": eaid_stereotype_map.get(eid, []),
            "name": id_name_map.get(eid)
        }

    return eaid_combined_map, dict(fms_annotations)


# -------------------------
# 解析器（Class/Property/StateMachine 等）
# -------------------------
def parse_property(prop, eaid_map, fms_annotations):
    xmi_id = get_attr(prop, "id")
    default_el = prop.find("./{*}defaultValue")
    default_val = get_attr(default_el, "value") if default_el is not None else None

    data = {
        "id": xmi_id,
        "name": get_attr(prop, "name"),
        "type": get_attr(prop, "type"),
        "visibility": get_attr(prop, "visibility"),
        "lower": get_attr(prop.find("./{*}lowerValue"), "value"),
        "upper": get_attr(prop.find("./{*}upperValue"), "value"),
        "datatype": get_attr(prop.find("./{*}type"), "idref"),
        "defaultValue": default_val,
        "stereotypes": eaid_map.get(xmi_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(xmi_id, [])
    }
    return data


def parse_state_machine(sm, eaid_map, fms_annotations):
    sm_id = get_attr(sm, "id")
    data = {
        "id": sm_id,
        "name": get_attr(sm, "name"),
        "type": get_attr(sm, "type"),
        "stereotypes": eaid_map.get(sm_id, {}).get("stereotypes", []),
        "regions": []
    }

    # 触发器定义
    trigger_defs = {}
    for trig in sm.findall("./{*}nestedClassifier"):
        if get_attr(trig, "type") != "uml:Trigger":
            continue
        trig_id = get_attr(trig, "id")
        trig_name = get_attr(trig, "name")
        event = trig.find("./{*}event")
        event_id = get_attr(event, "id") if event is not None else None
        signal_el = event.find("./{*}signal") if event is not None else None
        signal_ref = get_attr(signal_el, "idref") if signal_el is not None else None
        signal_name = get_attr(signal_el, "name") if signal_el is not None else None
        trigger_defs[trig_id] = {
            "id": trig_id,
            "name": trig_name,
            "event": event_id,
            "signal": signal_ref,
            "signal_name": signal_name,
            "fms_annotations": fms_annotations.get(trig_id, [])
        }

    for region in sm.findall("./{*}region"):
        reg = {"states": [], "transitions": []}
        for st in region.findall("./{*}subvertex"):
            st_id = get_attr(st, "id")
            reg["states"].append({
                "id": st_id,
                "name": get_attr(st, "name"),
                "type": get_attr(st, "type"),
                "kind": get_attr(st, "kind"),
                "incoming": [get_attr(i, "idref") for i in st.findall("./{*}incoming")],
                "outgoing": [get_attr(o, "idref") for o in st.findall("./{*}outgoing")],
                "stereotypes": eaid_map.get(st_id, {}).get("stereotypes", []),
                "fms_annotations": fms_annotations.get(st_id, [])
            })

        for tr in region.findall("./{*}transition"):
            tr_id = get_attr(tr, "id")
            guard_el = tr.find("./{*}guard")
            guard_body = None
            if guard_el is not None:
                spec = guard_el.find("./{*}specification")
                guard_body = get_attr(spec, "body") if spec is not None else None

            trans_info = {
                "id": tr_id,
                "source": get_attr(tr, "source"),
                "target": get_attr(tr, "target"),
                "kind": get_attr(tr, "kind"),
                "stereotypes": eaid_map.get(tr_id, {}).get("stereotypes", []),
                "fms_annotations": fms_annotations.get(tr_id, []),
                "guard": guard_body,
                "triggers": []
            }

            for trig in tr.findall("./{*}trigger"):
                trig_ref = get_attr(trig, "idref") or get_attr(trig, "id")

                # 尝试从当前状态机的 trigger_defs 中查找
                trig_info = trigger_defs.get(trig_ref)

                # 🩹 fallback：如果没找到，就全局查找
                if trig_info is None:
                    trig_node = None
                    for el in root.iter():
                        if get_attr(el, "id") == trig_ref and "Trigger" in (get_attr(el, "type") or ""):
                            trig_node = el
                            break

                    if trig_node is not None and "Trigger" in get_attr(trig_node, "type"):
                        event_el = trig_node.find("./{*}event")
                        event_id = get_attr(event_el, "id") if event_el is not None else None
                        signal_el = event_el.find("./{*}signal") if event_el is not None else None
                        signal_ref = get_attr(signal_el, "idref") if signal_el is not None else None
                        signal_name = get_attr(signal_el, "name") if signal_el is not None else None

                        trig_info = {
                            "id": trig_ref,
                            "name": get_attr(trig_node, "name"),
                            "event": event_id,
                            "signal": signal_ref,
                            "signal_name": signal_name,
                            "fms_annotations": fms_annotations.get(trig_ref, [])
                        }

                # 🩹 fallback 2：EA Deep Copy 内嵌 trigger（无 nestedClassifier）
                if trig_info is None:
                    event_el = trig.find("./{*}event")
                    event_id = get_attr(event_el, "id") if event_el is not None else None
                    signal_el = event_el.find("./{*}signal") if event_el is not None else None
                    signal_ref = get_attr(signal_el, "idref") if signal_el is not None else None
                    signal_name = get_attr(signal_el, "name") if signal_el is not None else None

                    trig_info = {
                        "id": trig_ref,
                        "name": get_attr(trig, "name"),
                        "event": event_id,
                        "signal": signal_ref,
                        "signal_name": signal_name,
                        "fms_annotations": fms_annotations.get(trig_ref, [])
                    }

                # 最后保证一定有一个 trigger 记录
                if trig_info is None:
                    trig_info = {
                        "id": trig_ref,
                        "fms_annotations": fms_annotations.get(trig_ref, [])
                    }

                trans_info["triggers"].append(trig_info)

            reg["transitions"].append(trans_info)
        data["regions"].append(reg)
    return data




def parse_class(cls, eaid_map, fms_annotations):
    cls_id = get_attr(cls, "id")
    data = {
        "id": cls_id,
        "name": get_attr(cls, "name"),
        "type": get_attr(cls, "type"),
        "stereotypes": eaid_map.get(cls_id, {}).get("stereotypes", []),
        "ownedAttributes": [],
        "ownedBehaviors": []
    }
    for child in cls:
        tag = localname(child.tag)
        if tag == "ownedAttribute":
            data["ownedAttributes"].append(parse_property(child, eaid_map, fms_annotations))
        elif tag == "ownedBehavior":
            data["ownedBehaviors"].append(parse_state_machine(child, eaid_map, fms_annotations))
    return data

def parse_package(pkg, eaid_map, fms_annotations):
    pkg_id = get_attr(pkg, "id")
    data = {
        "id": pkg_id,
        "name": get_attr(pkg, "name"),
        "type": get_attr(pkg, "type"),
        "stereotypes": eaid_map.get(pkg_id, {}).get("stereotypes", []),
        "children": []
    }
    for child in pkg.findall("./{*}packagedElement"):
        child_type = get_attr(child, "type")
        if child_type == "uml:Class":
            data["children"].append(parse_class(child, eaid_map, fms_annotations))
        elif child_type == "uml:Package":
            data["children"].append(parse_package(child, eaid_map, fms_annotations))
        else:
            ch_id = get_attr(child, "id")
            data["children"].append({
                "id": ch_id,
                "name": get_attr(child, "name"),
                "type": child_type,
                "stereotypes": eaid_map.get(ch_id, {}).get("stereotypes", []),
                "fms_annotations": fms_annotations.get(ch_id, [])
            })
    return data

# -------------------------
# 通用后处理工具：规范化所有 fms_annotations 中的 map_to_* 字段
# -------------------------
def normalize_annotation_maps(ann: dict, id_name_map: dict):
    """
    将 annotation 的 props 中所有 map_to_* (以及类似的) EAID 替换为对应 name，
    并补充 _<map_key>_name 字段，保留原始替换（如果没有映射则保留原值）。
    ann 是单个 annotation dict，直接修改其 props。
    """
    if not isinstance(ann, dict):
        return
    props = ann.get("props", {})
    if not isinstance(props, dict):
        return
    # 遍历 props 的键的副本，避免迭代中修改问题
    for k in list(props.keys()):
        v = props.get(k)
        # 只对字符串型且看起来像 EAID 的值做替换（或任何在 id_name_map 中的值）
        if isinstance(v, str):
            # 规则：任何以 map_to_ 开头的键都尝试替换；另外也支持通用映射（如果需要可扩展）
            if k.startswith("map_to_") or k in ("map_to_LV", "map_to_SV", "map_to_MV", "map_to_CV"):
                mapped = id_name_map.get(v, v)
                props[k] = mapped
                # 添加对应名称字段，前面加 '_' 以示区别
                props[f"_{k}_name"] = id_name_map.get(v, v)
            else:
                # 有时候其他键也可能存 EAID（例如 triggerVariable 等），我们也可以尝试智能替换：
                # 如果值正好是已知的 EAID，就把它替换为 name 并同时保留原键名（不强制）
                if v in id_name_map:
                    props[k] = id_name_map[v]
                    props[f"_{k}_name"] = id_name_map[v]
    # 写回
    ann["props"] = props

# -------------------------
# 后处理：EAID -> name 映射 & 精简字段（增强版）
# -------------------------
def simplify_element(el, id_name_map):
    """
    递归处理 element:
      - 保留 id/name/type/stereotypes/fms_annotations
      - 对 fms_annotations 中所有 map_to_* 进行替换并补充 _map_to_*_name
      - 对 ownedAttributes/ownedBehaviors/regions/children 递归
      - 替换 transition source/target、state incoming/outgoing 从 EAID -> name（如果有映射）
      - 删除不必要字段：visibility/lower/upper/kind/value/kind 等
      - 处理 transitions 中的 triggers（替换 event EAID -> name，处理注释）
    """
    if el is None:
        return

    # 先处理当前元素上可能存在的 fms_annotations
    for ann in el.get("fms_annotations", []) or []:
        normalize_annotation_maps(ann, id_name_map)

    # 1. ownedAttributes
    if "ownedAttributes" in el:
        for attr in el["ownedAttributes"]:
            # 处理 attr 自身的 fms_annotations
            for ann in attr.get("fms_annotations", []) or []:
                normalize_annotation_maps(ann, id_name_map)
            # 删除不必要字段
            for k in ["visibility", "lower", "upper"]:
                attr.pop(k, None)

    # 2. ownedBehaviors -> regions -> states/transitions
    if "ownedBehaviors" in el:
        for sm in el["ownedBehaviors"]:
            # sm 级别的注释也处理
            for ann in sm.get("fms_annotations", []) or []:
                normalize_annotation_maps(ann, id_name_map)

            for reg in sm.get("regions", []):
                # states
                for st in reg.get("states", []):
                    # 处理 state 的注释
                    for ann in st.get("fms_annotations", []) or []:
                        normalize_annotation_maps(ann, id_name_map)
                    # 删除不必要字段
                    for k in ["kind", "value"]:
                        st.pop(k, None)
                    # incoming/outgoing 替换 EAID -> name
                    st["incoming"] = [id_name_map.get(i, i) for i in st.get("incoming", []) if i]
                    st["outgoing"] = [id_name_map.get(o, o) for o in st.get("outgoing", []) if o]

                # transitions
                for tr in reg.get("transitions", []):
                    # transition 注释处理
                    for ann in tr.get("fms_annotations", []) or []:
                        normalize_annotation_maps(ann, id_name_map)

                    # source/target 替换 EAID -> name
                    tr["source"] = id_name_map.get(tr.get("source"), tr.get("source"))
                    tr["target"] = id_name_map.get(tr.get("target"), tr.get("target"))
                    tr.pop("kind", None)

                    # 保留 guard（已经在 parse 阶段提取）
                    if tr.get("guard") is not None:
                        # 如果 guard 是从 spec 的 body 中抽取出来的字符串，直接保留
                        tr["guard"] = tr["guard"]

                    # triggers 处理
                    for trig in tr.get("triggers", []) or []:
                        # 注释处理
                        for ann in trig.get("fms_annotations", []) or []:
                            normalize_annotation_maps(ann, id_name_map)
                        # event id 替换为名称
                        if trig.get("event"):
                            trig["event"] = id_name_map.get(trig["event"], trig["event"])
                            trig["event_name"] = id_name_map.get(trig["event"], trig["event"])

    # 3. children 递归（class / package / primitive 等）
    if "children" in el:
        for child in el["children"]:
            simplify_element(child, id_name_map)


# -------------------------
# 主程序
# -------------------------
if __name__ == "__main__":
    xml_file = "example3.xml"   # <-- 替换为你的文件名
    tree = ET.parse(xml_file)
    root = tree.getroot()

    # 1) 构建映射与注释表
    eaid_combined_map, fms_annotations = build_eaid_maps(root)


    with open("eaid_stereotype_map.json", "w", encoding="utf-8") as fh:
        json.dump(eaid_combined_map, fh, indent=2, ensure_ascii=False)

    # 保存 fms_annotations（调试用）
    with open("fms_annotations.json", "w", encoding="utf-8") as fh:
        json.dump(fms_annotations, fh, indent=2, ensure_ascii=False)

    # 2) 解析包/类/属性/状态机 等，并把 stereotype/fms 注释关联上去
    packages = []
    for el in root.iter():
        if localname(el.tag) == "packagedElement" and get_attr(el, "type") == "uml:Package":
            packages.append(parse_package(el, eaid_combined_map, fms_annotations))

    # 保存最终解析结果（原始）
    with open("parsed_model.json", "w", encoding="utf-8") as fh:
        json.dump({"packages": packages}, fh, indent=2, ensure_ascii=False)

    print(f"✅ 已生成 eaid_stereotype_map.json，共 {len(eaid_combined_map)} 条记录")
    print(f"已生成 fms_annotations.json（供调试）")
    print(f"已解析 {len(packages)} 个 Package，结果已保存到 parsed_model.json")

    # -------------------------
    # 执行后处理（替换 EAID -> name, 规范 map_to_*）
    # -------------------------
    with open("parsed_model.json", "r", encoding="utf-8") as fh:
        model = json.load(fh)
    id_name_map = {eid: info.get("name") for eid, info in eaid_combined_map.items() if info.get("name")}
    for pkg in model["packages"]:
        simplify_element(pkg, id_name_map)

    # 保存精简后的文件
    with open("parsed_model_simplified.json", "w", encoding="utf-8") as fh:
        json.dump(model, fh, indent=2, ensure_ascii=False)

    print("已生成精简后的 parsed_model_simplified.json（map_to_LV/SV/MV/CV 均已处理）")

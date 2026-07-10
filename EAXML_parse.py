import xml.etree.ElementTree as ET
import json
from collections import defaultdict
import os
import sys

# 修复 Windows 控制台和文件输出的中文乱码问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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
    if name in el.attrib:
        return el.attrib[name]
    for k, v in el.attrib.items():
        if localname(k) == name:
            return v
    return None

def collect_tagged_values(element: ET.Element):
    """收集元素的所有 Tagged Values"""
    tagged_values = {}
    for tv in element.findall(".//{*}taggedValue/{*}TaggedValue"):
        tag = get_attr(tv, "tag")
        value = get_attr(tv, "value")
        if tag:
            tagged_values[tag] = value
    for tv in element.findall("./{*}TaggedValue"):
        tag = get_attr(tv, "tag")
        value = get_attr(tv, "value")
        if tag:
            tagged_values[tag] = value
    return tagged_values

def collect_element_annotations(element: ET.Element):
    """收集元素的注解信息（如 ownedComment）"""
    annotations = []
    for comment in element.findall("./{*}ownedComment"):
        ann = {
            "body": get_attr(comment, "body") or "".join(comment.itertext()),
            "id": get_attr(comment, "id")
        }
        for annotated in comment.findall("./{*}annotatedElement"):
            ann["annotatedElement_ref"] = get_attr(annotated, "idref")
        annotations.append(ann)
    return annotations

def build_ea_connector_map(root: ET.Element):
    """直接从 EA 专有的 XMI.extension 扩展块中提取精确的连线端点映射关系，防止大面积跳过"""
    connector_map = {}
    for conn in root.findall(".//{*}XMI.extension/{*}connectors/{*}connector") or root.findall(".//{*}connectors/{*}connector"):
        xmi_id = get_attr(conn, "idref") or get_attr(conn, "id")
        if not xmi_id:
            continue
        source_el = conn.find("./{*}source")
        target_el = conn.find("./{*}target")
        
        source_ref = get_attr(source_el, "idref") if source_el is not None else None
        target_ref = get_attr(target_el, "idref") if target_el is not None else None
        
        if source_ref or target_ref:
            connector_map[xmi_id] = {
                "source_ref": source_ref,
                "target_ref": target_ref
            }
    return connector_map

def build_eaid_maps(root):
    """返回全局 EAID 构造型、注释与名称映射表"""
    eaid_stereotype_map = defaultdict(list)
    fms_annotations = defaultdict(list)
    id_name_map = {}
    id_tagged_values = {}  
    id_annotations = defaultdict(list)  

    for el in root.iter():
        el_id = get_attr(el, "id")
        el_name = get_attr(el, "name")

        if el_id:
            tvs = collect_tagged_values(el)
            if tvs:
                id_tagged_values[el_id] = tvs
            comments = collect_element_annotations(el)
            if comments:
                id_annotations[el_id] = comments

        if el_id and el_name:
            id_name_map[el_id] = el_name

        for raw_k, raw_v in el.attrib.items():
            k_local = localname(raw_k)
            if k_local.startswith("base_") and raw_v:
                base_id = raw_v
                props = {}
                for a_k, a_v in el.attrib.items():
                    kl = localname(a_k)
                    if kl.startswith("base_"):
                        continue  
                    props[kl] = a_v

                stereo_tvs = collect_tagged_values(el)
                if stereo_tvs:
                    props["_stereotype_tagged_values"] = stereo_tvs

                stereo_name = el.attrib.get("__EAStereoName") or localname(el.tag)
                if stereo_name not in eaid_stereotype_map[base_id]:
                    eaid_stereotype_map[base_id].append(stereo_name)

                fms_annotations[base_id].append({
                    "stereotype_element": localname(el.tag),
                    "stereotype_name": stereo_name,
                    "props": props
                })

    eaid_combined_map = {}
    all_ids = set(eaid_stereotype_map.keys()) | set(id_name_map.keys()) | set(id_tagged_values.keys())
    for eid in all_ids:
        eaid_combined_map[eid] = {
            "stereotypes": eaid_stereotype_map.get(eid, []),
            "name": id_name_map.get(eid),
            "tagged_values": id_tagged_values.get(eid, {}),
            "annotations": id_annotations.get(eid, [])
        }

    return eaid_combined_map, dict(fms_annotations), id_name_map


# -------------------------
# 解析器
# -------------------------
def parse_property(prop, eaid_map, fms_annotations):
    """解析属性（包括 Port）"""
    xmi_id = get_attr(prop, "id")
    is_port = prop.find("./{*}port") is not None

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
        "aggregation": get_attr(prop, "aggregation"),  
        "isStatic": get_attr(prop, "isStatic"),
        "isOrdered": get_attr(prop, "isOrdered"),
        "isUnique": get_attr(prop, "isUnique"),
        "isDerived": get_attr(prop, "isDerived"),
        "isPort": is_port,
        "isService": get_attr(prop, "isService"),
        "isBehavior": get_attr(prop, "isBehavior"),
        "isConjugated": get_attr(prop, "isConjugated"),
        "stereotypes": eaid_map.get(xmi_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(xmi_id, []),
        "tagged_values": eaid_map.get(xmi_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(xmi_id, {}).get("annotations", [])
    }

    if is_port:
        port_elem = prop.find("./{*}port")
        data["port_info"] = {
            "isService": get_attr(port_elem, "isService"),
            "isBehavior": get_attr(port_elem, "isBehavior"),
            "isConjugated": get_attr(port_elem, "isConjugated")
        }

    sub_props = []
    for sub in prop.findall("./{*}ownedAttribute"):
        sub_props.append(parse_property(sub, eaid_map, fms_annotations))
    if sub_props:
        data["nested_attributes"] = sub_props

    return data


def parse_connector(conn, eaid_map, fms_annotations, id_name_map):
    """解析内部 Block 之间的 ownedConnector"""
    conn_id = get_attr(conn, "id")
    ends = []
    for end in conn.findall("./{*}connectorEnd"):
        role_ref = get_attr(end, "role")
        part_with_port_ref = get_attr(end, "partWithPort")
        end_info = {
            "role": role_ref,
            "role_name": id_name_map.get(role_ref, role_ref) if role_ref else None,
            "partWithPort": part_with_port_ref,
            "partWithPort_name": id_name_map.get(part_with_port_ref, part_with_port_ref) if part_with_port_ref else None
        }
        ends.append(end_info)

    return {
        "id": conn_id,
        "name": get_attr(conn, "name"),
        "type": get_attr(conn, "type"),
        "visibility": get_attr(conn, "visibility"),
        "isStatic": get_attr(conn, "isStatic"),
        "stereotypes": eaid_map.get(conn_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(conn_id, []),
        "tagged_values": eaid_map.get(conn_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(conn_id, {}).get("annotations", []),
        "connector_ends": ends
    }


def parse_association(assoc, eaid_map, fms_annotations, id_name_map, ea_connector_map):
    """解析模式间或组件间的强连线关系（如 refine, arm_of, exclude）并精确捕获两端端点"""
    assoc_id = get_attr(assoc, "id")
    
    # 优先从 EA 全局连线映射表中读取底层高保真端点 ID
    ea_conn = ea_connector_map.get(assoc_id, {})
    source_ref = ea_conn.get("source_ref")
    target_ref = ea_conn.get("target_ref")
    
    # Fallback 兼容标准 UML 属性
    if not source_ref:
        source_ref = get_attr(assoc, "source") or get_attr(assoc, "client")
    if not target_ref:
        target_ref = get_attr(assoc, "target") or get_attr(assoc, "supplier")
        
    member_ends = [get_attr(me, "idref") for me in assoc.findall("./{*}memberEnd") if get_attr(me, "idref")]
    owned_ends = []
    for oe in assoc.findall("./{*}ownedEnd"):
        oe_type = get_attr(oe.find("./{*}type"), "idref") or get_attr(oe, "type")
        if oe_type: 
            owned_ends.append(oe_type)

    return {
        "id": assoc_id,
        "name": get_attr(assoc, "name"),
        "type": get_attr(assoc, "type") or "uml:Association",
        "stereotypes": eaid_map.get(assoc_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(assoc_id, []),
        "tagged_values": eaid_map.get(assoc_id, {}).get("tagged_values", {}),
        "endpoints": {
            "source_ref": source_ref,
            "source_name": id_name_map.get(source_ref) if source_ref else None,
            "target_ref": target_ref,
            "target_name": id_name_map.get(target_ref) if target_ref else None,
            "member_ends": member_ends,
            "owned_ends": owned_ends
        }
    }


def parse_enumeration(enum, eaid_map, fms_annotations):
    """解析枚举类型"""
    enum_id = get_attr(enum, "id")
    literals = []
    for lit in enum.findall("./{*}ownedLiteral"):
        lit_id = get_attr(lit, "id")
        literals.append({
            "id": lit_id,
            "name": get_attr(lit, "name"),
            "visibility": get_attr(lit, "visibility"),
            "stereotypes": eaid_map.get(lit_id, {}).get("stereotypes", []),
            "fms_annotations": fms_annotations.get(lit_id, []),
            "tagged_values": eaid_map.get(lit_id, {}).get("tagged_values", {})
        })

    return {
        "id": enum_id,
        "name": get_attr(enum, "name"),
        "type": get_attr(enum, "type"),
        "visibility": get_attr(enum, "visibility"),
        "stereotypes": eaid_map.get(enum_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(enum_id, []),
        "tagged_values": eaid_map.get(enum_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(enum_id, {}).get("annotations", []),
        "literals": literals
    }


def parse_primitive(prim, eaid_map, fms_annotations):
    """解析原始类型"""
    prim_id = get_attr(prim, "id")
    return {
        "id": prim_id,
        "name": get_attr(prim, "name"),
        "type": get_attr(prim, "type"),
        "visibility": get_attr(prim, "visibility"),
        "stereotypes": eaid_map.get(prim_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(prim_id, []),
        "tagged_values": eaid_map.get(prim_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(prim_id, {}).get("annotations", [])
    }


def parse_generalization(gen, eaid_map):
    """解析泛化关系"""
    gen_id = get_attr(gen, "id")
    return {
        "id": gen_id,
        "general": get_attr(gen, "general"),  
        "isSubstitutable": get_attr(gen, "isSubstitutable"),
        "stereotypes": eaid_map.get(gen_id, {}).get("stereotypes", []),
        "fms_annotations": []
    }


def parse_constraint(constraint, eaid_map, fms_annotations, id_name_map):
    """解析约束"""
    cons_id = get_attr(constraint, "id")
    spec = constraint.find("./{*}specification")
    body = get_attr(spec, "body") if spec is not None else None

    return {
        "id": cons_id,
        "name": get_attr(constraint, "name"),
        "visibility": get_attr(constraint, "visibility"),
        "specification": body,
        "stereotypes": eaid_map.get(cons_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(cons_id, []),
        "tagged_values": eaid_map.get(cons_id, {}).get("tagged_values", {})
    }


def parse_state_recursive(st, eaid_map, fms_annotations, id_name_map, trigger_defs, root):
    """深度递归解析状态（支持无限层级嵌套复合状态），补齐参数防崩溃"""
    st_id = get_attr(st, "id")
    st_type = get_attr(st, "type")

    state_data = {
        "id": st_id,
        "name": get_attr(st, "name"),
        "type": st_type,
        "kind": get_attr(st, "kind"),
        "visibility": get_attr(st, "visibility"),
        "incoming": [get_attr(i, "idref") for i in st.findall("./{*}incoming")],
        "outgoing": [get_attr(o, "idref") for o in st.findall("./{*}outgoing")],
        "stereotypes": eaid_map.get(st_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(st_id, []),
        "tagged_values": eaid_map.get(st_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(st_id, {}).get("annotations", []),
        "isComposite": get_attr(st, "isComposite"),
        "isOrthogonal": get_attr(st, "isOrthogonal"),
        "isFinal": get_attr(st, "isFinal"),
        "connectionPoint": [
            {"id": get_attr(cp, "id"), "name": get_attr(cp, "name")}
            for cp in st.findall("./{*}connectionPoint")
        ]
    }

    entry_act = parse_behavior(st.find("./{*}entry"), id_name_map)
    exit_act = parse_behavior(st.find("./{*}exit"), id_name_map)
    do_act = parse_behavior(st.find("./{*}doActivity"), id_name_map)
    if entry_act: state_data["entry"] = entry_act
    if exit_act: state_data["exit"] = exit_act
    if do_act: state_data["doActivity"] = do_act

    # 发现复合状态含有子 region，进行强类型递归向下提取
    if st.find("./{*}region") is not None:
        state_data["sub_regions"] = []
        for sub_reg in st.findall("./{*}region"):
            sub_reg_data = {
                "id": get_attr(sub_reg, "id"),
                "name": get_attr(sub_reg, "name"),
                "states": [],
                "transitions": []
            }
            for sub_st in sub_reg.findall("./{*}subvertex"):
                sub_reg_data["states"].append(parse_state_recursive(sub_st, eaid_map, fms_annotations, id_name_map, trigger_defs, root))
            for sub_tr in sub_reg.findall("./{*}transition"):
                sub_reg_data["transitions"].append(parse_transition(sub_tr, eaid_map, fms_annotations, id_name_map, trigger_defs, root))
            state_data["sub_regions"].append(sub_reg_data)

    state_constraints = []
    for rule in st.findall("./{*}ownedRule"):
        state_constraints.append(parse_constraint(rule, eaid_map, fms_annotations, id_name_map))
    if state_constraints:
        state_data["constraints"] = state_constraints

    return state_data


def parse_transition(tr, eaid_map, fms_annotations, id_name_map, trigger_defs, root):
    """解析状态迁移关系"""
    tr_id = get_attr(tr, "id")
    guard_el = tr.find("./{*}guard")
    guard_body = None
    if guard_el is not None:
        spec = guard_el.find("./{*}specification")
        if spec is not None: 
            guard_body = get_attr(spec, "body")

    trans_info = {
        "id": tr_id,
        "source": get_attr(tr, "source"),
        "source_name": id_name_map.get(get_attr(tr, "source")) if get_attr(tr, "source") else None,
        "target": get_attr(tr, "target"),
        "target_name": id_name_map.get(get_attr(tr, "target")) if get_attr(tr, "target") else None,
        "kind": get_attr(tr, "kind"),
        "visibility": get_attr(tr, "visibility"),
        "stereotypes": eaid_map.get(tr_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(tr_id, []),
        "tagged_values": eaid_map.get(tr_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(tr_id, {}).get("annotations", []),
        "guard": guard_body,
        "triggers": []
    }

    effect = tr.find("./{*}effect")
    if effect is not None:
        trans_info["effect"] = parse_behavior(effect, id_name_map)

    for trig in tr.findall("./{*}trigger"):
        trig_ref = get_attr(trig, "idref") or get_attr(trig, "id")
        trig_info = trigger_defs.get(trig_ref) if trigger_defs else None

        if trig_info is None:
            event_el = trig.find("./{*}event")
            event_id = get_attr(event_el, "id") if event_el is not None else None
            signal_el = event_el.find("./{*}signal") if event_el is not None else None
            trig_info = {
                "id": trig_ref,
                "name": get_attr(trig, "name"),
                "event": event_id,
                "event_name": id_name_map.get(event_id, event_id) if event_id else None,
                "signal": get_attr(signal_el, "idref") if signal_el is not None else None,
                "signal_name": get_attr(signal_el, "name") if signal_el is not None else None,
                "fms_annotations": fms_annotations.get(trig_ref, []) if trig_ref else []
            }
        trans_info["triggers"].append(trig_info)

    return trans_info


def parse_state_machine(sm, eaid_map, fms_annotations, id_name_map, root):
    """解析行为或约束状态机"""
    sm_id = get_attr(sm, "id")
    data = {
        "id": sm_id,
        "name": get_attr(sm, "name"),
        "type": get_attr(sm, "type"),
        "visibility": get_attr(sm, "visibility"),
        "stereotypes": eaid_map.get(sm_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(sm_id, []),
        "tagged_values": eaid_map.get(sm_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(sm_id, {}).get("annotations", []),
        "regions": [],
        "constraints": []
    }

    for rule in sm.findall("./{*}ownedRule"):
        data["constraints"].append(parse_constraint(rule, eaid_map, fms_annotations, id_name_map))

    trigger_defs = {}
    for trig in sm.findall(".//{*}nestedClassifier"):
        if get_attr(trig, "type") != "uml:Trigger":
            continue
        trig_id = get_attr(trig, "id")
        event = trig.find("./{*}event")
        event_id = get_attr(event, "idref") or get_attr(event, "id") if event is not None else None
        signal_el = event.find("./{*}signal") if event is not None else None
        trigger_defs[trig_id] = {
            "id": trig_id,
            "name": get_attr(trig, "name"),
            "event": event_id,
            "event_name": id_name_map.get(event_id, event_id) if event_id else None,
            "signal": get_attr(signal_el, "idref") if signal_el is not None else None,
            "signal_name": get_attr(signal_el, "name") if signal_el is not None else None,
            "fms_annotations": fms_annotations.get(trig_id, []),
            "tagged_values": eaid_map.get(trig_id, {}).get("tagged_values", {})
        }

    for region in sm.findall("./{*}region"):
        reg = {"states": [], "transitions": []}
        reg_constraints = []
        for rule in region.findall("./{*}ownedRule"):
            reg_constraints.append(parse_constraint(rule, eaid_map, fms_annotations, id_name_map))
        if reg_constraints:
            reg["constraints"] = reg_constraints

        for st in region.findall("./{*}subvertex"):
            reg["states"].append(parse_state_recursive(st, eaid_map, fms_annotations, id_name_map, trigger_defs, root))

        for tr in region.findall("./{*}transition"):
            reg["transitions"].append(parse_transition(tr, eaid_map, fms_annotations, id_name_map, trigger_defs, root))

        data["regions"].append(reg)
    return data


def parse_behavior(behavior, id_name_map):
    if behavior is None:
        return None
    bh_id = get_attr(behavior, "id")
    bh_type = localname(behavior.tag)

    result = {
        "id": bh_id,
        "type": bh_type,
        "name": get_attr(behavior, "name")
    }
    if bh_type == "OpaqueBehavior":
        result["body"] = get_attr(behavior, "body") or "".join(behavior.itertext())
        result["language"] = get_attr(behavior, "language")
    elif bh_type in ("Activity", "OpaqueAction"):
        nodes = []
        for node in behavior.findall("./{*}node"):
            node_data = {
                "id": get_attr(node, "id"),
                "name": get_attr(node, "name"),
                "type": get_attr(node, "type"),
                "kind": get_attr(node, "kind")
            }
            if node.find("./{*}input") is not None:
                node_data["input"] = get_attr(node.find("./{*}input"), "name")
            if node.find("./{*}output") is not None:
                node_data["output"] = get_attr(node.find("./{*}output"), "name")
            nodes.append(node_data)
        if nodes:
            result["nodes"] = nodes
    return result


def parse_class(cls, eaid_map, fms_annotations, id_name_map, root):
    """解析大块组件（Block/Class）"""
    cls_id = get_attr(cls, "id")
    data = {
        "id": cls_id,
        "name": get_attr(cls, "name"),
        "type": get_attr(cls, "type"),
        "visibility": get_attr(cls, "visibility"),
        "isAbstract": get_attr(cls, "isAbstract"),
        "isFinal": get_attr(cls, "isFinal"),
        "isActive": get_attr(cls, "isActive"),
        "stereotypes": eaid_map.get(cls_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(cls_id, []),
        "tagged_values": eaid_map.get(cls_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(cls_id, {}).get("annotations", []),
        "ownedAttributes": [],
        "ownedBehaviors": [],
        "ownedConnectors": [],
        "generalizations": []
    }

    for child in cls:
        tag = localname(child.tag)
        if tag == "ownedAttribute":
            data["ownedAttributes"].append(parse_property(child, eaid_map, fms_annotations))
        elif tag == "ownedBehavior":
            if get_attr(child, "type") == "uml:StateMachine":
                data["ownedBehaviors"].append(parse_state_machine(child, eaid_map, fms_annotations, id_name_map, root))
            else:
                data["ownedBehaviors"].append({
                    "id": get_attr(child, "id"),
                    "name": get_attr(child, "name"),
                    "type": get_attr(child, "type"),
                    "visibility": get_attr(child, "visibility"),
                    "body": get_attr(child, "body") or "".join(child.itertext()),
                    "stereotypes": eaid_map.get(get_attr(child, "id"), {}).get("stereotypes", []),
                    "fms_annotations": fms_annotations.get(get_attr(child, "id"), []),
                    "tagged_values": eaid_map.get(get_attr(child, "id"), {}).get("tagged_values", {})
                })
        elif tag == "ownedConnector":
            data["ownedConnectors"].append(parse_connector(child, eaid_map, fms_annotations, id_name_map))
        elif tag == "ownedRule":
            data.setdefault("constraints", []).append(parse_constraint(child, eaid_map, fms_annotations, id_name_map))
        elif tag == "generalization":
            gen = parse_generalization(child, eaid_map)
            gen["general_name"] = id_name_map.get(gen["general"], gen["general"]) if gen.get("general") else None
            data["generalizations"].append(gen)
    return data


def parse_package(pkg, eaid_map, fms_annotations, id_name_map, root, ea_connector_map):
    """解析规约系统包"""
    pkg_id = get_attr(pkg, "id")
    data = {
        "id": pkg_id,
        "name": get_attr(pkg, "name"),
        "type": "uml:Package",
        "visibility": get_attr(pkg, "visibility"),
        "stereotypes": eaid_map.get(pkg_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(pkg_id, []),
        "tagged_values": eaid_map.get(pkg_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(pkg_id, {}).get("annotations", []),
        "children": []
    }

    for child in pkg.findall("./{*}packagedElement"):
        child_type = get_attr(child, "type")
        if child_type == "uml:Class":
            data["children"].append(parse_class(child, eaid_map, fms_annotations, id_name_map, root))
        elif child_type == "uml:Package":
            data["children"].append(parse_package(child, eaid_map, fms_annotations, id_name_map, root, ea_connector_map))
        elif child_type == "uml:Enumeration":
            data["children"].append(parse_enumeration(child, eaid_map, fms_annotations))
        elif child_type == "uml:PrimitiveType":
            data["children"].append(parse_primitive(child, eaid_map, fms_annotations))
        elif child_type == "uml:StateMachine":
            data["children"].append(parse_state_machine(child, eaid_map, fms_annotations, id_name_map, root))
        elif child_type in ("uml:Association", "uml:Dependency", "uml:Realization"):
            # 强关系类型分流进入关联关系高保真端点解析器
            data["children"].append(parse_association(child, eaid_map, fms_annotations, id_name_map, ea_connector_map))
        elif child_type == "uml:DataType":
            data["children"].append({
                "id": get_attr(child, "id"),
                "name": get_attr(child, "name"),
                "type": child_type,
                "visibility": get_attr(child, "visibility"),
                "stereotypes": eaid_map.get(get_attr(child, "id"), {}).get("stereotypes", []),
                "fms_annotations": fms_annotations.get(get_attr(child, "id"), []),
                "tagged_values": eaid_map.get(get_attr(child, "id"), {}).get("tagged_values", {}),
                "annotations": eaid_map.get(get_attr(child, "id"), {}).get("annotations", []),
                "ownedAttributes": [
                    parse_property(p, eaid_map, fms_annotations)
                    for p in child.findall("./{*}ownedAttribute")
                ]
            })
        else:
            data["children"].append({
                "id": get_attr(child, "id"),
                "name": get_attr(child, "name"),
                "type": child_type,
                "stereotypes": eaid_map.get(get_attr(child, "id"), {}).get("stereotypes", []),
                "fms_annotations": fms_annotations.get(get_attr(child, "id"), []),
                "tagged_values": eaid_map.get(get_attr(child, "id"), {}).get("tagged_values", {}),
                "annotations": eaid_map.get(get_attr(child, "id"), {}).get("annotations", [])
            })
    return data


# -------------------------
# 后处理映射规范化工具
# -------------------------
def normalize_annotation_maps(ann: dict, id_name_map: dict):
    if not isinstance(ann, dict): return
    props = ann.get("props", {})
    if not isinstance(props, dict): return
    for k in list(props.keys()):
        v = props.get(k)
        if isinstance(v, str):
            if k.startswith("map_to_") or k in ("map_to_LV", "map_to_SV", "map_to_MV", "map_to_CV"):
                mapped = id_name_map.get(v, v)
                props[k] = mapped
                props[f"_{k}_name"] = id_name_map.get(v, v)
            elif v in id_name_map:
                props[k] = id_name_map[v]
                props[f"_{k}_name"] = id_name_map[v]
    ann["props"] = props

def normalize_transition_refs(tr, id_name_map):
    if tr.get("source") and tr["source"] in id_name_map:
        tr["source_name"] = id_name_map[tr["source"]]
    if tr.get("target") and tr["target"] in id_name_map:
        tr["target_name"] = id_name_map[tr["target"]]

def simplify_element(el, id_name_map):
    if el is None: return
    for ann in el.get("fms_annotations", []) or []:
        normalize_annotation_maps(ann, id_name_map)

    if "ownedAttributes" in el:
        for attr in el["ownedAttributes"]:
            for ann in attr.get("fms_annotations", []) or []:
                normalize_annotation_maps(ann, id_name_map)
            for k in ["visibility", "lower", "upper"]:
                attr.pop(k, None)

    if "ownedConnectors" in el:
        for conn in el["ownedConnectors"]:
            for ann in conn.get("fms_annotations", []) or []:
                normalize_annotation_maps(ann, id_name_map)
            for end in conn.get("connector_ends", []):
                if end.get("role") and end["role"] in id_name_map:
                    end["role_name"] = id_name_map[end["role"]]
                if end.get("partWithPort") and end["partWithPort"] in id_name_map:
                    end["partWithPort_name"] = id_name_map[end["partWithPort"]]

    if "ownedBehaviors" in el:
        for sm in el["ownedBehaviors"]:
            for ann in sm.get("fms_annotations", []) or []:
                normalize_annotation_maps(ann, id_name_map)
            for reg in sm.get("regions", []):
                for st in reg.get("states", []):
                    for ann in st.get("fms_annotations", []) or []:
                        normalize_annotation_maps(ann, id_name_map)
                    for k in ["kind", "value"]:
                        st.pop(k, None)
                    st["incoming"] = [id_name_map.get(i, i) for i in st.get("incoming", []) if i]
                    st["outgoing"] = [id_name_map.get(o, o) for o in st.get("outgoing", []) if o]

                    for sub_reg in st.get("sub_regions", []):
                        for sub_st in sub_reg.get("states", []):
                            for ann in sub_st.get("fms_annotations", []) or []:
                                normalize_annotation_maps(ann, id_name_map)
                        for sub_tr in sub_reg.get("transitions", []):
                            normalize_transition_refs(sub_tr, id_name_map)

                for tr in reg.get("transitions", []):
                    for ann in tr.get("fms_annotations", []) or []:
                        normalize_annotation_maps(ann, id_name_map)
                    normalize_transition_refs(tr, id_name_map)
                    tr.pop("kind", None)
                    for trig in tr.get("triggers", []) or []:
                        for ann in trig.get("fms_annotations", []) or []:
                            normalize_annotation_maps(ann, id_name_map)
                        if trig.get("event"):
                            trig["event"] = id_name_map.get(trig["event"], trig["event"])
                            trig["event_name"] = id_name_map.get(trig["event"], trig["event"])
                        if trig.get("signal"):
                            trig["signal"] = id_name_map.get(trig["signal"], trig["signal"])

    if "children" in el:
        for child in el["children"]:
            simplify_element(child, id_name_map)


def collect_all_element_info(root, eaid_map, fms_annotations, id_name_map):
    global_info = {"profiles": [], "diagrams": [], "references": [], "all_elements": []}
    for profile_app in root.findall(".//{*}ProfileApplication"):
        global_info["profiles"].append({
            "applied_profile": get_attr(profile_app.find("./{*}appliedProfile"), "href"),
            "imported_package": get_attr(profile_app.find("./{*}importedPackage"), "idref")
        })
    for diagram in root.findall(".//{*}Diagram"):
        diag_id = get_attr(diagram, "id")
        global_info["diagrams"].append({
            "id": diag_id, "name": get_attr(diagram, "name"), "type": get_attr(diagram, "type"),
            "stereotypes": eaid_map.get(diag_id, {}).get("stereotypes", []),
            "tagged_values": eaid_map.get(diag_id, {}).get("tagged_values", {})
        })
    for el in root.iter():
        el_id = get_attr(el, "id")
        if el_id and el_id in eaid_map:
            info = eaid_map.get(el_id, {})
            if info.get("name"):
                global_info["all_elements"].append({
                    "id": el_id, "name": info["name"], "type": get_attr(el, "type"),
                    "stereotypes": info.get("stereotypes", []), "tagged_values": info.get("tagged_values", {})
                })
    return global_info


# -------------------------
# 主环境程序
# -------------------------
if __name__ == "__main__":
    xml_file = "example3.xml"   
    if not os.path.exists(xml_file):
        print(f"❌ 找不到输入模型文件: {xml_file}")
        sys.exit(1)

    tree = ET.parse(xml_file)
    root = tree.getroot()

    # 1) 构建基础全局映射表与 EA 专有底层连线信息表
    eaid_combined_map, fms_annotations, id_name_map = build_eaid_maps(root)
    ea_connector_map = build_ea_connector_map(root)

    with open("eaid_stereotype_map.json", "w", encoding="utf-8") as fh:
        json.dump(eaid_combined_map, fh, indent=2, ensure_ascii=False)
    with open("fms_annotations.json", "w", encoding="utf-8") as fh:
        json.dump(fms_annotations, fh, indent=2, ensure_ascii=False)

    # 2) 仅从根节点下发进行单向拓扑提取，杜绝重复冗余
    packages = []
    for el in root.findall("./{*}packagedElement") + root.findall(".//{*}Model/{*}packagedElement"):
        if get_attr(el, "type") == "uml:Package":
            packages.append(parse_package(el, eaid_combined_map, fms_annotations, id_name_map, root, ea_connector_map))

    with open("parsed_model.json", "w", encoding="utf-8") as fh:
        json.dump({"packages": packages}, fh, indent=2, ensure_ascii=False)

    # 3) 收集元数据并后处理归一化
    global_info = collect_all_element_info(root, eaid_combined_map, fms_annotations, id_name_map)
    with open("global_info.json", "w", encoding="utf-8") as fh:
        json.dump(global_info, fh, indent=2, ensure_ascii=False)

    with open("parsed_model.json", "r", encoding="utf-8") as fh:
        model = json.load(fh)
    for pkg in model["packages"]:
        simplify_element(pkg, id_name_map)

    with open("parsed_model_simplified.json", "w", encoding="utf-8") as fh:
        json.dump(model, fh, indent=2, ensure_ascii=False)

    print("✅ 解析完成！连线端点与嵌套状态机 Bug 已全面修复。")
    print(f"   - 基础映射字典: eaid_stereotype_map.json")
    print(f"   - 全量模型输出: parsed_model.json")
    print(f"   - 归一化精简版: parsed_model_simplified.json (下游 json_to_mtrdl.py 输入源)")
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
    # 直接键匹配优先
    if name in el.attrib:
        return el.attrib[name]
    # 再按本地名匹配
    for k, v in el.attrib.items():
        if localname(k) == name:
            return v
    return None

def collect_tagged_values(element: ET.Element):
    """
    收集元素的所有 Tagged Values（子元素形式）
    EA导出的XMI中，Tagged Values 通常以子元素形式存在：
    <UML:ModelElement.taggedValue>
        <UML:TaggedValue tag="xxx" value="yyy"/>
    </UML:ModelElement.taggedValue>
    """
    tagged_values = {}
    # 查找 taggedValue 子元素（可能有各种命名空间前缀）
    for tv in element.findall(".//{*}taggedValue/{*}TaggedValue"):
        tag = get_attr(tv, "tag")
        value = get_attr(tv, "value")
        if tag:
            tagged_values[tag] = value
    # 兼容另一种形式：直接子元素
    for tv in element.findall("./{*}TaggedValue"):
        tag = get_attr(tv, "tag")
        value = get_attr(tv, "value")
        if tag:
            tagged_values[tag] = value
    return tagged_values

def collect_element_annotations(element: ET.Element, idref_prefix=None):
    """
    收集元素的注解信息（如 UML:Comment）
    idref_prefix: 如果存在 annotatedElement 引用，提取其 idref
    """
    annotations = []
    for comment in element.findall("./{*}ownedComment"):
        ann = {
            "body": get_attr(comment, "body") or "".join(comment.itertext()),
            "id": get_attr(comment, "id")
        }
        # 查找被注解的元素引用
        for annotated in comment.findall("./{*}annotatedElement"):
            ann["annotatedElement_ref"] = get_attr(annotated, "idref")
        annotations.append(ann)
    return annotations

# -------------------------
# 构建 EAID -> stereotype 映射 & FMS 注释映射
# -------------------------
def build_eaid_maps(root):
    """
    返回:
      - eaid_combined_map: { EAID: {"stereotypes": [...], "name": str, "tagged_values": {...}} }
      - fms_annotations: dict EAID -> [annotation,...]
      - profile_info: dict EAID -> profile相关元数据
    """
    eaid_stereotype_map = defaultdict(list)
    fms_annotations = defaultdict(list)
    id_name_map = {}
    id_tagged_values = {}  # 收集所有元素的 tagged values
    id_annotations = defaultdict(list)  # 收集注释

    for el in root.iter():
        el_id = get_attr(el, "id")
        el_name = get_attr(el, "name")
        el_type = get_attr(el, "type")

        if el_id:
            # 收集 Tagged Values
            tvs = collect_tagged_values(el)
            if tvs:
                id_tagged_values[el_id] = tvs

            # 收集注释
            comments = collect_element_annotations(el)
            if comments:
                id_annotations[el_id] = comments

        if el_id and el_name:
            id_name_map[el_id] = el_name

        # stereotype base_* 属性关联
        for raw_k, raw_v in el.attrib.items():
            k_local = localname(raw_k)
            if k_local.startswith("base_") and raw_v:
                base_id = raw_v
                # 收集构造型上的所有属性
                props = {}
                for a_k, a_v in el.attrib.items():
                    kl = localname(a_k)
                    if kl.startswith("base_"):
                        continue  # 跳过 base 属性本身
                    props[kl] = a_v

                # 尝试从构造型元素内部收集 tagged values
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

    # 组合为统一字典
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
        "aggregation": get_attr(prop, "aggregation"),  # 聚合类型
        "isStatic": get_attr(prop, "isStatic"),
        "isOrdered": get_attr(prop, "isOrdered"),
        "isUnique": get_attr(prop, "isUnique"),
        "isDerived": get_attr(prop, "isDerived"),
        "isPort": is_port,
        # Port 特有属性
        "isService": get_attr(prop, "isService"),
        "isBehavior": get_attr(prop, "isBehavior"),
        "isConjugated": get_attr(prop, "isConjugated"),
        "stereotypes": eaid_map.get(xmi_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(xmi_id, []),
        "tagged_values": eaid_map.get(xmi_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(xmi_id, {}).get("annotations", [])
    }

    # 如果是 Port，解析端口信息
    if is_port:
        port_elem = prop.find("./{*}port")
        data["port_info"] = {
            "isService": get_attr(port_elem, "isService"),
            "isBehavior": get_attr(port_elem, "isBehavior"),
            "isConjugated": get_attr(port_elem, "isConjugated")
        }

    # 解析属性的子属性（属性链）
    sub_props = []
    for sub in prop.findall("./{*}ownedAttribute"):
        sub_props.append(parse_property(sub, eaid_map, fms_annotations))
    if sub_props:
        data["nested_attributes"] = sub_props

    return data


def parse_connector(conn, eaid_map, fms_annotations, id_name_map):
    """解析连接器"""
    conn_id = get_attr(conn, "id")

    # 解析连接端点
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
        "general": get_attr(gen, "general"),  # 父类ID
        "isSubstitutable": get_attr(gen, "isSubstitutable"),
        "stereotypes": eaid_map.get(gen_id, {}).get("stereotypes", []),
        "fms_annotations": []
    }


def parse_constraint(constraint, eaid_map, fms_annotations, id_name_map):
    """解析约束"""
    cons_id = get_attr(constraint, "id")
    spec = constraint.find("./{*}specification")
    body = None
    if spec is not None:
        body = get_attr(spec, "body")

    return {
        "id": cons_id,
        "name": get_attr(constraint, "name"),
        "visibility": get_attr(constraint, "visibility"),
        "specification": body,
        "stereotypes": eaid_map.get(cons_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(cons_id, []),
        "tagged_values": eaid_map.get(cons_id, {}).get("tagged_values", {})
    }


def parse_state_machine(sm, eaid_map, fms_annotations, id_name_map):
    """解析状态机"""
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
        # 新增：解析状态机的约束
        "constraints": []
    }

    # 解析约束
    for rule in sm.findall("./{*}ownedRule"):
        cons = parse_constraint(rule, eaid_map, fms_annotations, id_name_map)
        data["constraints"].append(cons)

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
            "event_name": id_name_map.get(event_id, event_id) if event_id else None,
            "signal": signal_ref,
            "signal_name": signal_name,
            "fms_annotations": fms_annotations.get(trig_id, []),
            "tagged_values": eaid_map.get(trig_id, {}).get("tagged_values", {})
        }

    for region in sm.findall("./{*}region"):
        reg = {"states": [], "transitions": []}

        # 解析 region 的约束
        reg_constraints = []
        for rule in region.findall("./{*}ownedRule"):
            cons = parse_constraint(rule, eaid_map, fms_annotations, id_name_map)
            reg_constraints.append(cons)
        if reg_constraints:
            reg["constraints"] = reg_constraints

        for st in region.findall("./{*}subvertex"):
            st_id = get_attr(st, "id")
            st_type = get_attr(st, "type")

            # 解析 State 的 entry/exit/doActivity
            entry_act = parse_behavior(st.find("./{*}entry"), id_name_map)
            exit_act = parse_behavior(st.find("./{*}exit"), id_name_map)
            do_act = parse_behavior(st.find("./{*}doActivity"), id_name_map)

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
                # State 特有
                "isComposite": get_attr(st, "isComposite"),
                "isOrthogonal": get_attr(st, "isOrthogonal"),
                "isFinal": get_attr(st, "isFinal"),
                # 连接点（伪状态）
                "connectionPoint": [
                    {"id": get_attr(cp, "id"), "name": get_attr(cp, "name")}
                    for cp in st.findall("./{*}connectionPoint")
                ]
            }

            # 如果是 Composite State，递归解析子区域
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
                        sub_st_parsed = parse_state_recursive(sub_st, eaid_map, fms_annotations, id_name_map, root=None)
                        if sub_st_parsed:
                            sub_reg_data["states"].append(sub_st_parsed)
                    for sub_tr in sub_reg.findall("./{*}transition"):
                        sub_tr_parsed = parse_transition(sub_tr, eaid_map, fms_annotations, id_name_map, trigger_defs={})
                        sub_reg_data["transitions"].append(sub_tr_parsed)
                    state_data["sub_regions"].append(sub_reg_data)

            # 添加 entry/exit/doActivity
            if entry_act:
                state_data["entry"] = entry_act
            if exit_act:
                state_data["exit"] = exit_act
            if do_act:
                state_data["doActivity"] = do_act

            # 解析 State 内部的约束
            state_constraints = []
            for rule in st.findall("./{*}ownedRule"):
                cons = parse_constraint(rule, eaid_map, fms_annotations, id_name_map)
                state_constraints.append(cons)
            if state_constraints:
                state_data["constraints"] = state_constraints

            reg["states"].append(state_data)

        for tr in region.findall("./{*}transition"):
            trans_info = parse_transition(tr, eaid_map, fms_annotations, id_name_map, trigger_defs, root=None)
            reg["transitions"].append(trans_info)

        data["regions"].append(reg)
    return data


def parse_behavior(behavior, id_name_map):
    """解析行为（entry/exit/doActivity）"""
    if behavior is None:
        return None
    bh_id = get_attr(behavior, "id")
    bh_type = localname(behavior.tag)

    result = {
        "id": bh_id,
        "type": bh_type,
        "name": get_attr(behavior, "name")
    }

    # 尝试获取行为体
    if bh_type == "OpaqueBehavior":
        result["body"] = get_attr(behavior, "body") or "".join(behavior.itertext())
        result["language"] = get_attr(behavior, "language")
    elif bh_type in ("Activity", "OpaqueAction"):
        # 收集 Activity 的节点和边
        nodes = []
        for node in behavior.findall("./{*}node"):
            node_data = {
                "id": get_attr(node, "id"),
                "name": get_attr(node, "name"),
                "type": get_attr(node, "type"),
                "kind": get_attr(node, "kind")
            }
            # 解析动作输入/输出
            if node.find("./{*}input") is not None:
                node_data["input"] = get_attr(node.find("./{*}input"), "name")
            if node.find("./{*}output") is not None:
                node_data["output"] = get_attr(node.find("./{*}output"), "name")
            nodes.append(node_data)
        if nodes:
            result["nodes"] = nodes

    return result


def parse_state_recursive(st, eaid_map, fms_annotations, id_name_map, root):
    """递归解析状态"""
    st_id = get_attr(st, "id")
    return {
        "id": st_id,
        "name": get_attr(st, "name"),
        "type": get_attr(st, "type"),
        "kind": get_attr(st, "kind"),
        "stereotypes": eaid_map.get(st_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(st_id, []),
        "tagged_values": eaid_map.get(st_id, {}).get("tagged_values", {})
    }


def parse_transition(tr, eaid_map, fms_annotations, id_name_map, trigger_defs, root):
    """解析状态转换"""
    tr_id = get_attr(tr, "id")
    guard_el = tr.find("./{*}guard")
    guard_body = None
    if guard_el is not None:
        spec = guard_el.find("./{*}specification")
        guard_body = get_attr(spec, "body") if spec is not None else None

    trans_info = {
        "id": tr_id,
        "source": get_attr(tr, "source"),
        "source_name": id_name_map.get(get_attr(tr, "source"), get_attr(tr, "source")) if get_attr(tr, "source") else None,
        "target": get_attr(tr, "target"),
        "target_name": id_name_map.get(get_attr(tr, "target"), get_attr(tr, "target")) if get_attr(tr, "target") else None,
        "kind": get_attr(tr, "kind"),
        "visibility": get_attr(tr, "visibility"),
        "stereotypes": eaid_map.get(tr_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(tr_id, []),
        "tagged_values": eaid_map.get(tr_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(tr_id, {}).get("annotations", []),
        "guard": guard_body,
        "triggers": []
    }

    # 解析 effect (动作)
    effect = tr.find("./{*}effect")
    if effect is not None:
        trans_info["effect"] = parse_behavior(effect, id_name_map)

    for trig in tr.findall("./{*}trigger"):
        trig_ref = get_attr(trig, "idref") or get_attr(trig, "id")
        trig_info = trigger_defs.get(trig_ref) if trigger_defs else None

        if trig_info is None:
            # fallback: 内联解析
            event_el = trig.find("./{*}event")
            event_id = get_attr(event_el, "id") if event_el is not None else None
            signal_el = event_el.find("./{*}signal") if event_el is not None else None
            signal_ref = get_attr(signal_el, "idref") if signal_el is not None else None
            signal_name = get_attr(signal_el, "name") if signal_el is not None else None
            trig_info = {
                "id": trig_ref,
                "name": get_attr(trig, "name"),
                "event": event_id,
                "event_name": id_name_map.get(event_id, event_id) if event_id else None,
                "signal": signal_ref,
                "signal_name": signal_name,
                "fms_annotations": fms_annotations.get(trig_ref, []) if trig_ref else []
            }

        trans_info["triggers"].append(trig_info)

    return trans_info


def parse_class(cls, eaid_map, fms_annotations, id_name_map, root):
    """解析类"""
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
                data["ownedBehaviors"].append(parse_state_machine(child, eaid_map, fms_annotations, id_name_map))
            else:
                # 其他行为（Activity, FunctionBehavior等）
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


def parse_package(pkg, eaid_map, fms_annotations, id_name_map, root):
    """解析包"""
    pkg_id = get_attr(pkg, "id")
    data = {
        "id": pkg_id,
        "name": get_attr(pkg, "name"),
        "type": get_attr(pkg, "type"),
        "visibility": get_attr(pkg, "visibility"),
        "stereotypes": eaid_map.get(pkg_id, {}).get("stereotypes", []),
        "fms_annotations": fms_annotations.get(pkg_id, []),
        "tagged_values": eaid_map.get(pkg_id, {}).get("tagged_values", {}),
        "annotations": eaid_map.get(pkg_id, {}).get("annotations", []),
        "children": []
    }

    for child in pkg.findall("./{*}packagedElement"):
        child_type = get_attr(child, "type")
        child_id = get_attr(child, "id")

        if child_type == "uml:Class":
            data["children"].append(parse_class(child, eaid_map, fms_annotations, id_name_map, root))
        elif child_type == "uml:Package":
            data["children"].append(parse_package(child, eaid_map, fms_annotations, id_name_map, root))
        elif child_type == "uml:Enumeration":
            data["children"].append(parse_enumeration(child, eaid_map, fms_annotations))
        elif child_type == "uml:PrimitiveType":
            data["children"].append(parse_primitive(child, eaid_map, fms_annotations))
        elif child_type == "uml:StateMachine":
            data["children"].append(parse_state_machine(child, eaid_map, fms_annotations, id_name_map))
        elif child_type == "uml:DataType":
            # DataType 可能包含属性和约束
            data["children"].append({
                "id": child_id,
                "name": get_attr(child, "name"),
                "type": child_type,
                "visibility": get_attr(child, "visibility"),
                "stereotypes": eaid_map.get(child_id, {}).get("stereotypes", []),
                "fms_annotations": fms_annotations.get(child_id, []),
                "tagged_values": eaid_map.get(child_id, {}).get("tagged_values", {}),
                "annotations": eaid_map.get(child_id, {}).get("annotations", []),
                "ownedAttributes": [
                    parse_property(p, eaid_map, fms_annotations)
                    for p in child.findall("./{*}ownedAttribute")
                ]
            })
        else:
            # 其他类型统一收集
            other = {
                "id": child_id,
                "name": get_attr(child, "name"),
                "type": child_type,
                "visibility": get_attr(child, "visibility"),
                "stereotypes": eaid_map.get(child_id, {}).get("stereotypes", []),
                "fms_annotations": fms_annotations.get(child_id, []),
                "tagged_values": eaid_map.get(child_id, {}).get("tagged_values", {}),
                "annotations": eaid_map.get(child_id, {}).get("annotations", [])
            }
            data["children"].append(other)

    return data


# -------------------------
# 通用后处理工具：规范化所有 fms_annotations 中的 map_to_* 字段
# -------------------------
def normalize_annotation_maps(ann: dict, id_name_map: dict):
    """将 annotation 的 props 中所有 map_to_* EAID 替换为对应 name"""
    if not isinstance(ann, dict):
        return
    props = ann.get("props", {})
    if not isinstance(props, dict):
        return
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
    """规范化 transition 的引用"""
    if tr.get("source") and tr["source"] in id_name_map:
        tr["source_name"] = id_name_map[tr["source"]]
    if tr.get("target") and tr["target"] in id_name_map:
        tr["target_name"] = id_name_map[tr["target"]]


def simplify_element(el, id_name_map):
    """
    后处理：EAID -> name 映射 & 精简字段
    """
    if el is None:
        return

    # 处理当前元素的 fms_annotations
    for ann in el.get("fms_annotations", []) or []:
        normalize_annotation_maps(ann, id_name_map)

    # 1. ownedAttributes
    if "ownedAttributes" in el:
        for attr in el["ownedAttributes"]:
            for ann in attr.get("fms_annotations", []) or []:
                normalize_annotation_maps(ann, id_name_map)
            # 删除不必要字段
            for k in ["visibility", "lower", "upper"]:
                attr.pop(k, None)

    # 2. ownedConnectors
    if "ownedConnectors" in el:
        for conn in el["ownedConnectors"]:
            for ann in conn.get("fms_annotations", []) or []:
                normalize_annotation_maps(ann, id_name_map)
            # 规范化连接端点引用
            for end in conn.get("connector_ends", []):
                if end.get("role") and end["role"] in id_name_map:
                    end["role_name"] = id_name_map[end["role"]]
                if end.get("partWithPort") and end["partWithPort"] in id_name_map:
                    end["partWithPort_name"] = id_name_map[end["partWithPort"]]

    # 3. ownedBehaviors -> regions -> states/transitions
    if "ownedBehaviors" in el:
        for sm in el["ownedBehaviors"]:
            for ann in sm.get("fms_annotations", []) or []:
                normalize_annotation_maps(ann, id_name_map)

            for reg in sm.get("regions", []):
                # states
                for st in reg.get("states", []):
                    for ann in st.get("fms_annotations", []) or []:
                        normalize_annotation_maps(ann, id_name_map)
                    for k in ["kind", "value"]:
                        st.pop(k, None)
                    st["incoming"] = [id_name_map.get(i, i) for i in st.get("incoming", []) if i]
                    st["outgoing"] = [id_name_map.get(o, o) for o in st.get("outgoing", []) if o]

                    # 递归处理子状态机的 region
                    for sub_reg in st.get("sub_regions", []):
                        for sub_st in sub_reg.get("states", []):
                            for ann in sub_st.get("fms_annotations", []) or []:
                                normalize_annotation_maps(ann, id_name_map)
                        for sub_tr in sub_reg.get("transitions", []):
                            normalize_transition_refs(sub_tr, id_name_map)

                # transitions
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

    # 4. children 递归
    if "children" in el:
        for child in el["children"]:
            simplify_element(child, id_name_map)


def collect_all_element_info(root, eaid_map, fms_annotations, id_name_map):
    """
    全局收集：获取所有未在包层级解析的元素信息
    包括: Profile 应用信息、Diagram 信息、Reference 信息等
    """
    global_info = {
        "profiles": [],
        "diagrams": [],
        "references": [],
        "all_elements": []
    }

    # 收集 Profile 应用信息
    for profile_app in root.findall(".//{*}ProfileApplication"):
        app_info = {
            "applied_profile": get_attr(profile_app.find("./{*}appliedProfile"), "href"),
            "imported_package": get_attr(profile_app.find("./{*}importedPackage"), "idref")
        }
        global_info["profiles"].append(app_info)

    # 收集 Diagram 信息（如果有）
    for diagram in root.findall(".//{*}Diagram"):
        diag_id = get_attr(diagram, "id")
        global_info["diagrams"].append({
            "id": diag_id,
            "name": get_attr(diagram, "name"),
            "type": get_attr(diagram, "type"),
            "stereotypes": eaid_map.get(diag_id, {}).get("stereotypes", []),
            "tagged_values": eaid_map.get(diag_id, {}).get("tagged_values", {})
        })

    # 收集所有元素的概要信息
    for el in root.iter():
        el_id = get_attr(el, "id")
        if el_id and el_id in eaid_map:
            info = eaid_map.get(el_id, {})
            if info.get("name"):
                global_info["all_elements"].append({
                    "id": el_id,
                    "name": info["name"],
                    "type": get_attr(el, "type"),
                    "stereotypes": info.get("stereotypes", []),
                    "tagged_values": info.get("tagged_values", {})
                })

    return global_info


# -------------------------
# 主程序
# -------------------------
if __name__ == "__main__":
    xml_file = "example3.xml"   # <-- 替换为你的文件名
    tree = ET.parse(xml_file)
    root = tree.getroot()

    # 1) 构建映射与注释表
    eaid_combined_map, fms_annotations, id_name_map = build_eaid_maps(root)

    # 保存映射表
    with open("eaid_stereotype_map.json", "w", encoding="utf-8") as fh:
        json.dump(eaid_combined_map, fh, indent=2, ensure_ascii=False)

    # 保存 fms_annotations
    with open("fms_annotations.json", "w", encoding="utf-8") as fh:
        json.dump(fms_annotations, fh, indent=2, ensure_ascii=False)

    # 2) 解析包/类/属性/状态机 等
    packages = []
    for el in root.iter():
        if localname(el.tag) == "packagedElement" and get_attr(el, "type") == "uml:Package":
            packages.append(parse_package(el, eaid_combined_map, fms_annotations, id_name_map, root))

    # 保存原始解析结果
    with open("parsed_model.json", "w", encoding="utf-8") as fh:
        json.dump({"packages": packages}, fh, indent=2, ensure_ascii=False)

    print(f"✅ 已生成 eaid_stereotype_map.json，共 {len(eaid_combined_map)} 条记录")
    print(f"已生成 fms_annotations.json（供调试）")
    print(f"已解析 {len(packages)} 个 Package，结果已保存到 parsed_model.json")

    # 3) 收集全局信息
    global_info = collect_all_element_info(root, eaid_combined_map, fms_annotations, id_name_map)
    with open("global_info.json", "w", encoding="utf-8") as fh:
        json.dump(global_info, fh, indent=2, ensure_ascii=False)
    print(f"已收集全局信息（Profile、Diagram等）到 global_info.json")

    # 4) 执行后处理
    with open("parsed_model.json", "r", encoding="utf-8") as fh:
        model = json.load(fh)
    for pkg in model["packages"]:
        simplify_element(pkg, id_name_map)

    with open("parsed_model_simplified.json", "w", encoding="utf-8") as fh:
        json.dump(model, fh, indent=2, ensure_ascii=False)

    print("已生成精简后的 parsed_model_simplified.json（map_to_LV/SV/MV/CV 均已处理）")
    print("\n📊 解析统计：")
    print(f"   - 构造型映射: {len(eaid_combined_map)} 条")
    print(f"   - FMS注解: {len(fms_annotations)} 条")
    print(f"   - Profile应用: {len(global_info['profiles'])} 条")
    print(f"   - 元素总数: {len(global_info['all_elements'])} 条")

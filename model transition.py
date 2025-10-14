import json
import os
from collections import defaultdict
from guard_csv_utils import guard_csv_to_nusmv  # 假设你已经实现了这个函数
import re

# ---------- 工具函数 ----------
def sanitize_name(name: str) -> str:
    return re.sub(r'[^0-9a-zA-Z_]', '_', name)

# ---------- 读取 EAID 映射 ----------
with open("eaid_stereotype_map.json", "r", encoding="utf-8") as f:
    eaid_map = json.load(f)

# ---------- 读取核心 JSON ----------
with open("parsed_model_core.json", "r", encoding="utf-8") as fh:
    model = json.load(fh)

classes = {}  # class_name -> {vars:{}, sms:[]}
events = {}   # event_name -> {type:..., ...}

# ---------- 收集类和事件 ----------
def collect_classes(pkg_list):
    for pkg in pkg_list:
        for child in pkg.get("children", []):
            ctype = child.get("type")
            cname = child.get("name")
            if ctype == "uml:Class":
                classes[cname] = {"vars": {}, "sms": []}
                for attr in child.get("ownedAttributes", []):
                    vname = sanitize_name(attr["name"])
                    vtype = attr.get("datatype", "boolean")
                    if vtype.lower() == "eajava_boolean":
                        nusmv_type = "boolean"
                    elif vtype.startswith("EAID_") and vtype in eaid_map:
                        nusmv_type = eaid_map[vtype]["name"]
                    else:
                        nusmv_type = vtype
                    classes[cname]["vars"][vname] = nusmv_type
                for sm in child.get("ownedBehaviors", []):
                    classes[cname]["sms"].append(sm)

            elif ctype == "uml:Signal":
                stereotypes = child.get("stereotypes", [])
                ann_list = child.get("fms_annotations", [])
                ename = sanitize_name(child.get("name", "<unnamed>"))

                if "Automic Event" in stereotypes:
                    # 原子事件
                    for ann in ann_list:
                        props = ann.get("props", {})
                        tvar = sanitize_name(props.get("triggerVariable", ""))
                        changetype = props.get("changetype", "rising")
                        events[ename] = {
                            "type": "atomic",
                            "triggerVariable": tvar,
                            "changetype": changetype
                        }

                elif "Composite Event" in stereotypes:
                    # 复合事件
                    for ann in ann_list:
                        props = ann.get("props", {})
                        subevents = [sanitize_name(s.strip()) for s in props.get("subevent", "").split(",") if s.strip()]
                        operator = props.get("operator", "OR").upper()
                        events[ename] = {
                            "type": "composite",
                            "subevents": subevents,
                            "operator": operator
                        }

                else:
                    # 未定义事件或未标记事件
                    if not ann_list:
                        # 既没有 annotation 也没有 stereotype → undefined
                        events[ename] = {"type": "undefined"}
                    else:
                        # 有 annotation，但不是 atomic/composite → 当作 atomic
                        for ann in ann_list:
                            props = ann.get("props", {})
                            tvar = sanitize_name(props.get("triggerVariable", ""))
                            changetype = props.get("changetype", "rising")
                            events[ename] = {
                                "type": "atomic",
                                "triggerVariable": tvar,
                                "changetype": changetype
                            }

def collect_packages(pkg_list):
    for pkg in pkg_list:
        collect_classes([pkg])
        for child in pkg.get("children", []):
            if child.get("type") == "uml:Package":
                collect_packages([child])

collect_packages(model["packages"])

# ---------- 生成状态机赋值 ----------
def generate_next_assignments(sm_list, class_vars):
    assignments = defaultdict(list)

    def get_state_conditions(state, all_states, visited=None):
        if visited is None:
            visited = set()
        conds = []
        sid = id(state)
        if sid in visited:
            return []
        visited.add(sid)

        # 当前状态 fms_annotations
        for ann in state.get("fms_annotations", []):
            props = ann.get("props", {})
            if "value" in props:
                varname = sanitize_name(
                    props.get("map_to_LV") or props.get("map_to_SV") or
                    props.get("map_to_MV") or props.get("map_to_CV")
                )
                val = props["value"]
                conds.append((varname, val))

        # 处理 composite state 的 substate tag
        for ann in state.get("fms_annotations", []):
            props = ann.get("props", {})
            substate_names = props.get("substate")
            if substate_names:
                for sub_name in [s.strip() for s in substate_names.split(",")]:
                    sub_state = all_states.get(sub_name)
                    if sub_state:
                        conds.extend(get_state_conditions(sub_state, all_states, visited))
        return conds

    for sm in sm_list:
        for reg in sm.get("regions", []):
            states_dict = {st["name"]: st for st in reg.get("states", [])}
            for tr in reg.get("transitions", []):
                source_name = tr["source"]
                target_name = tr["target"]
                source_state = states_dict.get(source_name)
                target_state = states_dict.get(target_name)
                if not target_state:
                    continue

                target_conds_list = get_state_conditions(target_state, states_dict)
                for varname, val in target_conds_list:
                    if varname not in class_vars:
                        continue
                    is_next = not (source_state and source_state["type"] == "uml:Pseudostate" and source_name.lower() == "initial")
                    cond_parts = []

                    if is_next and source_state:
                        source_conds = get_state_conditions(source_state, states_dict)
                        for v, vval in source_conds:
                            cond_parts.append(f"{v} = {vval}")

                    if tr.get("fms_annotations"):
                        gt = tr["fms_annotations"][0]["props"].get("guard_truthTable")
                        if gt:
                            csv_cond = guard_csv_to_nusmv(gt)
                            if csv_cond and csv_cond != "TRUE":
                                cond_parts.append(f"({csv_cond})")

                    if tr.get("guard"):
                        cond_parts.append(f"({tr['guard']})")
                    trigger_names = []
                    for trg in tr.get("triggers", []):
                        trg_name = sanitize_name(trg.get("name", trg.get("signal_name", "")))
                        if trg_name:
                            trigger_names.append(trg_name)
                    if trigger_names:
                        trigger_expr = " | ".join(trigger_names)
                        cond_parts.append(f"({trigger_expr})")
                    cond_str = " & ".join(cond_parts) if cond_parts else "TRUE"
                    if not any(cond_str == c for c, v, n in assignments[varname]):
                        assignments[varname].append((cond_str, val, is_next))
    return assignments

# ---------- 生成 NuSMV 文件 ----------
with open("model.smv", "w", encoding="utf-8") as fh:
    # 类模块
    for cname, cinfo in classes.items():
        fh.write(f"MODULE {sanitize_name(cname)}\n")

        # 写 VAR 段
        if cinfo["vars"]:
            fh.write("VAR\n")
            for vname, vtype in cinfo["vars"].items():
                fh.write(f"  {vname} : {vtype if vtype.lower() != 'boolean' else 'boolean'};\n")

        # 写 ASSIGN 段
        fh.write("ASSIGN\n")

        # ===== 输出 init(var) 赋值 =====
        for pkg in model["packages"]:
            for child in pkg.get("children", []):
                if child.get("type") == "uml:Class" and child.get("name") == cname:
                    for attr in child.get("ownedAttributes", []):
                        vname = sanitize_name(attr["name"])
                        if "defaultValue" in attr and attr["defaultValue"] is not None:
                            defval = attr["defaultValue"]
                            fh.write(f"  init({vname}) := {defval};\n")

        # ===== 保持 next(var) 原逻辑 =====
        next_assignments = generate_next_assignments(cinfo["sms"], cinfo["vars"])
        for vname in cinfo["vars"]:
            if vname not in next_assignments:
                continue
            is_initial = all(not is_next for _, _, is_next in next_assignments[vname])
            if is_initial:
                fh.write(f"  {vname} := case\n")
            else:
                fh.write(f"  next({vname}) := case\n")
            for cond, val, _ in next_assignments[vname]:
                fh.write(f"    {cond} : {val};\n")
            fh.write(f"    TRUE : {vname if not is_initial else 'FALSE'};\n")
            fh.write("  esac;\n\n")

    # ---------- 事件模块生成 ----------
    # ---------- 事件模块生成 ----------
    for ename, edata in events.items():
        etype = edata.get("type", "atomic")
        fh.write(f"MODULE {sanitize_name(ename)}\nVAR\n")
        fh.write("  occ : boolean;\n")  # 所有事件都定义 occ

        fh.write("ASSIGN\n")
        # ✅ 新增：初始化所有事件为 FALSE
        fh.write("  init(occ) := FALSE;\n")

        if etype == "atomic":
            changetype = edata.get("changetype", "rising")
            trigger_var = edata.get("triggerVariable", "")
            if changetype.lower() == "rising" and trigger_var:
                fh.write("  next(occ) := case\n")
                fh.write(f"    {trigger_var} = FALSE & next({trigger_var}) = TRUE : TRUE;\n")
                fh.write("    TRUE : FALSE;\n")
                fh.write("  esac;\n\n")
            elif changetype.lower() == "falling" and trigger_var:
                fh.write("  next(occ) := case\n")
                fh.write(f"    {trigger_var} = TRUE & next({trigger_var}) = FALSE : TRUE;\n")
                fh.write("    TRUE : FALSE;\n")
                fh.write("  esac;\n\n")


        elif etype == "composite":
            subs = edata.get("subevents", [])
            op = edata.get("operator", "OR").upper()
            if op == "OR":
                expr = " | ".join(subs)
            elif op == "AND":
                expr = " & ".join(subs)
            elif op == "XOR":
                assert len(subs) == 2
                expr = f"({subs[0]} & !{subs[1]}) | (!{subs[0]} & {subs[1]})"
            elif op == "MASKING":
                assert len(subs) == 2
                expr = f"{subs[0]} & !{subs[1]}"
            elif op == "NOT":
                assert len(subs) == 1
                expr = f"!{subs[0]}"
            else:
                expr = "FALSE"
            fh.write(f"  occ := {expr};\n\n")



    # ---------- 主模块 ----------
    fh.write("MODULE main\nVAR\n")
    for cname in classes.keys():
        fh.write(f"  {sanitize_name(cname)}_inst : {sanitize_name(cname)}();\n")
    for ename in events.keys():
        fh.write(f"  {sanitize_name(ename)}_inst : {sanitize_name(ename)}();\n")
